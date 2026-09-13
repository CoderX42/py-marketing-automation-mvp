from __future__ import annotations

import re
import subprocess
import time
import html
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Callable

from appium import webdriver
from appium.options.android import UiAutomator2Options
from appium.webdriver.common.appiumby import AppiumBy
from selenium.webdriver.remote.client_config import ClientConfig
from device import AdbError, resolve_adb, subprocess_options


class ManualLoginRequired(RuntimeError):
    pass


class NavigationError(RuntimeError):
    """The app did not reach the page required for the current operation."""


class NetworkUnavailableError(NavigationError):
    """The Android app reports that its network is unavailable."""


# The APK uses one generic network dialog for both transport failures and its
# security/session tunnel failures. Keep the exact phrases here so a genuine
# expired session is not retried as if it were a transient Wi-Fi outage.
NETWORK_ERROR_MARKERS = (
    "当前网络不可用",
    "请检查网络环境",
    "网络不可用，请检查你的网络",
    "服务器请求超时",
    "服务器响应超时",
    "无法连接服务器",
    "服务器连接异常",
    "服务器返回异常",
)
SESSION_ERROR_MARKERS = (
    "当前登录已失效",
    "认证凭证失败",
    "安全隧道已断开",
    "账号在另一台设备登录被强制退出",
    "网关异常请尝试重新登录",
)


