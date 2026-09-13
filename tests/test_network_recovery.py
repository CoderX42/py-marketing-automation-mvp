"""Network/session interruption coverage for the batch checkpoint contract.

These tests use mocked Appium transports and temporary workbooks.  They model
the failure shown by the Android app (``当前网络不可用，请检查网络环境``)
without connecting to a real phone.  The important contract is that a network
failure is a resumable session interruption, never a successful/empty phone
result, and that a later run can continue from the durable checkpoint.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, PropertyMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app
import automation
from excel_io import InputRecord, load_existing_results, load_result_journal


class NetworkRecoveryBatchTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            InputRecord(2, "13800000001", "甲"),
            InputRecord(3, "13800000002", "乙"),
        ]

    @staticmethod
    def _config():
        return {
            "timing": {
                "between_records_seconds": 0,
                "random_jitter_seconds": 0,
                "pause_every_records": 0,
            }
        }

    def _run(self, output: Path, automation: Mock):
        """Run one worker synchronously with only the phone transport mocked."""
        worker = app.BatchWorker(self.records, self._config(), output)
        manual_login = []
        worker.manual_login.connect(manual_login.append)
        with patch.object(app, "MarketingAutomation", return_value=automation), \
             patch.object(app, "AppiumServer"):
            worker.run()
        return worker, manual_login

    def test_network_loss_after_detail_checkpoint_is_durable_and_stops_batch(self):
        """A network banner must preserve text and leave the next phone untouched."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "marketing.xlsx"
            automation = Mock()

            def query(phone):
                # Simulate entering the detail page and receiving its first
                # checkpoint before the app reports the network failure.
                automation.on_detail_progress({
                    "phone": phone,
                    "status": "partial",
                    "raw_text": "营销详情\n推荐方案甲",
                    "items": [],
                    "collection_complete": False,
                })
                raise app.NetworkUnavailableError(
                    "当前网络不可用，请检查网络环境"
                )

            automation.query.side_effect = query
            worker, manual_login = self._run(output, automation)

            self.assertEqual(automation.query.call_count, 1)
            self.assertEqual(worker.success_count, 0)
            self.assertEqual(worker.failure_count, 1)
            self.assertTrue(worker.output_saved)
            self.assertTrue(worker.journal_saved)
            self.assertIn("暂停", worker.end_reason)
            self.assertEqual(len(manual_login), 1)
            self.assertIn("网络", manual_login[0])

            # Both durable layers retain the captured detail as a retryable
            # partial row.  The second phone has no journal entry and was not
            # queried, so a resumed run can safely start there/at this row.
            journal = load_result_journal(output)
            self.assertEqual(set(journal), {self.records[0].phone})
            self.assertEqual(journal[self.records[0].phone]["status"], "partial")
            self.assertIn("推荐方案甲", journal[self.records[0].phone]["raw_text"])
            self.assertIn("网络", journal[self.records[0].phone]["error"])

            workbook = load_existing_results(output)
            self.assertEqual(workbook[self.records[0].phone]["status"], "partial")
            self.assertEqual(
                workbook[self.records[0].phone]["raw_text"],
                "营销详情\n推荐方案甲",
            )

    def test_network_loss_before_details_is_login_required_not_empty(self):
        """A failure before the detail page must never be recorded as no data."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "marketing.xlsx"
            automation = Mock()
            automation.query.side_effect = app.NetworkUnavailableError(
                "当前网络不可用，请检查网络环境"
            )
            worker, manual_login = self._run(output, automation)

            self.assertEqual(automation.query.call_count, 1)
            self.assertEqual(worker.success_count, 0)
            self.assertEqual(worker.empty_count, 0)
            self.assertEqual(worker.failure_count, 1)
            self.assertEqual(len(manual_login), 1)
            self.assertIn("重新登录", worker.completion_message())

            journal = load_result_journal(output)
            saved = journal[self.records[0].phone]
            self.assertEqual(saved["status"], "login_required")
            self.assertEqual(saved["items"], [])
            self.assertNotIn(self.records[1].phone, journal)

    def test_second_run_retries_partial_row_after_network_recovers(self):
        """A recovered session reuses the checkpoint and exports both rows."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "marketing.xlsx"

            first_automation = Mock()

            def fail_first(phone):
                first_automation.on_detail_progress({
                    "phone": phone,
                    "status": "partial",
                    "raw_text": "网络中断前的详情",
                    "items": [],
                    "collection_complete": False,
                })
                raise app.NetworkUnavailableError("当前网络不可用")

            first_automation.query.side_effect = fail_first
            first_worker, _ = self._run(output, first_automation)
            self.assertIn("暂停", first_worker.end_reason)

            recovered = Mock()
            recovered.query.side_effect = lambda phone: {
                "phone": phone,
                "status": "success",
                "raw_text": f"网络恢复后的详情-{phone[-4:]}",
                "items": [],
                "collection_complete": True,
            }
            second_worker, manual_login = self._run(output, recovered)

            self.assertEqual(
                recovered.query.call_args_list,
                [call(record.phone) for record in self.records],
            )
            self.assertEqual(second_worker.success_count, 2)
            self.assertEqual(second_worker.failure_count, 0)
            self.assertEqual(second_worker.skipped_count, 0)
            self.assertFalse(manual_login)

            journal = load_result_journal(output)
            self.assertEqual(
                {phone: result["status"] for phone, result in journal.items()},
                {record.phone: "success" for record in self.records},
            )
            workbook = load_existing_results(output)
            for record in self.records:
                self.assertEqual(workbook[record.phone]["status"], "success")
                self.assertIn(record.phone[-4:], workbook[record.phone]["raw_text"])


