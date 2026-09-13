"""Batch persistence regressions, using mocked phone/server transports only."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from excel_io import InputRecord


class BatchWorkerPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.records = [InputRecord(2, "13800000001"), InputRecord(3, "13800000002")]
        self.worker = app.BatchWorker(self.records, {"timing": {
            "between_records_seconds": 0, "random_jitter_seconds": 0,
            "pause_every_records": 0,
        }}, Path("/tmp/batch-regression-result.xlsx"))
        self.messages = []
        self.worker.message.connect(self.messages.append)
        self.automation = Mock()
        self.patches = [
            patch.object(app, "MarketingAutomation", return_value=self.automation),
            patch.object(app, "AppiumServer"),
            patch.object(app, "load_existing_results", return_value={}),
            patch.object(app, "load_result_journal", return_value={}),
            patch.object(app, "append_result_journal"),
            patch.object(app, "write_result"),
        ]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches:
            self.addCleanup(p.stop)
        self.journal = self.mocks[4]
        self.export = self.mocks[5]

    def snapshot(self, phone, text="第一屏内容"):
        value = {"phone": phone, "raw_text": text, "status": "partial",
                 "collection_complete": False, "items": []}
        self.automation.on_detail_progress(value)
        return value

    def test_first_details_saved_before_query_finishes(self):
        def query(phone):
            self.snapshot(phone)
            self.assertEqual(self.journal.call_args.args[1], phone)
            self.assertTrue(self.export.called)
            self.snapshot(phone, "第一屏内容\n第二屏内容")
            return {"status": "success", "raw_text": "第一屏内容\n第二屏内容", "collection_complete": True}
        self.automation.query.side_effect = query
        self.worker.run()
        self.assertEqual(self.worker.success_count, 2)
        self.assertEqual(self.export.call_count, 4)
        self.assertEqual(self.journal.call_count, 6)
        self.assertEqual(self.worker.results[self.records[0].phone]["raw_text"], "第一屏内容\n第二屏内容")

    def test_exception_after_details_preserves_text_without_success(self):
        def query(phone):
            self.snapshot(phone, "需要保留的正文")
            raise RuntimeError("滚动服务连接中断")
        self.automation.query.side_effect = query
        self.worker.run()
        self.assertEqual(self.worker.success_count, 0)
        self.assertEqual(self.worker.failure_count, 2)
        for value in self.worker.results.values():
            self.assertEqual(value["status"], "partial")
            self.assertEqual(value["raw_text"], "需要保留的正文")
        self.assertTrue(self.worker.output_saved)

    def test_session_interruption_stops_after_saving_partial(self):
        def query(phone):
            value = self.snapshot(phone)
            return {**value, "session_interrupted": True, "error": "当前网络不可用"}
        self.automation.query.side_effect = query
        self.worker.run()
        self.assertEqual(self.automation.query.call_count, 1)
        self.assertEqual(self.worker.success_count, 0)
        self.assertTrue(self.worker.output_saved)
        self.assertIn("暂停", self.worker.end_reason)
        self.assertEqual(self.worker.results[self.records[0].phone]["status"], "partial")

    def test_excel_write_failure_keeps_journal_and_never_reports_saved_success(self):
        def query(phone):
            self.snapshot(phone)
            return {"status": "success", "raw_text": "正文", "collection_complete": True}
        self.automation.query.side_effect = query
        self.export.side_effect = [None, PermissionError("file locked")]
        self.worker.run()
        self.assertEqual(self.automation.query.call_count, 1)
        self.assertTrue(self.worker.journal_saved)
        self.assertTrue(self.worker.save_failed)
        self.assertEqual(self.worker.success_count, 0)
        self.assertIn("Excel 未完成更新", self.worker.completion_message())
        self.assertIn("恢复日志", self.worker.completion_message())
        self.assertFalse(any("查询成功" in line for line in self.messages))

    def test_failed_checkpoint_aborts_before_next_phone(self):
        self.automation.query.side_effect = lambda phone: self.snapshot(phone)
        self.journal.side_effect = OSError("disk full")
        self.worker.run()
        self.assertEqual(self.automation.query.call_count, 1)
        self.export.assert_not_called()
        self.assertTrue(self.worker.save_failed)
        self.assertEqual(self.worker.success_count, 0)

    def test_business_popup_result_does_not_block_next_phone(self):
        self.automation.query.return_value = {"status": "empty", "error": "该号码非上海移动现网号码"}
        self.worker.run()
        self.assertEqual(self.automation.query.call_count, 2)
        self.assertEqual(self.worker.empty_count, 2)
        self.assertEqual(self.export.call_count, 2)

    def test_resume_skips_completed_and_preserves_previous_partial_on_early_failure(self):
        self.mocks[2].return_value = {
            self.records[0].phone: {"status": "success", "raw_text": "已完成内容"},
            self.records[1].phone: {"status": "partial", "raw_text": "上次中断前的内容"},
        }
        self.automation.query.side_effect = app.NavigationError("未找到输入页")
        self.worker.run()
        self.automation.query.assert_called_once_with(self.records[1].phone)
        self.assertEqual(self.worker.skipped_count, 1)
        self.assertEqual(self.worker.results[self.records[1].phone]["raw_text"], "上次中断前的内容")
        self.assertEqual(self.worker.results[self.records[1].phone]["status"], "partial")

    def test_journal_replayed_into_excel_before_connecting_phone(self):
        self.mocks[3].return_value = {r.phone: {"status": "success", "raw_text": "journal正文"} for r in self.records}
        self.automation.connect.side_effect = lambda: self.assertEqual(self.export.call_count, 1)
        self.worker.run()
        self.automation.query.assert_not_called()
        self.assertEqual(self.worker.skipped_count, 2)
        self.assertEqual(self.worker.saved_count, 2)

    def test_incomplete_collection_cannot_count_as_success(self):
        self.automation.query.return_value = {"status": "success", "raw_text": "正文", "collection_complete": False}
        self.worker.run()
        self.assertEqual(self.worker.success_count, 0)
        self.assertEqual(self.worker.failure_count, 2)
        self.assertTrue(all(value["status"] == "partial" for value in self.worker.results.values()))


if __name__ == "__main__":
    unittest.main()