class MarketingAutomation:
    def __init__(self, config: dict, artifact_dir: str | Path, log: Callable[[str], None] | None = None):
        self.config = config
        self.artifact_dir = Path(artifact_dir)
        self.log = log or (lambda _: None)
        self.driver = None
        self._context = "NATIVE_APP"
        self.on_detail_progress = None

    def connect(self) -> None:
        options = UiAutomator2Options()
        options.platform_name = "Android"
        options.automation_name = "UiAutomator2"
        options.device_name = self.config.get("device_name", "Android")
        options.app_package = self.config.get("app_package", "com.sh.cm.grid4a")
        options.app_activity = self.config.get("app_activity", "com.sh.cm.grid4a.SplashActivity")
        options.no_reset = True
        # Attach to the page the user already opened. Launching SplashActivity
        # for every desktop run can route an otherwise valid session to login.
        options.set_capability("appium:autoLaunch", False)
        options.set_capability("appium:dontStopAppOnReset", True)
        # A configured batch pause can exceed two minutes. Keep the Appium
        # session alive so a deliberate throttle pause is not mistaken for a
        # phone/network failure on the following record.
        pause_seconds = float(self.config.get("timing", {}).get("pause_minutes", 2)) * 60
        options.new_command_timeout = max(600, int(pause_seconds + 120))
        # 部分小米/国产 ROM 拒绝 settings delete global hidden_api_policy。
        # 该选项让 UiAutomator2 忽略这一步，不影响普通自动化能力。
        options.set_capability("appium:ignoreHiddenApiPolicyError", True)
        # 部分 ROM 不允许 adb install -g 给 Appium Settings 辅助包自动授予权限。
        # 跳过设备初始化即可继续安装并启动 UiAutomator2 服务。
        options.set_capability("appium:skipDeviceInitialization", True)
        options.set_capability("appium:settings[waitForIdleTimeout]", 0)
        options.set_capability("appium:settings[waitForSelectorTimeout]", 0)
        options.set_capability("appium:uiautomator2ServerReadTimeout", 15000)
        appium_url = self.config.get("appium_url", "http://127.0.0.1:4723/wd/hub")
        # A WebView activity transition can leave one Appium command waiting
        # forever on some Xiaomi builds.  Bound the HTTP call so the worker can
        # report a failure and stop instead of appearing frozen.
        client_config = ClientConfig(remote_server_addr=appium_url, timeout=30,
                                     init_args_for_pool_manager={"init_args_for_pool_manager": {"retries": 0}})
        self.driver = webdriver.Remote(appium_url, options=options, client_config=client_config)
        self.driver.update_settings({"waitForIdleTimeout": 0, "waitForSelectorTimeout": 0})
        self.driver.implicitly_wait(0)
        self.driver.command_executor._client_config.timeout = 20
        self._context = "NATIVE_APP"

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            self.driver = None
            self._context = "NATIVE_APP"

    def _text(self, key: str) -> str:
        return self.config.get("selectors", {}).get(key, key)

    def _find_text(self, text: str, timeout: int = 10, exact: bool = False):
        if not self.driver:
            raise RuntimeError("Appium 未连接")
        end = time.time() + timeout
        while time.time() < end:
            for by, value in (
                (AppiumBy.ANDROID_UIAUTOMATOR, f'new UiSelector().{"text" if exact else "textContains"}("{text}")'),
                (AppiumBy.XPATH, f'//*[@text="{text}"]' if exact else f'//*[contains(@text,"{text}")]'),
            ):
                try:
                    elements = self.driver.find_elements(by, value)
                    if elements:
                        return elements[0]
                except Exception:
                    pass
            time.sleep(0.5)
        return None

    def _switch_native(self) -> None:
        """Return to the native context before looking for Android widgets."""
        if not self.driver:
            return
        try:
            if self._context != "NATIVE_APP":
                self.driver.switch_to.context("NATIVE_APP")
                self._context = "NATIVE_APP"
        except Exception:
            pass

    def _click_element(self, element) -> None:
        """Click a widget, using its clickable ancestor and a coordinate fallback."""
        if not self.driver:
            raise RuntimeError("Appium 未连接")
        target = element
        try:
            clickable = str(element.get_attribute("clickable")).lower() == "true"
        except Exception:
            clickable = False
        if not clickable:
            try:
                ancestor = element.find_element(AppiumBy.XPATH, "ancestor::*[@clickable='true'][1]")
                if ancestor:
                    target = ancestor
            except Exception:
                pass
            # The marketing card is a clickable container whose label child is
            # reported as non-clickable.  Send the tap gesture directly to the
            # container first; WebElement.click() can block while this H5 page
            # is transitioning activities.
            try:
                self.driver.execute_script("mobile: clickGesture", {"elementId": target.id})
                return
            except Exception:
                pass
        try:
            target.click()
            return
        except Exception as first_error:
            # UiAutomator sometimes reports a text node as found but does not
            # dispatch its native click.  Appium's clickGesture sends a real
            # tap to the same element/coordinates in that case.
            try:
                self.driver.execute_script("mobile: clickGesture", {"elementId": target.id})
                return
            except Exception:
                try:
                    rect = target.rect
                    self.driver.execute_script(
                        "mobile: clickGesture",
                        {"x": int(rect["x"] + rect["width"] / 2), "y": int(rect["y"] + rect["height"] / 2)},
                    )
                    return
                except Exception as second_error:
                    raise NavigationError(f"点击控件失败：{first_error}; {second_error}") from second_error

    def _require_manual_login_if_needed(self) -> None:
        login_marker = self._text("login_marker")
        home_marker = self._text("home_marker")
        marketing_tab = self._text("smart_marketing_tab")
        # A previous run may have left the app on the input/detail page.  Those
        # pages are also proof that login succeeded and should not trigger a
        # false "未检测到登录成功页面" error.
        logged_in_markers = (
            home_marker,
            marketing_tab,
            self._text("marketing_entry"),
            self._text("phone_input_hint"),
            self._text("detail_marker"),
        )
        # The home and detail activities are only reachable after the user has
        # completed login.  Checking the activity first also avoids a slow
        # page_source call while the home WebView is still settling.
        try:
            activity = self.driver.current_activity or ""
            package = self.driver.current_package or ""
            expected_package = self.config.get("app_package", "com.sh.cm.grid4a")
            if package and package != expected_package:
                raise NavigationError(f"当前前台应用不是目标应用（{package}），请先打开目标应用后重试")
            if "login" in activity.lower():
                raise ManualLoginRequired("请在手机上完成登录后重新开始任务")
            if package == self.config.get("app_package", "com.sh.cm.grid4a") and any(
                name in activity for name in ("MainActivity", "MiniHtmlActivity", "VerifyStep1Activity")
            ):
                return
        except (ManualLoginRequired, NavigationError):
            raise
        except Exception:
            pass
        # Read one source snapshot instead of polling every marker.  During a
        # WebView activity transition repeated find_elements calls can block
        # long enough to make the desktop UI look frozen.
        try:
            source = self.driver.page_source
            if login_marker and login_marker in source:
                raise ManualLoginRequired("请在手机上完成账号、密码和短信验证码登录，然后点击继续")
            if any(marker and marker in source for marker in logged_in_markers):
                return
        except ManualLoginRequired:
            raise
        except NavigationError:
            raise
        except Exception:
            pass
        if self._try_webview():
            try:
                source = self.driver.page_source
                if login_marker and login_marker in source:
                    raise ManualLoginRequired("请在手机上完成账号、密码和短信验证码登录，然后点击继续")
                if any(marker and marker in source for marker in logged_in_markers):
                    return
            except ManualLoginRequired:
                raise
            except NavigationError:
                raise
            except Exception:
                pass
        raise TimeoutError("未检测到登录成功页面")

    def _click_text(self, key: str, timeout: int = 15) -> None:
        element = self._find_text(self._text(key), timeout)
        if not element and self._try_webview():
            element = self._find_text(self._text(key), timeout)
        if not element:
            raise TimeoutError(f"找不到控件：{self._text(key)}")
        self._click_element(element)

    def _click_if_present(self, key: str, timeout: int = 5) -> bool:
        text = self._text(key)
        if not text:
            return False
        element = self._find_text(text, timeout)
        if not element and self._try_webview():
            element = self._find_text(text, timeout)
        if element:
            self._click_element(element)
            return True
        return False

    def _click_smart_marketing_tab(self) -> None:
        """Click the bottom tab by its container id, then fall back to its label."""
        self._switch_native()
        tab_id = "com.sh.cm.grid4a:id/menu_nav_item_2"
        try:
            elements = self.driver.find_elements(AppiumBy.ID, tab_id)
            if elements:
                element = elements[0]
                rect = element.rect
                self.driver.execute_script(
                    "mobile: clickGesture",
                    {"x": int(rect["x"] + rect["width"] / 2), "y": int(rect["y"] + rect["height"] / 2)},
                )
                self.log("已点击底部“智慧营销”容器")
                return
        except Exception as exc:
            self.log(f"智慧营销容器点击失败，改用文字定位：{exc}")
        self._click_text("smart_marketing_tab", timeout=8)
        self.log("已点击“智慧营销”文字区域")

    def _try_webview(self) -> bool:
        """Switch to the first available WebView context used by the remote H5 pages."""
        if not self.driver:
            return False
        try:
            for context in self.driver.contexts:
                if "WEBVIEW" in context.upper():
                    self.driver.switch_to.context(context)
                    self._context = context
                    self.log(f"已切换到 {context} 页面上下文")
                    return True
        except Exception as exc:
            self.log(f"WebView 上下文不可用，将继续尝试原生控件：{exc}")
        return False

    def _phone_input(self):
        self._switch_native()
        hint = self._text("phone_input_hint")
        # The home screen also contains an EditText (the global search box).
        # Never treat an arbitrary EditText as the phone field; first require
        # the labels that only exist on the marketing assistant page.
        try:
            hint_nodes = self.driver.find_elements(AppiumBy.XPATH, f'//*[contains(@text,"{hint}")]')
            jump_nodes = self.driver.find_elements(AppiumBy.XPATH, f'//*[contains(@text,"{self._text("jump_button")}")]')
            if not hint_nodes or not jump_nodes:
                raise TimeoutError("当前页面不是手机号输入页")
        except TimeoutError:
            raise
        except Exception as exc:
            raise TimeoutError("无法确认手机号输入页") from exc
        for by, value in (
            (AppiumBy.XPATH, f'//*[contains(@text,"{hint}")]/following::*[@class="android.widget.EditText"][1]'),
            (AppiumBy.CLASS_NAME, "android.widget.EditText"),
        ):
            try:
                elements = self.driver.find_elements(by, value)
                if elements:
                    return elements[-1]
            except Exception:
                pass
        if self._try_webview():
            for by, value in (
                (AppiumBy.CSS_SELECTOR, 'input[placeholder*="手机"]'),
                (AppiumBy.CSS_SELECTOR, 'input[type="tel"]'),
                (AppiumBy.CSS_SELECTOR, "input"),
            ):
                try:
                    elements = self.driver.find_elements(by, value)
                    if elements:
                        return elements[-1]
                except Exception:
                    pass
        raise TimeoutError("找不到手机号输入框")

    def _wait_phone_input(self, timeout: int = 10):
        end = time.time() + timeout
        last_error = None
        self._raise_if_network_error()
        while time.time() < end:
            try:
                return self._phone_input()
            except Exception as exc:
                last_error = exc
                time.sleep(0.4)
        raise TimeoutError("手机号输入页未加载完成") from last_error

    def _back_to_entry_surface(self) -> None:
        """Leave a previous detail/input page so the next number starts at the entry."""
        if not self.driver:
            return
        self._switch_native()
        for _ in range(3):
            try:
                activity = str(self.driver.current_activity or "")
            except Exception:
                return
            # MainActivity contains the home/smart-marketing cards. The input
            # page on this build is VerifyStep1Activity and the detail page is
            # a MiniHtml/WebActivity; all of them need a back navigation.
            if "MainActivity" in activity or "Login" in activity or "login" in activity.lower():
                return
            if not any(name in activity for name in ("MiniHtmlActivity", "VerifyStep1Activity", "WebActivity")):
                raise NavigationError(f"当前页面无法自动返回查询入口：{activity}")
            self.driver.back()
            time.sleep(0.8)

    def _wait_ui(self, predicate, timeout: float = 15):
        deadline = time.monotonic() + timeout
        while True:
            root = self._adb_ui_root()
            if root is not None and predicate(root):
                return root
            if root is not None:
                self._check_session_message(self._root_text(root), check_login=False)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def _has_label(self, root, label: str) -> bool:
        return any(self._normal_text(t) == self._normal_text(label) for t in self._root_text(root))

    def _tap_common_free_entry(self) -> None:
        self.log('智慧营销入口未加载，改用“首页 → 常用 → 营销助手(免签入)”')
        if not self._adb_tap_target(resource_id="com.sh.cm.grid4a:id/menu_nav_item_1"):
            raise NavigationError("未找到底部首页，无法进入常用入口")
        root = self._wait_ui(lambda r: self._has_label(r, "常用"), timeout=15)
        if root is None:
            raise NavigationError("首页常用区域未加载")
        # Select the actual common tab when another category was left open.
        if not any(n.get("resource-id") == "com.sh.cm.grid4a:id/common_use_item_tv" and
                   self._normal_text(n.get("text", "")) == self._normal_text(self._text("marketing_entry"))
                   for n in root.iter()):
            self._adb_tap_target(text="常用")
            self._wait_ui(lambda r: any(n.get("resource-id") == "com.sh.cm.grid4a:id/common_use_item_tv"
                                      for n in r.iter()), timeout=10)
        if not self._adb_tap_target(text=self._text("marketing_entry"),
                                    resource_id="com.sh.cm.grid4a:id/common_use_item_tv"):
            raise NavigationError("首页常用中未找到“营销助手(免签入)”；请将该功能加入常用")
        self.log('已点击常用“营销助手(免签入)”，正在验证输入页')

    def _tap_home_marketing_flow(self) -> None:
        self.log('正在进入“智慧营销”')
        if not self._adb_tap_target(resource_id="com.sh.cm.grid4a:id/menu_nav_item_2"):
            raise NavigationError("未找到底部智慧营销页签，请将手机停留在应用首页")
        root = self._wait_ui(lambda r: self._has_label(r, self._text("home_marker")) or
                            self._has_label(r, "暂无数据"), timeout=15)
        if root is None or self._has_label(root, "暂无数据"):
            self._tap_common_free_entry()
            return
        if not self._adb_tap_target(text=self._text("home_marker"), resource_id="com.sh.cm.grid4a:id/home_business_tab_tv"):
            raise NavigationError("未找到个人业务页签")
        self.log('已点击“个人业务”，等待全景视图')
        if self._wait_ui(lambda r: self._has_label(r, self._text("full_view_marker")), timeout=10) is None:
            self._tap_common_free_entry()
            return
        if not self._adb_tap_target(text=self._text("full_view_marker"), resource_id="com.sh.cm.grid4a:id/home_business_floor_title_tv"):
            raise NavigationError("未找到全景视图导航项")
        self.log('已点击“全景视图”，等待免签入入口')
        root = self._wait_ui(lambda r: self._has_label(r, self._text("marketing_entry")) or
                            self._has_label(r, "暂无数据"), timeout=10)
        if root is None or self._has_label(root, "暂无数据"):
            self._tap_common_free_entry()
            return
        if not self._adb_tap_target(text=self._text("marketing_entry")):
            raise NavigationError("未找到“营销助手(免签入)”入口")
        self.log('已点击“营销助手(免签入)”，正在验证输入页')

    def _ensure_entry(self) -> None:
        self._switch_native()
        self._back_to_entry_surface()
        self._tap_home_marketing_flow()
        self._wait_phone_input(timeout=30)
        self._verify_free_entry_title()
        self.log("已确认免签入手机号输入页")

    def _verify_free_entry_title(self) -> None:
        root = self._adb_ui_root()
        if root is None or not self._has_label(root, self._text("marketing_entry")):
            raise NavigationError("当前输入页标题不是“营销助手(免签入)”，已停止输入")

    @staticmethod
    def _root_text(root) -> list[str]:
        texts = []
        for node in root.iter():
            text = (node.get("text") or node.get("content-desc") or "").strip()
            if text:
                texts.append(text)
        return texts

    def _check_session_message(self, texts: list[str], *, check_login: bool = True) -> None:
        joined = "\n".join(texts)
        configured_network = self._text("network_error")
        network_markers = (*NETWORK_ERROR_MARKERS,
                           configured_network if configured_network != "network_error" else "")
        if any(marker and marker in joined for marker in network_markers):
            raise NetworkUnavailableError("手机应用提示网络不可用，请在手机恢复网络或重新登录后继续")
        if any(marker in joined for marker in SESSION_ERROR_MARKERS):
            raise ManualLoginRequired("手机应用登录凭证或安全隧道已失效，请在手机重新登录后继续")
        if check_login and self._text("login_marker") in texts:
            raise ManualLoginRequired("查询过程中出现登录页面，请在手机重新登录后继续")

    def _raise_if_network_error(self) -> None:
        self._switch_native()
        adb_text, _ = self._adb_ui_snapshot()
        self._check_session_message(adb_text, check_login=False)

    def _visible_text(self) -> list[str]:
        source = self.driver.page_source
        try:
            root = ET.fromstring(source)
            # Some WebView bridges expose labels through content-desc instead
            # of text. Keep document order and avoid duplicating a node when
            # both attributes contain the same value.
            native: list[str] = []
            for node in root.iter():
                text = (node.attrib.get("text") or "").strip()
                description = (node.attrib.get("content-desc") or "").strip()
                if text:
                    native.append(text)
                elif description:
                    native.append(description)
            if native:
                return native
        except ET.ParseError:
            pass
        plain = html.unescape(re.sub(r"<[^>]+>", " ", source))
        return [line.strip() for line in re.split(r"\s+", plain) if line.strip()]

    def _swipe_detail(self, direction: str) -> None:
        """Slow overlapping drags: fast flings can skip whole recommendation cards."""
        root = self._adb_ui_root()
        size = self.driver.get_window_size()
        width, height = int(size["width"]), int(size["height"])
        bounds = [self._node_bounds(n) for n in root.iter()
                  if n.get("class") == "android.webkit.WebView"]
        bounds = [b for b in bounds if b]
        left, top, right, bottom = bounds[0] if bounds else (0, height // 7, width, height * 9 // 10)
        top, bottom = max(top, height // 8), min(bottom, height * 9 // 10)
        x = int(left + (right - left) * .45)
        high = int(top + (bottom - top) * .72)
        low = int(top + (bottom - top) * .32)
        start, end = (high, low) if direction == "up" else (low, high)
        adb = self._adb_path()
        if adb:
            proc = subprocess.run([adb, "shell", "input", "swipe", str(x), str(start), str(x), str(end), "1200"],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=5, **subprocess_options())
            if proc.returncode or "Exception" in proc.stderr or "Error" in proc.stderr:
                raise NavigationError("手机拒绝滑动操作，请检查 USB 调试（安全设置）")
        else:
            self.driver.execute_script("mobile: dragGesture", {
                "startX": x, "startY": start, "endX": x, "endY": end, "speed": 600})

    @staticmethod
    def _merge_detail_lines(collected: list[str], incoming: list[str]) -> int:
        """Append a newly visible viewport while retaining repeated business text."""
        lines = [line.strip() for line in incoming if line and line.strip()]
        if not lines:
            return 0
        if not collected:
            collected.extend(lines)
            return len(lines)
        if tuple(lines) == tuple(collected[-len(lines):]):
            return 0
        # Adjacent swipes normally overlap. Merge only that overlap; do not use
        # a global set, because two recommendations can intentionally share the
        # same product name or label and both occurrences must be exported.
        max_overlap = min(120, len(collected), len(lines))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if collected[-size:] == lines[:size]:
                overlap = size
                break
        if overlap:
            lines = lines[overlap:]
        elif len(lines) <= len(collected):
            # A WebView may expose the complete DOM on every scroll. Skip an
            # unchanged/subset snapshot, but retain any genuinely new viewport.
            for offset in range(len(collected) - len(lines) + 1):
                if collected[offset:offset + len(lines)] == lines:
                    return 0
        collected.extend(lines)
        return len(lines)

    @staticmethod
    def _clean_detail_label(text: str) -> str:
        # Private-use glyphs are icon fonts (arrows/envelopes), not page text.
        return re.sub(r"[\ue000-\uf8ff]", "", text).strip()

    def _detail_snapshot(self):
        root = self._adb_ui_root()
        all_text = self._root_text(root)
        self._check_session_message(all_text)
        if not self._has_label(root, self._text("detail_marker")):
            raise NavigationError("采集时已离开营销详情页")
        webviews = [n for n in root.iter() if n.get("class") == "android.webkit.WebView"]
        area = webviews[0] if webviews else root
        lines, positions, previous = [], [], None
        for node in area.iter():
            text = self._clean_detail_label(node.get("text") or node.get("content-desc") or "")
            if not text or text in {"关闭", "返回", self._text("detail_marker")}:
                continue
            # A WebView label and its child may describe the same visual node.
            identity = (text, node.get("bounds"))
            if identity == previous:
                continue
            previous = identity
            lines.append(text)
            positions.append(identity)
        return lines, tuple(positions)

    def _has_detail_body(self, lines):
        headings = {"用户推荐方案", "推荐业务", "属地推荐方案", "营销详情", "关闭", "返回", "加载中", "加载中...", "正在加载"}
        return any(self._clean_detail_label(line) not in headings and
                   len(self._clean_detail_label(line)) >= 2 for line in lines)

    def _detail_result(self, *, complete=False, error="", session_interrupted=False,
                       interruption_type=""):
        lines = self._collected_detail
        raw = "\n".join([self._text("detail_marker"), *lines])
        return {"phone": self._current_phone, "status": "success" if complete else "partial",
                "raw_text": raw, "items": self._parse_items(lines, raw),
                "query_time": self._query_time, "collection_complete": complete,
                "error": error, "session_interrupted": session_interrupted,
                "interruption_type": interruption_type}

    def _record_detail(self, lines):
        clean = [self._clean_detail_label(t) for t in lines]
        clean = [t for t in clean if t and t not in {"关闭", "返回", self._text("detail_marker")}]
        clean = [t for i, t in enumerate(clean) if not (
            i and t == clean[i - 1] and t in {"用户推荐方案", "推荐业务", "属地推荐方案"})]
        # Replace the loading skeleton once actual recommendations arrive.
        if not self._has_detail_body(self._collected_detail) and self._has_detail_body(clean):
            self._collected_detail = []
        before = tuple(self._collected_detail)
        self._merge_detail_lines(self._collected_detail, clean)
        if not self._detail_checkpointed or tuple(self._collected_detail) != before:
            callback = getattr(self, "on_detail_progress", None)
            if callback:
                callback(self._detail_result(error="正在采集，尚未确认完整"))
            self._detail_checkpointed = True

    def _collect_detail_text(self, max_scrolls: int = 80) -> list[str]:
        lines, fingerprint = self._detail_snapshot()
        self._record_detail(lines)
        unchanged = 0
        no_new_text = 0
        for step in range(max_scrolls):
            self._swipe_detail("up")
            time.sleep(.8)
            after, after_fingerprint = self._detail_snapshot()
            previous_text = tuple(self._collected_detail)
            self._record_detail(after)
            no_new_text = no_new_text + 1 if tuple(self._collected_detail) == previous_text else 0
            # The H5 footer has a moving watermark and tiny layout shifts.
            # Compare actual text too; pixel-identical bounds never settle on
            # that page even after it has reached the bottom.
            unchanged = unchanged + 1 if after_fingerprint == fingerprint or after == lines else 0
            fingerprint = after_fingerprint
            lines = after
            self.log(f"向下采集第 {step + 1} 次，已读取 {sum(map(len, self._collected_detail))} 字符")
            if unchanged >= 2 or no_new_text >= 2:
                # A stable viewport alone can be a blocked swipe. Require the
                # actual final section too; the safety ceiling is never success.
                if "属地推荐方案" in after and self._has_detail_body(self._collected_detail):
                    self._collection_complete = True
                    self.log("已确认营销详情底部，全文采集完成")
                    return self._collected_detail
                if unchanged >= 3:
                    raise NavigationError("页面未继续向下移动，且未确认详情底部；已保留当前内容")
        raise NavigationError(f"已达到 {max_scrolls} 次滚动上限，尚未确认详情底部；已保留当前内容")

    @staticmethod
    def _business_dialog_error(root) -> str | None:
        messages = []
        labels = MarketingAutomation._root_text(root)
        for node in root.iter():
            if node.get("resource-id") in {"android:id/message", "android:id/alertTitle"}:
                if node.get("text"):
                    messages.append(node.get("text"))
        keywords = ("失败", "错误", "异常", "不存在", "无效", "不支持", "非上海移动", "重新输入", "未查询到")
        if messages and any(key in "\n".join(messages) for key in keywords):
            return "；".join(messages)
        # This application's custom H5 validation popup has no android:id/message.
        if "确定" in labels and any("非上海移动" in t and "重新输入" in "\n".join(labels) for t in labels):
            return next(t for t in labels if "非上海移动" in t)
        return None

    def _wait_detail_or_error(self, timeout: int) -> tuple[list[str], str | None]:
        deadline = time.monotonic() + timeout
        last_text = []
        while time.monotonic() < deadline:
            root = self._adb_ui_root()
            if root is None:
                time.sleep(.5)
                continue
            last_text = self._root_text(root)
            self._check_session_message(last_text)
            business_error = self._business_dialog_error(root)
            if business_error:
                return last_text, business_error
            if self._has_label(root, self._text("detail_marker")):
                return last_text, None
            time.sleep(.5)
        return last_text, None

    def _adb_path(self) -> str | None:
        try:
            return resolve_adb(self.config.get("adb_path") or "adb")
        except AdbError:
            return None

    def _adb_tap_xy(self, x: int, y: int) -> bool:
        adb = self._adb_path()
        if not adb:
            return False
        try:
            result = subprocess.run([adb, "shell", "input", "tap", str(x), str(y)], check=False, capture_output=True, timeout=3,
                                    **subprocess_options())
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _normal_text(text: str) -> str:
        return re.sub(r"\s+", "", text).replace("（", "(").replace("）", ")")

    @staticmethod
    def _node_bounds(node) -> tuple[int, int, int, int] | None:
        match = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", node.attrib.get("bounds", ""))
        if not match:
            return None
        bounds = tuple(int(value) for value in match.groups())
        left, top, right, bottom = bounds
        return bounds if left >= 0 and top >= 0 and right > left and bottom > top else None

    def _adb_ui_root(self):
        """Read a new hierarchy only after a successful dump; never reuse stale XML."""
        if self.driver is not None:
            # `uiautomator dump` starts another accessibility client and can
            # terminate/conflict with Appium's UiAutomator2 instrumentation.
            # Use the existing session for hierarchies and ADB only for taps.
            self._switch_native()
            return ET.fromstring(self.driver.page_source)
        adb = self._adb_path()
        if not adb:
            return None
        remote = f"/sdcard/marketing-window-{uuid.uuid4().hex}.xml"
        try:
            dumped = subprocess.run([adb, "shell", "uiautomator", "dump", remote], check=False, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=5, **subprocess_options())
            if dumped.returncode != 0:
                return None
            loaded = subprocess.run([adb, "exec-out", "cat", remote], check=False, capture_output=True, text=True,
                                    encoding="utf-8", timeout=3, **subprocess_options())
            if loaded.returncode != 0:
                return None
            return ET.fromstring(loaded.stdout)
        except Exception:
            return None
        finally:
            try:
                subprocess.run([adb, "shell", "rm", "-f", remote], check=False, capture_output=True, timeout=3,
                               **subprocess_options())
            except Exception:
                pass

    def _adb_tap_target(self, *, text: str | None = None, resource_id: str | None = None) -> bool:
        if not text and not resource_id:
            return False
        root = self._adb_ui_root()
        if root is None:
            return False
        parents = {child: parent for parent in root.iter() for child in parent}
        for node in root.iter():
            label = node.attrib.get("text") or node.attrib.get("content-desc") or ""
            if text and self._normal_text(label) != self._normal_text(text):
                continue
            if resource_id and node.attrib.get("resource-id") != resource_id:
                continue
            if node.attrib.get("enabled") == "false" or not self._node_bounds(node):
                continue
            target = node
            ancestor = node
            while ancestor is not None:
                if ancestor.attrib.get("clickable") == "true" and self._node_bounds(ancestor):
                    target = ancestor
                    break
                ancestor = parents.get(ancestor)
            left, top, right, bottom = self._node_bounds(target)
            return self._adb_tap_xy((left + right) // 2, (top + bottom) // 2)
        return False

    def _adb_ui_snapshot(self) -> tuple[list[str], tuple[int, int, int, int] | None]:
        root = self._adb_ui_root()
        if root is None:
            return [], None
        texts: list[str] = []
        confirm_bounds = None
        for node in root.iter():
            text = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
            if text:
                texts.append(text)
            if self._normal_text(text) == self._normal_text(self._text("confirm_button")):
                confirm_bounds = self._node_bounds(node)
        return texts, confirm_bounds

    def _adb_tap_text(self, text: str) -> bool:
        return self._adb_tap_target(text=text)

    def _dismiss_network_popup(self) -> None:
        """Dismiss only the known network dialog before retrying the same phone."""
        try:
            texts, confirm_bounds = self._adb_ui_snapshot()
            joined = "\n".join(texts)
            configured_network = self._text("network_error")
            network_markers = (*NETWORK_ERROR_MARKERS,
                               configured_network if configured_network != "network_error" else "")
            if not any(marker and marker in joined for marker in network_markers):
                return
            dismissed = False
            if confirm_bounds:
                left, top, right, bottom = confirm_bounds
                dismissed = self._adb_tap_xy((left + right) // 2, (top + bottom) // 2)
            if not dismissed:
                dismissed = self._click_if_present("confirm_button", timeout=2)
            if dismissed:
                self.log("已关闭网络异常提示，准备重试当前号码")
        except Exception as exc:
            self.log(f"关闭网络异常提示失败，将直接重试：{str(exc).split('Stacktrace:')[0]}")

    def query(self, phone: str) -> dict:
        """Retry a transient app network dialog, without skipping the current phone."""
        timing = self.config.get("timing", {})
        retries = max(0, min(int(timing.get("network_retries", 0)), 5))
        base_delay = max(1.0, min(float(timing.get("network_retry_seconds", 15)), 60.0))
        best_partial = None
        last_error = None
        for attempt in range(retries + 1):
            try:
                result = self._query_once(phone)
                if result.get("interruption_type") != "network":
                    return result
                last_error = NetworkUnavailableError(result.get("error") or "手机应用提示网络不可用")
                if result.get("raw_text") and (best_partial is None or
                                                len(result["raw_text"]) > len(best_partial.get("raw_text", ""))):
                    best_partial = result
            except NetworkUnavailableError as exc:
                last_error = exc
            if attempt >= retries:
                if best_partial is not None:
                    return best_partial
                raise last_error
            delay = min(base_delay * (2 ** attempt), 60.0)
            self.log(f"检测到应用网络异常，{delay:g} 秒后自动重试当前号码（{attempt + 1}/{retries}）")
            self._dismiss_network_popup()
            # BatchWorker injects its interruptible wait here so the desktop
            # pause/stop controls remain responsive during a retry backoff.
            # Direct callers and existing tests keep the original time.sleep
            # behavior when no callback is configured.
            retry_wait = timing.get("retry_wait")
            if callable(retry_wait):
                retry_wait(delay)
            else:
                time.sleep(delay)
        raise last_error or NetworkUnavailableError("手机应用提示网络不可用")

    def _query_once(self, phone: str) -> dict:
        if not self.driver:
            raise RuntimeError("Appium 未连接")
        self._current_phone = phone
        self._query_time = datetime.now().isoformat(sep=" ", timespec="seconds")
        self._collected_detail = []
        self._collection_complete = False
        self._detail_checkpointed = False
        if self.config.get("check_login_state", False):
            self._require_manual_login_if_needed()
        self._ensure_entry()
        field = self._wait_phone_input(timeout=10)
        field.clear()
        field.send_keys(phone)
        actual = str(field.get_attribute("text") or "")
        if re.sub(r"\D", "", actual) != phone:
            raise NavigationError("输入框号码与当前 Excel 号码不一致，已停止跳转")
        self.log(f"已核对输入号码后四位 {phone[-4:]}，点击跳转")
        self._click_text("jump_button", timeout=10)
        detail_timeout = int(self.config.get("timing", {}).get("page_timeout_seconds", 30))
        initial, business_error = self._wait_detail_or_error(detail_timeout)
        if business_error:
            self._click_if_present("confirm_button", timeout=2)
            return {"phone": phone, "status": "empty", "items": [], "raw_text": "\n".join(initial),
                    "error": business_error, "query_time": self._query_time}
        if not any(self._normal_text(t) == self._normal_text(self._text("detail_marker")) for t in initial):
            raise TimeoutError("营销详情加载超时")
        # Checkpoint immediately upon observing details, before another lookup,
        # swipe or wait can fail. Never discard these lines on a later error.
        self._record_detail(initial)
        self.log("已进入营销详情页，等待推荐正文加载")
        try:
            deadline = time.monotonic() + detail_timeout
            while True:
                lines, _ = self._detail_snapshot()
                self._record_detail(lines)
                if self._has_detail_body(lines):
                    # Loading H5 initially displays section headings and even
                    # the footer. Give lazy cards time to populate before scroll.
                    time.sleep(1)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("营销详情仅加载了标题，尚未获得正文")
                time.sleep(.7)
            max_scrolls = max(10, min(int(self.config.get("timing", {}).get("detail_max_scrolls", 80)), 200))
            self._collect_detail_text(max_scrolls)
            return self._detail_result(complete=self._collection_complete)
        except Exception as exc:
            if getattr(exc, "is_persistence_error", False):
                raise
            interruption_type = ("network" if isinstance(exc, NetworkUnavailableError)
                                 else "login" if isinstance(exc, ManualLoginRequired) else "")
            return self._detail_result(error=str(exc).split("Stacktrace:")[0],
                                       session_interrupted=bool(interruption_type),
                                       interruption_type=interruption_type)

    @staticmethod
    def _parse_items(texts: list[str], raw: str) -> list[dict]:
        """Group card labels; the complete source always remains in raw_text."""
        items = []
        current = []
        headings = {"用户推荐方案", "推荐业务", "属地推荐方案", "营销详情", "关闭", "返回"}
        for text in texts:
            if text in headings:
                continue
            is_field = re.match(r"^(提示|口径|产品描述)\s*[:：]", text)
            if (not is_field or re.match(r"^提示\s*[:：]", text)) and current and any(re.match(r"^产品描述\s*[:：]", t) for t in current):
                items.append(current)
                current = []
            current.append(text)
        if current:
            items.append(current)
        output = []
        for group in items:
            names = [t for t in group if not re.match(r"^(提示|口径|产品描述)\s*[:：]", t)]
            output.append({"name": "\n".join(names) if names else "（页面未显示业务名称）", "price": "",
                           "description": "\n".join(t for t in group if t.startswith("产品描述")),
                           "raw_text": "\n".join(group)})
        return output
