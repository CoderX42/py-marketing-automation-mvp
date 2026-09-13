"""Navigation regression tests; no phone, Appium server, or third-party packages.

Run from the project directory:
    python3 -m unittest discover -s tests -p 'test_navigation.py' -v
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, PropertyMock, call, patch


def _load_automation():
    """Stub transport imports only; exercise the actual navigation methods."""
    module_names = (
        "appium",
        "appium.webdriver",
        "appium.options",
        "appium.options.android",
        "appium.webdriver.common",
        "appium.webdriver.common.appiumby",
        "selenium",
        "selenium.webdriver",
        "selenium.webdriver.remote",
        "selenium.webdriver.remote.client_config",
    )
    stubs = {name: types.ModuleType(name) for name in module_names}
    for name, module in stubs.items():
        module.__path__ = []
        if "." in name:
            parent, child = name.rsplit(".", 1)
            setattr(stubs[parent], child, module)
    stubs["appium.options.android"].UiAutomator2Options = object
    stubs["appium.webdriver.common.appiumby"].AppiumBy = types.SimpleNamespace(
        ANDROID_UIAUTOMATOR="-android uiautomator",
        XPATH="xpath",
        ID="id",
        CLASS_NAME="class name",
        CSS_SELECTOR="css selector",
    )
    stubs["selenium.webdriver.remote.client_config"].ClientConfig = object
    source = Path(__file__).resolve().parents[1] / "automation.py"
    spec = importlib.util.spec_from_file_location("marketing_automation_under_test", source)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module


automation = _load_automation()


def _home_xml(*, include_free=True, free_text="营销助手(免签入)"):
    root = ET.fromstring(
        """<hierarchy>
          <node clickable="true" bounds="[0,0][1200,2608]">
            <node resource-id="ordinary_card" clickable="true" bounds="[609,833][889,1153]">
              <node resource-id="com.sh.cm.grid4a:id/floor_subitem_tv"
                    text="营销助手" clickable="false" bounds="[625,1020][873,1114]" />
            </node>
            <node resource-id="free_card" clickable="true" bounds="[889,833][1169,1153]">
              <node clickable="false" bounds="[889,833][1169,1153]">
                <node resource-id="com.sh.cm.grid4a:id/floor_subitem_tv"
                      text="营销助手(免签入)" clickable="false" bounds="[905,1020][1153,1114]" />
              </node>
            </node>
            <node resource-id="com.sh.cm.grid4a:id/menu_nav_item_2"
                  clickable="true" bounds="[240,2380][480,2560]">
              <node text="智慧营销" clickable="false" bounds="[265,2500][455,2550]" />
            </node>
          </node>
        </hierarchy>"""
    )
    cards = root.find("node")
    free_card = cards.find("node[@resource-id='free_card']")
    if include_free:
        free_card.find("node/node").set("text", free_text)
    else:
        cards.remove(free_card)
    return root


class AdbTargetNavigationTests(unittest.TestCase):
    def setUp(self):
        # Avoid __init__ filesystem setup and all device transport operations.
        self.navigator = object.__new__(automation.MarketingAutomation)
        self.navigator.config = {
            "app_package": "com.sh.cm.grid4a",
            "selectors": {
                "marketing_entry": "营销助手(免签入)",
                "smart_marketing_tab": "智慧营销",
                "home_marker": "个人业务",
                "full_view_marker": "全景视图",
                "phone_input_hint": "手机号码",
                "jump_button": "跳转",
                "detail_marker": "营销详情",
                "network_error": "当前网络不可用",
            },
        }
        self.navigator.driver = Mock()
        self.navigator.log = Mock()
        self.navigator._context = "NATIVE_APP"
        self.navigator._adb_ui_root = Mock(return_value=_home_xml())
        self.navigator._adb_tap_xy = Mock(return_value=True)

    def test_free_entry_uses_nearest_clickable_parent_when_both_entries_exist(self):
        self.assertTrue(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.navigator._adb_tap_xy.assert_called_once_with(1029, 993)
        self.navigator._adb_ui_root.assert_called_once_with()

    def test_plain_entry_alone_does_not_match_free_entry(self):
        self.navigator._adb_ui_root.return_value = _home_xml(include_free=False)
        self.assertFalse(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.navigator._adb_tap_xy.assert_not_called()

    def test_failed_transport_is_not_reported_as_success(self):
        self.navigator._adb_tap_xy.return_value = False
        self.assertFalse(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.navigator._adb_tap_xy.assert_called_once_with(1029, 993)

    def test_missing_or_empty_snapshot_never_taps(self):
        for root in (None, ET.fromstring("<hierarchy />")):
            with self.subTest(root=root):
                self.navigator._adb_ui_root.return_value = root
                self.navigator._adb_tap_xy.reset_mock()
                self.assertFalse(self.navigator._adb_tap_target(text="营销助手(免签入)"))
                self.navigator._adb_tap_xy.assert_not_called()

    def test_layout_line_breaks_and_full_width_parentheses_are_equivalent(self):
        self.navigator._adb_ui_root.return_value = _home_xml(free_text="营销助手（免签\n入）")
        self.assertTrue(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.navigator._adb_tap_xy.assert_called_once_with(1029, 993)

    def test_target_is_selected_from_a_fresh_snapshot_each_time(self):
        self.navigator._adb_ui_root.side_effect = [_home_xml(), _home_xml(include_free=False)]
        self.assertTrue(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.assertFalse(self.navigator._adb_tap_target(text="营销助手(免签入)"))
        self.assertEqual(self.navigator._adb_ui_root.call_count, 2)
        self.navigator._adb_tap_xy.assert_called_once_with(1029, 993)

    def test_resource_id_selects_the_bottom_tab_container(self):
        self.assertTrue(
            self.navigator._adb_tap_target(resource_id="com.sh.cm.grid4a:id/menu_nav_item_2")
        )
        self.navigator._adb_tap_xy.assert_called_once_with(360, 2470)

    def test_partial_resource_id_does_not_match(self):
        self.assertFalse(self.navigator._adb_tap_target(resource_id="menu_nav_item_2"))
        self.navigator._adb_tap_xy.assert_not_called()

    def test_network_banner_is_an_explicit_navigation_failure(self):
        self.navigator._adb_ui_root.return_value = ET.fromstring(
            '<hierarchy><node text="当前网络不可用，请检查网络环境" /></hierarchy>'
        )
        with self.assertRaises(automation.NetworkUnavailableError) as raised:
            self.navigator._raise_if_network_error()
        self.assertIsInstance(raised.exception, automation.NavigationError)
        self.assertIn("网络", str(raised.exception))
        self.navigator._adb_ui_root.assert_called_once_with()
        self.navigator.driver.find_elements.assert_not_called()
        self.navigator._adb_tap_xy.assert_not_called()

    def test_healthy_page_does_not_raise_network_failure(self):
        self.navigator._raise_if_network_error()
        self.navigator._adb_ui_root.assert_called_once_with()
        self.navigator.driver.find_elements.assert_not_called()
        self.navigator._adb_tap_xy.assert_not_called()

    def test_enters_free_assistant_through_every_required_home_step_in_order(self):
        """Each correct tap reveals the next page; skipping a step cannot pass."""
        pages = [
            '<hierarchy><node resource-id="com.sh.cm.grid4a:id/menu_nav_item_2" '
            'text="智慧营销" clickable="true" bounds="[240,2380][480,2560]" /></hierarchy>',
            '<hierarchy><node resource-id="com.sh.cm.grid4a:id/home_business_tab_tv" '
            'text="个人业务" clickable="true" bounds="[0,200][600,350]" /></hierarchy>',
            '<hierarchy><node resource-id="com.sh.cm.grid4a:id/home_business_floor_title_tv" '
            'text="全景视图" clickable="true" bounds="[0,350][300,550]" /></hierarchy>',
            ET.tostring(_home_xml(), encoding="unicode"),
            '<hierarchy><node text="营销助手(免签入)" /><node text="手机号码" /></hierarchy>',
        ]
        expected_taps = [(360, 2470), (300, 275), (150, 450), (1029, 993)]
        page_index = 0

        def advance_page(x, y):
            nonlocal page_index
            self.assertEqual((x, y), expected_taps[page_index])
            page_index += 1
            return True

        self.navigator.driver.current_activity = "com.richeninfo.home.activity.MainActivity"
        self.navigator._adb_ui_root.side_effect = lambda: ET.fromstring(pages[page_index])
        self.navigator._adb_tap_xy.side_effect = advance_page
        self.navigator._wait_phone_input = Mock()
        self.navigator._find_text = Mock(return_value=None)
        with patch.object(automation.time, "sleep"):
            self.navigator._ensure_entry()
        self.assertEqual(page_index, 4)
        self.assertEqual(self.navigator._adb_tap_xy.call_args_list, [call(*xy) for xy in expected_taps])
        self.navigator._wait_phone_input.assert_called_once_with(timeout=30)
        self.navigator._find_text.assert_not_called()

    def test_no_data_uses_home_common_free_assistant_and_verifies_input_title(self):
        pages = [
            '<hierarchy><node resource-id="com.sh.cm.grid4a:id/menu_nav_item_2" '
            'text="智慧营销" clickable="true" bounds="[240,2380][480,2560]" /></hierarchy>',
            '<hierarchy><node text="暂无数据" />'
            '<node resource-id="com.sh.cm.grid4a:id/menu_nav_item_1" text="首页" '
            'clickable="true" bounds="[0,2380][240,2560]" /></hierarchy>',
            '<hierarchy><node text="常用" />'
            '<node resource-id="com.sh.cm.grid4a:id/common_use_item_tv" text="营销助手" '
            'clickable="true" bounds="[0,400][300,700]" />'
            '<node resource-id="com.sh.cm.grid4a:id/common_use_item_tv" text="营销助手(免签入)" '
            'clickable="true" bounds="[300,400][600,700]" /></hierarchy>',
            '<hierarchy><node text="营销助手(免签入)" /><node text="手机号码" /></hierarchy>',
        ]
        expected_taps = [(360, 2470), (120, 2470), (450, 550)]
        page_index = 0

        def advance_page(x, y):
            nonlocal page_index
            self.assertEqual((x, y), expected_taps[page_index])
            page_index += 1
            return True

        self.navigator.driver.current_activity = "com.richeninfo.home.activity.MainActivity"
        self.navigator._adb_ui_root.side_effect = lambda: ET.fromstring(pages[page_index])
        self.navigator._adb_tap_xy.side_effect = advance_page
        self.navigator._wait_phone_input = Mock()
        self.navigator._ensure_entry()
        self.assertEqual(page_index, 3)
        self.assertEqual(self.navigator._adb_tap_xy.call_args_list, [call(*xy) for xy in expected_taps])
        self.navigator._wait_phone_input.assert_called_once_with(timeout=30)

    def test_marketing_page_timeout_falls_back_to_common_entry(self):
        self.navigator._wait_ui = Mock(return_value=None)
        self.navigator._tap_common_free_entry = Mock()
        self.navigator._tap_home_marketing_flow()
        self.navigator._adb_tap_xy.assert_called_once_with(360, 2470)
        self.navigator._tap_common_free_entry.assert_called_once_with()

    def test_common_fallback_requires_both_common_resource_id_and_exact_free_label(self):
        for free_node in (
            '',
            '<node resource-id="com.sh.cm.grid4a:id/floor_subitem_tv" text="营销助手(免签入)" '
            'clickable="true" bounds="[300,400][600,700]" />',
            '<node resource-id="com.sh.cm.grid4a:id/common_use_item_tv" text="营销助手(免签入)帮助" '
            'clickable="true" bounds="[300,400][600,700]" />',
        ):
            with self.subTest(free_node=free_node):
                self.navigator._adb_ui_root.return_value = ET.fromstring(
                    '<hierarchy><node resource-id="com.sh.cm.grid4a:id/menu_nav_item_1" text="首页" '
                    'clickable="true" bounds="[0,2380][240,2560]" />'
                    '<node text="常用" clickable="true" bounds="[0,200][200,300]" />'
                    '<node resource-id="com.sh.cm.grid4a:id/common_use_item_tv" text="营销助手" '
                    'clickable="true" bounds="[0,400][300,700]" />'
                    f'{free_node}</hierarchy>'
                )
                self.navigator._adb_tap_xy.reset_mock()
                with self.assertRaisesRegex(automation.NavigationError, "未找到.*免签入"):
                    self.navigator._tap_common_free_entry()
                self.assertEqual(self.navigator._adb_tap_xy.call_args_list, [call(120, 2470), call(100, 250)])

    def test_entry_rejects_ordinary_assistant_title_even_when_phone_input_exists(self):
        self.navigator._back_to_entry_surface = Mock()
        self.navigator._tap_home_marketing_flow = Mock()
        self.navigator._wait_phone_input = Mock()
        self.navigator._adb_ui_root.return_value = ET.fromstring(
            '<hierarchy><node text="营销助手" /><node text="手机号码" /></hierarchy>'
        )
        with self.assertRaisesRegex(automation.NavigationError, "标题不是.*免签入"):
            self.navigator._ensure_entry()
        self.navigator._wait_phone_input.assert_called_once_with(timeout=30)
        self.navigator._adb_tap_xy.assert_not_called()

    def test_default_query_skips_login_precheck_and_enters_navigation(self):
        self.navigator._require_manual_login_if_needed = Mock()
        self.navigator._ensure_entry = Mock()
        field = Mock()
        field.get_attribute.return_value = "13800138000"
        self.navigator._wait_phone_input = Mock(return_value=field)
        self.navigator._click_text = Mock()
        self.navigator._wait_detail_or_error = Mock(return_value=(["非上海移动", "重新输入"], "非上海移动"))
        self.navigator._click_if_present = Mock()
        result = self.navigator.query("13800138000")
        self.assertEqual(result["status"], "empty")
        self.navigator._require_manual_login_if_needed.assert_not_called()
        self.navigator._ensure_entry.assert_called_once_with()
        field.clear.assert_called_once_with()
        field.send_keys.assert_called_once_with("13800138000")
        field.get_attribute.assert_called_once_with("text")
        self.navigator._click_text.assert_called_once_with("jump_button", timeout=10)

    def test_explicit_login_precheck_remains_opt_in(self):
        self.navigator.config["check_login_state"] = True
        self.navigator._require_manual_login_if_needed = Mock(
            side_effect=automation.ManualLoginRequired("strict login check")
        )
        self.navigator._ensure_entry = Mock()
        with self.assertRaisesRegex(automation.ManualLoginRequired, "strict login check"):
            self.navigator.query("13800138000")
        self.navigator._require_manual_login_if_needed.assert_called_once_with()
        self.navigator._ensure_entry.assert_not_called()


class NavigationTransportTests(unittest.TestCase):
    def setUp(self):
        self.navigator = object.__new__(automation.MarketingAutomation)
        self.navigator.config = {"adb_path": "/test/adb"}
        self.navigator._context = "NATIVE_APP"
        self.navigator.driver = Mock()

    def test_active_driver_uses_fresh_page_source_without_launching_uiautomator_dump(self):
        source = PropertyMock(side_effect=[
            '<hierarchy><node text="智慧营销" /></hierarchy>',
            '<hierarchy><node text="营销助手(免签入)" /></hierarchy>',
        ])
        type(self.navigator.driver).page_source = source
        with patch.object(automation.subprocess, "run") as subprocess_run:
            first = self.navigator._adb_ui_root()
            second = self.navigator._adb_ui_root()
        self.assertEqual(first.find("node").get("text"), "智慧营销")
        self.assertEqual(second.find("node").get("text"), "营销助手(免签入)")
        self.assertEqual(source.call_count, 2)
        subprocess_run.assert_not_called()

    def test_broken_appium_session_does_not_spawn_competing_uiautomator(self):
        type(self.navigator.driver).page_source = PropertyMock(side_effect=RuntimeError("session lost"))
        with patch.object(automation.subprocess, "run") as subprocess_run:
            with self.assertRaisesRegex(RuntimeError, "session lost"):
                self.navigator._adb_ui_root()
        subprocess_run.assert_not_called()

    def test_hierarchy_read_switches_existing_webview_session_to_native(self):
        self.navigator._context = "WEBVIEW_com.sh.cm.grid4a"
        self.navigator.driver.page_source = '<hierarchy><node text="智慧营销" /></hierarchy>'
        with patch.object(automation.subprocess, "run") as subprocess_run:
            self.navigator._adb_ui_root()
        self.navigator.driver.switch_to.context.assert_called_once_with("NATIVE_APP")
        self.assertEqual(self.navigator._context, "NATIVE_APP")
        subprocess_run.assert_not_called()


class NavigationParsingTests(unittest.TestCase):
    def test_visible_text_falls_back_to_content_description(self):
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.driver = Mock()
        navigator.driver.page_source = (
            '<hierarchy><node text="" content-desc="营销详情"/>'
            '<node text="推荐套餐" content-desc="重复标签不应重复"/></hierarchy>'
        )
        self.assertEqual(navigator._visible_text(), ["营销详情", "推荐套餐"])

    def test_normal_text_handles_parentheses_and_layout_whitespace(self):
        self.assertEqual(
            automation.MarketingAutomation._normal_text(" 营销助手（免签\n入） "),
            "营销助手(免签入)",
        )

    def test_node_bounds_parses_real_device_rectangle(self):
        node = ET.Element("node", {"bounds": "[889,833][1169,1153]"})
        self.assertEqual(automation.MarketingAutomation._node_bounds(node), (889, 833, 1169, 1153))

    def test_node_bounds_rejects_missing_or_malformed_bounds(self):
        for bounds in (None, "", "889,833,1169,1153", "[x,833][1169,1153]"):
            with self.subTest(bounds=bounds):
                node = ET.Element("node", {} if bounds is None else {"bounds": bounds})
                self.assertIsNone(automation.MarketingAutomation._node_bounds(node))


if __name__ == "__main__":
    unittest.main()
