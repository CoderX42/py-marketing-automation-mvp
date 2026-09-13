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
from unittest.mock import Mock, call, patch

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


if __name__ == "__main__":
    unittest.main()
