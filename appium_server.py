from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from device import AdbError, resolve_adb, subprocess_options


class AppiumServerError(RuntimeError):
    pass


def configure_android_sdk() -> str | None:
    roots = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value).expanduser())
    roots.extend([Path.home() / "Library/Android/sdk", Path.home() / "Android/Sdk"])
    if os.environ.get("LOCALAPPDATA"):
        roots.append(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk")
    roots.append(Path.home() / "AppData/Local/Android/Sdk")
    try:
        adb_directory = Path(resolve_adb()).resolve().parent
        if adb_directory.name.lower() == "platform-tools":
            roots.append(adb_directory.parent)
    except AdbError:
        pass
    roots.extend(sorted(Path("/opt/homebrew/Caskroom/android-platform-tools").glob("*/"), reverse=True))
    roots.extend(sorted(Path("/usr/local/Caskroom/android-platform-tools").glob("*/"), reverse=True))
    for root in roots:
        executable = "adb.exe" if sys.platform == "win32" else "adb"
        if (root / "platform-tools" / executable).is_file():
            os.environ["ANDROID_HOME"] = str(root)
            os.environ["ANDROID_SDK_ROOT"] = str(root)
            platform_tools = str(root / "platform-tools")
            paths = os.environ.get("PATH", "").split(os.pathsep)
            if platform_tools not in paths:
                os.environ["PATH"] = os.pathsep.join([platform_tools, *paths])
            return str(root)
    return None


def resolve_appium() -> str | None:
    if sys.platform == "win32" and os.environ.get("NPM_CONFIG_PREFIX"):
        configured = Path(os.environ["NPM_CONFIG_PREFIX"]) / "appium.cmd"
        if configured.is_file():
            return str(configured)
    command = shutil.which("appium.cmd" if sys.platform == "win32" else "appium")
    if command:
        return command
    node_root = Path.home() / ".nvm/versions/node"
    candidates = []
    if sys.platform == "win32":
        for prefix in (str(Path(os.environ["LOCALAPPDATA"]) / "MarketingAutomation/runtime/npm") if os.environ.get("LOCALAPPDATA") else None,
                       str(Path(os.environ["APPDATA"]) / "npm") if os.environ.get("APPDATA") else None):
            if prefix:
                candidates.append(Path(prefix) / "appium.cmd")
    if node_root.is_dir():
        candidates.extend(version / "bin/appium" for version in node_root.iterdir() if version.is_dir())
    candidates.extend([Path("/opt/homebrew/bin/appium"), Path("/usr/local/bin/appium")])
    for candidate in candidates if sys.platform == "win32" else sorted(candidates, reverse=True):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def appium_launch_command(command: str) -> list[str]:
    """Run npm's JavaScript entry directly; .cmd shims need a Windows shell."""
    if sys.platform != "win32" or Path(command).suffix.lower() not in {".cmd", ".bat"}:
        return [command]
    prefix = Path(command).parent
    entry = prefix / "node_modules/appium/index.js"
    node = shutil.which("node.exe")
    if not node and (prefix / "node.exe").is_file():
        node = str(prefix / "node.exe")
    if not node or not entry.is_file():
        raise AppiumServerError("Appium 或 Node.js 安装不完整，请重新运行 run_windows.bat 自动修复环境。")
    return [node, str(entry)]


class AppiumServer:
    def __init__(self, url: str = "http://127.0.0.1:4723/wd/hub", log=None):
        self.url = url.rstrip("/")
        self.log = log or (lambda _: None)
        self.process = None

    def ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/status", timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def ensure(self) -> None:
        sdk_root = configure_android_sdk()
        if not sdk_root:
            raise AppiumServerError("未找到 Android SDK 根目录。请先安装 Android Platform-Tools，或设置 ANDROID_HOME。")
        self.log(f"Android SDK：{sdk_root}")
        if self.ready():
            self.log("复用已运行的 Appium 服务")
            return
        command = resolve_appium()
        if not command:
            launcher = "run_windows.bat" if sys.platform == "win32" else "run_mac.command"
            raise AppiumServerError(f"未找到 Appium。请重新运行 {launcher}，让它自动安装 Node.js、Appium 和 UiAutomator2 驱动。")
        self.log("正在启动 Appium 服务…")
        self.process = subprocess.Popen([*appium_launch_command(command), "--base-path", "/wd/hub", "--port", "4723", "--log-level", "error"], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, **subprocess_options())
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.ready():
                self.log("Appium 服务已就绪")
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.5)
        self.stop()
        raise AppiumServerError("Appium 启动失败。请检查 Node.js、Appium UiAutomator2 驱动，或查看终端输出。")

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            if sys.platform == "win32":
                # Terminate only the server tree this instance created. Reused
                # external Appium servers never populate self.process.
                try:
                    subprocess.run(["taskkill.exe", "/PID", str(self.process.pid), "/T", "/F"],
                                   capture_output=True, timeout=5, **subprocess_options())
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None