class NetworkRetryQueryTests(unittest.TestCase):
    """The query layer retries transient network banners, but not login errors."""

    PHONE = "13800138000"

    def _navigator(self, retries=2, delay=15):
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "timing": {
                "network_retries": retries,
                "network_retry_seconds": delay,
            },
            "selectors": {"confirm_button": "确定"},
        }
        navigator.log = Mock()
        navigator._dismiss_network_popup = Mock()
        return navigator

    def test_transient_network_error_retries_same_phone_and_dismisses_popup(self):
        navigator = self._navigator(retries=2, delay=15)
        navigator._query_once = Mock(side_effect=[
            automation.NetworkUnavailableError("当前网络不可用，请检查网络环境"),
            {
                "phone": self.PHONE,
                "status": "success",
                "raw_text": "恢复后的营销详情",
                "items": [],
                "collection_complete": True,
            },
        ])
        with patch.object(automation.time, "sleep") as sleep:
            result = navigator.query(self.PHONE)

        self.assertEqual(result["status"], "success")
        self.assertEqual(navigator._query_once.call_args_list,
                         [call(self.PHONE), call(self.PHONE)])
        navigator._dismiss_network_popup.assert_called_once_with()
        sleep.assert_called_once_with(15)

    def test_login_error_is_not_retried_or_dismissed(self):
        navigator = self._navigator(retries=2, delay=15)
        navigator._query_once = Mock(
            side_effect=automation.ManualLoginRequired("请在手机上重新登录")
        )
        with patch.object(automation.time, "sleep") as sleep:
            with self.assertRaises(automation.ManualLoginRequired):
                navigator.query(self.PHONE)

        navigator._query_once.assert_called_once_with(self.PHONE)
        navigator._dismiss_network_popup.assert_not_called()
        sleep.assert_not_called()

    def test_exhausted_network_retries_return_longest_partial(self):
        navigator = self._navigator(retries=2, delay=15)
        short = {
            "phone": self.PHONE,
            "status": "partial",
            "raw_text": "营销详情\n首屏",
            "items": [],
            "collection_complete": False,
            "session_interrupted": True,
            "interruption_type": "network",
            "error": "当前网络不可用",
        }
        longest = {**short, "raw_text": "营销详情\n首屏\n第二屏"}
        # The third result is shorter on purpose: the retry wrapper must retain
        # the most complete checkpoint seen across all attempts.
        navigator._query_once = Mock(side_effect=[short, longest, short])
        with patch.object(automation.time, "sleep") as sleep:
            result = navigator.query(self.PHONE)

        self.assertEqual(navigator._query_once.call_count, 3)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["session_interrupted"])
        self.assertEqual(result["interruption_type"], "network")
        self.assertEqual(result["raw_text"], longest["raw_text"])
        self.assertEqual(navigator._dismiss_network_popup.call_count, 2)
        self.assertEqual(sleep.call_args_list, [call(15), call(30)])

    def test_retry_backoff_uses_injected_waiter(self):
        navigator = self._navigator(retries=1, delay=15)
        waiter = Mock()
        navigator.config["timing"]["retry_wait"] = waiter
        navigator._query_once = Mock(side_effect=[
            automation.NetworkUnavailableError("当前网络不可用，请检查网络环境"),
            {"phone": self.PHONE, "status": "success", "raw_text": "恢复", "items": [],
             "collection_complete": True},
        ])
        with patch.object(automation.time, "sleep") as sleep:
            result = navigator.query(self.PHONE)

        self.assertEqual(result["status"], "success")
        waiter.assert_called_once_with(15)
        sleep.assert_not_called()

    def test_dismiss_network_popup_honors_configured_marker(self):
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "自定义网络异常", "confirm_button": "确定"}}
        navigator._adb_ui_snapshot = Mock(return_value=(["自定义网络异常", "确定"], (10, 20, 110, 80)))
        navigator._adb_tap_xy = Mock(return_value=True)
        navigator._click_if_present = Mock()
        navigator.log = Mock()

        navigator._dismiss_network_popup()

        navigator._adb_tap_xy.assert_called_once_with(60, 50)
        navigator._click_if_present.assert_not_called()

    def test_known_security_tunnel_expiry_is_manual_login_not_network_retry(self):
        navigator = self._navigator(retries=2)
        with self.assertRaises(automation.ManualLoginRequired):
            navigator._check_session_message(["长时间未使用安全隧道已断开请重新登录"])

    def test_server_timeout_message_is_classified_as_transient_network_error(self):
        navigator = self._navigator(retries=2)
        with self.assertRaises(automation.NetworkUnavailableError):
            navigator._check_session_message(["服务器请求超时"])

    def test_bottom_timeout_toast_is_classified_as_transient_network_error(self):
        """The Android toast is different from the red banner text.

        It is commonly the only network signal left in the hierarchy after
        the page has already returned to the home screen.  Treating it as a
        transient network interruption keeps the current phone retryable.
        """
        navigator = self._navigator(retries=2)
        with self.assertRaises(automation.NetworkUnavailableError):
            navigator._check_session_message(["网络连接超时，请稍后再试"])

    def test_generic_retry_phrase_in_detail_text_is_not_network_error(self):
        """Common marketing copy must not interrupt detail collection."""
        navigator = self._navigator(retries=2)
        navigator._check_session_message(["活动说明：请稍后再试，感谢您的理解"])

    def test_top_network_banner_close_control_is_tapped_before_retry(self):
        """A non-modal red banner must be dismissed before tapping underneath it."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="当前网络不可用，请检查网络环境" bounds="[0,110][1200,310]" />'
            '<node content-desc="关闭" clickable="true" bounds="[1080,110][1200,310]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        navigator._dismiss_network_popup()

        navigator._adb_tap_xy.assert_called_once()
        x, y = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(x, 1080)
        self.assertLessEqual(x, 1200)
        self.assertGreaterEqual(y, 110)
        self.assertLessEqual(y, 310)

    def test_left_close_control_is_not_selected_for_network_banner(self):
        """The detail header's left-side close action must remain untouched."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="当前网络不可用，请检查网络环境" bounds="[0,110][1200,310]" />'
            '<node content-desc="关闭" clickable="true" bounds="[0,110][120,310]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._dismiss_network_popup())
        x, _ = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(x, 780)

    def test_network_banner_without_close_node_uses_safe_top_right_fallback(self):
        """Some ROMs expose only the banner text; use its bounds for the X tap."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy><node text="当前网络不可用，请检查网络环境" '
            'bounds="[0,110][1200,310]" /></hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        navigator._dismiss_network_popup()

        navigator._adb_tap_xy.assert_called_once()
        x, y = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(x, 1080)
        self.assertLessEqual(x, 1200)
        self.assertGreaterEqual(y, 110)
        self.assertLessEqual(y, 310)

    def test_full_screen_marker_fallback_stays_inside_top_banner(self):
        """A full-screen WebView bound must map the fallback tap to the red strip."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = Mock()
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator.driver.get_window_size.return_value = {"width": 1200, "height": 2608}
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy><node text="当前网络不可用，请检查网络环境" '
            'bounds="[0,0][1200,2608]" /></hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._dismiss_network_popup())
        x, y = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(x, 1080)
        self.assertGreaterEqual(y, 78)   # 3% of 2608
        self.assertLessEqual(y, 235)     # 9% of 2608

    def test_banner_without_text_bounds_uses_labelled_top_right_close_node(self):
        """A bridge may omit the banner rectangle but expose the X node."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="当前网络不可用，请检查网络环境" />'
            '<node content-desc="关闭" clickable="true" bounds="[1080,110][1200,310]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._dismiss_network_popup())

        navigator._adb_tap_xy.assert_called_once()
        x, y = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(x, 1080)
        self.assertLessEqual(y, 310)

    def test_network_dialog_with_confirm_button_is_dismissed(self):
        """Older APK builds use a centered alert instead of the red banner."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="当前网络不可用，请检查网络环境" bounds="[120,900][1080,1400]" />'
            '<node text="确定" clickable="true" bounds="[500,1180][700,1300]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._dismiss_network_popup())
        navigator._adb_tap_xy.assert_called_once_with(600, 1240)

    def test_bottom_timeout_toast_with_bounds_is_never_tapped(self):
        """A Toast may expose bounds, but tapping it would hit page content."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy><node text="网络连接超时，请稍后再试" '
            'bounds="[200,2050][1000,2220]" /></hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        dismissed = navigator._dismiss_network_popup()

        self.assertFalse(dismissed)
        navigator._adb_tap_xy.assert_not_called()

    def test_toast_only_does_not_tap_underlying_close_control(self):
        """A page-level close button must survive while a Toast is visible."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="营销详情" bounds="[0,80][1200,180]" />'
            '<node content-desc="关闭" clickable="true" bounds="[1080,80][1200,180]" />'
            '<node text="网络连接超时，请稍后再试" '
            'bounds="[200,2050][1000,2220]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertFalse(navigator._dismiss_network_popup())
        navigator._adb_tap_xy.assert_not_called()

    def test_toast_only_snapshot_does_not_tap_underlying_confirm(self):
        """Even a snapshot fallback must never treat a Toast as a dialog."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=None)
        navigator._adb_ui_snapshot = Mock(
            return_value=(["网络连接超时，请稍后再试", "确定"], (500, 1100, 700, 1200))
        )
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertFalse(navigator._dismiss_network_popup())
        navigator._adb_tap_xy.assert_not_called()

    def test_banner_is_selected_when_toast_and_banner_coexist(self):
        """When both signals are present, only the top red bar may be tapped."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "confirm_button": "确定",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="当前网络不可用，请检查网络环境" bounds="[0,110][1200,310]" />'
            '<node text="网络连接超时，请稍后再试" bounds="[200,2050][1000,2220]" />'
            '</hierarchy>'
        ))
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._dismiss_network_popup())
        x, y = navigator._adb_tap_xy.call_args.args
        self.assertGreaterEqual(y, 110)
        self.assertLessEqual(y, 310)
        self.assertGreaterEqual(x, 1080)

    def test_target_waits_for_banner_exit_animation_before_tapping(self):
        """A stale hierarchy during the X exit animation must not abort navigation."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        overlay = automation.ET.fromstring(
            '<hierarchy><node text="当前网络不可用，请检查网络环境" '
            'bounds="[0,110][1200,310]" /></hierarchy>'
        )
        clear = automation.ET.fromstring(
            '<hierarchy><node text="营销助手(免签入)" clickable="true" '
            'bounds="[300,500][700,800]" /></hierarchy>'
        )
        navigator._adb_ui_root = Mock(side_effect=[overlay, overlay, clear])
        navigator._dismiss_network_popup = Mock(return_value=True)
        navigator._adb_tap_xy = Mock(return_value=True)

        with patch.object(automation.time, "sleep"), patch.object(
            automation.time, "monotonic", side_effect=[0, 0.1, 0.2, 1.0]
        ):
            self.assertTrue(navigator._adb_tap_target(text="营销助手(免签入)"))

        navigator._adb_tap_xy.assert_called_once_with(500, 650)

    def test_target_can_be_tapped_while_bottom_toast_is_still_visible(self):
        """A Toast is transient feedback, not a blocker for the target page."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        root = automation.ET.fromstring(
            '<hierarchy>'
            '<node text="网络连接超时，请稍后再试" '
            'bounds="[200,2050][1000,2220]" />'
            '<node text="营销助手(免签入)" clickable="true" bounds="[300,500][700,800]" />'
            '</hierarchy>'
        )
        navigator._adb_ui_root = Mock(side_effect=[root, root])
        navigator._dismiss_network_popup = Mock(return_value=False)
        navigator._adb_tap_xy = Mock(return_value=True)

        self.assertTrue(navigator._adb_tap_target(text="营销助手(免签入)"))
        navigator._adb_tap_xy.assert_called_once_with(500, 650)

    def test_detail_snapshot_keeps_collecting_with_bottom_toast(self):
        """A transient Toast must not discard an already visible detail page."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {
            "selectors": {
                "network_error": "当前网络不可用",
                "detail_marker": "营销详情",
            }
        }
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        navigator._adb_ui_root = Mock(return_value=automation.ET.fromstring(
            '<hierarchy>'
            '<node text="营销详情" bounds="[0,80][1200,180]" />'
            '<node text="网络连接超时，请稍后再试" bounds="[200,2050][1000,2220]" />'
            '<node class="android.webkit.WebView" bounds="[0,180][1200,2400]">'
            '<node text="用户推荐方案" bounds="[40,250][1100,350]" />'
            '<node text="推荐套餐甲" bounds="[40,350][1100,500]" />'
            '</node>'
            '</hierarchy>'
        ))
        navigator._dismiss_network_popup = Mock(return_value=False)

        lines, _ = navigator._detail_snapshot()

        self.assertIn("推荐套餐甲", lines)
        self.assertNotIn("网络连接超时，请稍后再试", lines)
        navigator._dismiss_network_popup.assert_called_once()

    def test_wait_ui_accepts_target_behind_nonblocking_toast(self):
        """入口 waits must not time out while a bottom Toast is fading."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = None
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        root = automation.ET.fromstring(
            '<hierarchy>'
            '<node text="网络连接超时，请稍后再试" bounds="[200,2050][1000,2220]" />'
            '<node text="营销助手(免签入)" bounds="[300,500][700,800]" />'
            '</hierarchy>'
        )
        navigator._adb_ui_root = Mock(return_value=root)
        navigator._dismiss_network_popup = Mock(return_value=False)

        result = navigator._wait_ui(
            lambda current: navigator._has_label(current, "营销助手(免签入)"), timeout=1
        )

        self.assertIs(result, root)
        navigator._dismiss_network_popup.assert_called_once_with(root)

    def test_wait_phone_input_does_not_turn_network_error_into_timeout(self):
        """A network exception from a transient input-page read must escape immediately."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = Mock()
        navigator._context = "NATIVE_APP"
        navigator._raise_if_network_error = Mock()
        navigator._phone_input = Mock(
            side_effect=automation.NetworkUnavailableError("当前网络不可用，请检查网络环境")
        )
        with patch.object(automation.time, "sleep"):
            with self.assertRaises(automation.NetworkUnavailableError):
                navigator._wait_phone_input(timeout=10)

    def test_network_banner_appearing_during_input_wait_is_not_misreported_as_timeout(self):
        """A late banner must be classified as network loss while polling the field."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = Mock()
        navigator._context = "NATIVE_APP"
        navigator._dismiss_network_popup = Mock()
        navigator._raise_if_network_error = Mock(side_effect=[
            None,
            None,
            automation.NetworkUnavailableError("当前网络不可用，请检查网络环境"),
        ])
        navigator._phone_input = Mock(side_effect=TimeoutError("输入框尚未出现"))
        with patch.object(automation.time, "sleep"):
            with self.assertRaises(automation.NetworkUnavailableError):
                navigator._wait_phone_input(timeout=10)

        self.assertEqual(navigator._phone_input.call_count, 3)

    def test_input_timeout_without_network_marker_is_navigation_failure(self):
        """A page-read timeout alone must not be classified as network loss."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = Mock()
        navigator._context = "NATIVE_APP"
        navigator._dismiss_network_popup = Mock()
        navigator._raise_if_network_error = Mock()
        navigator._phone_input = Mock(side_effect=TimeoutError("输入框尚未出现"))
        # Avoid a real ten-second polling loop while retaining the timeout path.
        with patch.object(automation.time, "time", side_effect=[0, 20]), patch.object(
            automation.time, "sleep"
        ):
            with self.assertRaises(automation.NavigationError) as raised:
                navigator._wait_phone_input(timeout=10)
        self.assertNotIsInstance(raised.exception, automation.NetworkUnavailableError)

    def test_query_retries_same_phone_when_first_navigation_returns_home(self):
        """A home-page return must cause a fresh entry navigation for the same phone."""
        navigator = self._navigator(retries=1, delay=0)
        navigation_calls = []

        def query_once(phone):
            navigation_calls.append(phone)
            # Model the app returning to MainActivity with a network toast
            # after the first tap sequence.  The retry starts _query_once
            # again, so it must run the complete entry flow for the same row.
            if len(navigation_calls) == 1:
                raise automation.NetworkUnavailableError("网络连接超时，请稍后再试")
            return {
                "phone": phone,
                "status": "success",
                "raw_text": "营销详情\n恢复后的正文",
                "items": [],
                "collection_complete": True,
            }

        navigator._query_once = Mock(side_effect=query_once)
        navigator._dismiss_network_popup = Mock()
        with patch.object(automation.time, "sleep"):
            result = navigator.query(self.PHONE)

        self.assertEqual(result["status"], "success")
        self.assertEqual(navigation_calls, [self.PHONE, self.PHONE])
        self.assertEqual(navigator._query_once.call_args_list,
                         [call(self.PHONE), call(self.PHONE)])
        navigator._dismiss_network_popup.assert_called_once_with()

    def test_wait_ui_clears_overlay_before_accepting_underlying_page(self):
        """A valid label behind the red banner must not satisfy the wait early."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"selectors": {"network_error": "当前网络不可用"}}
        navigator.driver = None
        navigator.log = Mock()
        overlay = automation.ET.fromstring(
            '<hierarchy><node text="当前网络不可用，请检查网络环境" />'
            '<node text="营销助手(免签入)" /></hierarchy>'
        )
        clear = automation.ET.fromstring(
            '<hierarchy><node text="营销助手(免签入)" /></hierarchy>'
        )
        navigator._adb_ui_root = Mock(side_effect=[overlay, clear])
        navigator._dismiss_network_popup = Mock(return_value=True)

        with patch.object(automation.time, "monotonic", side_effect=[0, 1]):
            result = navigator._wait_ui(
                lambda root: navigator._has_label(root, "营销助手(免签入)"), timeout=5
            )

        self.assertIs(result, clear)
        navigator._dismiss_network_popup.assert_called_once_with(overlay)

    def test_unknown_target_package_activity_is_popped_back_to_main_page(self):
        """A plugin activity in the target APK must not abort the next number."""
        navigator = object.__new__(automation.MarketingAutomation)
        navigator.config = {"app_package": "com.sh.cm.grid4a"}
        navigator.driver = Mock()
        navigator._context = "NATIVE_APP"
        navigator.log = Mock()
        type(navigator.driver).current_activity = PropertyMock(
            side_effect=[
                ".plugin.gallery.ui.AlbumPreviewUI",
                "com.richeninfo.home.activity.MainActivity",
            ]
        )
        type(navigator.driver).current_package = PropertyMock(
            return_value="com.sh.cm.grid4a"
        )
        navigator._dismiss_network_popup = Mock()

        with patch.object(automation.time, "sleep"):
            navigator._back_to_entry_surface()

        navigator.driver.back.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
