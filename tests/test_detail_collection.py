"""Detail preservation regressions with simulated viewports; no phone or files."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from unittest.mock import Mock, patch

from test_navigation import automation


class PersistenceFailure(OSError):
    is_persistence_error = True


class DetailCollectionTests(unittest.TestCase):
    PHONE = "13800138000"

    def setUp(self):
        self.navigator = object.__new__(automation.MarketingAutomation)
        self.navigator.config = {
            "selectors": {"detail_marker": "营销详情", "login_marker": "短信验证码登录"},
            "timing": {"page_timeout_seconds": 30, "detail_max_scrolls": 10},
        }
        self.navigator.driver = Mock()
        self.navigator.log = Mock()
        self.navigator._current_phone = self.PHONE
        self.navigator._query_time = "2026-09-13T12:00:00"
        self.navigator._collected_detail = []
        self.navigator._collection_complete = False
        self.navigator._detail_checkpointed = False
        self.navigator.on_detail_progress = Mock()
        self.navigator._swipe_detail = Mock()
        self.navigator._ensure_entry = Mock()
        self.field = Mock()
        self.field.get_attribute.return_value = self.PHONE
        self.navigator._wait_phone_input = Mock(return_value=self.field)
        self.navigator._click_text = Mock()
        self.navigator._wait_detail_or_error = Mock(return_value=(["营销详情", "用户推荐方案"], None))
        self.sleep = patch.object(automation.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    @staticmethod
    def snapshot(lines, position=0):
        return lines, tuple((text, f"bounds-{position}-{i}") for i, text in enumerate(lines))

    def test_loading_skeleton_including_footer_is_never_complete(self):
        headings = ["用户推荐方案", "推荐业务", "属地推荐方案"]
        self.navigator._detail_snapshot = Mock(return_value=self.snapshot(headings))
        with self.assertRaisesRegex(automation.NavigationError, "未确认详情底部"):
            self.navigator._collect_detail_text(max_scrolls=4)
        self.assertFalse(self.navigator._collection_complete)
        checkpoints = [c.args[0] for c in self.navigator.on_detail_progress.call_args_list]
        self.assertTrue(checkpoints)
        self.assertTrue(all(r["status"] == "partial" and not r["collection_complete"] for r in checkpoints))

    def test_query_heading_only_timeout_preserves_text_as_partial(self):
        self.navigator.config["timing"]["page_timeout_seconds"] = 0
        headings = ["用户推荐方案", "属地推荐方案"]
        self.navigator._detail_snapshot = Mock(return_value=self.snapshot(headings))
        result = self.navigator.query(self.PHONE)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["collection_complete"])
        self.assertIn("尚未获得正文", result["error"])
        self.assertIn("属地推荐方案", result["raw_text"])
        self.navigator._swipe_detail.assert_not_called()

    def test_later_snapshot_failure_keeps_every_collected_viewport_for_same_phone(self):
        first = ["用户推荐方案", "推荐方案甲", "提示：甲方案说明"]
        second = ["提示：甲方案说明", "推荐方案乙", "产品描述：乙方案说明"]
        self.navigator._wait_detail_or_error.return_value = (["营销详情", *first], None)
        self.navigator._detail_snapshot = Mock(side_effect=[
            self.snapshot(first), self.snapshot(first), self.snapshot(second, 1), RuntimeError("读取下一屏失败"),
        ])
        result = self.navigator.query(self.PHONE)
        self.assertEqual(result["phone"], self.PHONE)
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["collection_complete"])
        self.assertIn("读取下一屏失败", result["error"])
        self.assertEqual(result["raw_text"], "\n".join(["营销详情", *first, *second[1:]]))
        last_checkpoint = self.navigator.on_detail_progress.call_args.args[0]
        self.assertEqual(last_checkpoint["phone"], self.PHONE)
        self.assertEqual(last_checkpoint["raw_text"], result["raw_text"])

    def test_scroll_ceiling_keeps_all_text_but_never_reports_completion(self):
        frames = [["方案甲"], ["方案甲", "方案乙"], ["方案乙", "方案丙"]]
        self.navigator._detail_snapshot = Mock(side_effect=[self.snapshot(lines, i) for i, lines in enumerate(frames)])
        with self.assertRaisesRegex(automation.NavigationError, "2 次滚动上限"):
            self.navigator._collect_detail_text(max_scrolls=2)
        self.assertEqual(self.navigator._collected_detail, ["方案甲", "方案乙", "方案丙"])
        self.assertFalse(self.navigator._collection_complete)
        self.assertEqual(self.navigator._swipe_detail.call_count, 2)

    def test_footer_with_two_stable_viewports_completes_collection(self):
        first = self.snapshot(["用户推荐方案", "推荐方案甲"])
        footer = self.snapshot(["推荐方案甲", "产品描述：套餐内容", "属地推荐方案"], 1)
        self.navigator._detail_snapshot = Mock(side_effect=[first, footer, footer, footer])
        lines = self.navigator._collect_detail_text(max_scrolls=10)
        self.assertTrue(self.navigator._collection_complete)
        self.assertEqual(self.navigator._swipe_detail.call_count, 3)
        self.assertEqual(lines, ["用户推荐方案", "推荐方案甲", "产品描述：套餐内容", "属地推荐方案"])

    def test_same_footer_text_completes_despite_continuously_changing_bounds(self):
        first = self.snapshot(["用户推荐方案", "推荐方案甲"])
        footer = ["推荐方案甲", "产品描述：套餐内容", "属地推荐方案"]
        self.navigator._detail_snapshot = Mock(side_effect=[
            first, self.snapshot(footer, 1), self.snapshot(footer, 2), self.snapshot(footer, 3),
        ])
        result = self.navigator._collect_detail_text(max_scrolls=10)
        self.assertTrue(self.navigator._collection_complete)
        self.assertEqual(self.navigator._swipe_detail.call_count, 3)
        self.assertEqual(result, ["用户推荐方案", *footer])

    def test_alternating_clipped_footer_views_complete_when_no_new_text_is_added(self):
        full = ["推荐方案甲", "提示：套餐内容", "产品描述：完整条款", "属地推荐方案"]
        clipped = full[1:]
        self.navigator._detail_snapshot = Mock(side_effect=[
            self.snapshot(full, 0), self.snapshot(clipped, 1), self.snapshot(full, 2),
            AssertionError("已到达底部，不应继续无限滑动"),
        ])
        result = self.navigator._collect_detail_text(max_scrolls=10)
        self.assertTrue(self.navigator._collection_complete)
        self.assertEqual(result, full)
        self.assertEqual(self.navigator._swipe_detail.call_count, 2)
        self.assertEqual(self.navigator._detail_snapshot.call_count, 3)

    def test_alternating_clipped_views_without_footer_cannot_report_completion(self):
        full = ["推荐方案甲", "提示：套餐内容", "产品描述：完整条款"]
        clipped = full[1:]
        self.navigator._detail_snapshot = Mock(side_effect=[
            self.snapshot(full, 0), self.snapshot(clipped, 1), self.snapshot(full, 2),
        ])
        with self.assertRaisesRegex(automation.NavigationError, "尚未确认详情底部"):
            self.navigator._collect_detail_text(max_scrolls=2)
        self.assertFalse(self.navigator._collection_complete)
        self.assertEqual(self.navigator._collected_detail, full)

    def test_repeated_parent_child_label_at_same_bounds_is_deduplicated_only_once(self):
        root = ET.fromstring(
            '<hierarchy><node text="营销详情" />'
            '<node class="android.webkit.WebView" bounds="[0,200][1200,2300]">'
            '<node text="同名推荐方案" bounds="[20,300][1000,450]">'
            '<node content-desc="同名推荐方案" bounds="[20,300][1000,450]" />'
            '</node>'
            '<node text="同名推荐方案" bounds="[20,800][1000,950]" />'
            '</node></hierarchy>'
        )
        self.navigator._adb_ui_root = Mock(return_value=root)
        lines, fingerprint = self.navigator._detail_snapshot()
        self.assertEqual(lines, ["同名推荐方案", "同名推荐方案"])
        self.assertEqual(len(fingerprint), 2)
        self.assertNotEqual(fingerprint[0], fingerprint[1])

    def test_persistence_failure_is_rethrown_even_inside_collection(self):
        first = ["用户推荐方案", "推荐方案甲"]
        second = ["推荐方案甲", "产品描述：甲方案内容"]
        self.navigator._wait_detail_or_error.return_value = (["营销详情", *first], None)
        self.navigator._detail_snapshot = Mock(return_value=self.snapshot(second))
        failure = PersistenceFailure("Excel 保存失败")
        self.navigator.on_detail_progress.side_effect = [None, failure]
        with self.assertRaises(PersistenceFailure) as caught:
            self.navigator.query(self.PHONE)
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.navigator.on_detail_progress.call_count, 2)
        self.navigator._swipe_detail.assert_not_called()

    def test_initial_checkpoint_failure_prevents_any_detail_scroll(self):
        failure = PersistenceFailure("首次写入失败")
        self.navigator.on_detail_progress.side_effect = failure
        self.navigator._detail_snapshot = Mock()
        with self.assertRaises(PersistenceFailure) as caught:
            self.navigator.query(self.PHONE)
        self.assertIs(caught.exception, failure)
        self.navigator._detail_snapshot.assert_not_called()
        self.navigator._swipe_detail.assert_not_called()

    def test_phone_mismatch_never_clicks_jump_or_reads_another_customers_details(self):
        self.field.get_attribute.return_value = "13800138001"
        with self.assertRaisesRegex(automation.NavigationError, "号码不一致"):
            self.navigator.query(self.PHONE)
        self.field.send_keys.assert_called_once_with(self.PHONE)
        self.navigator._click_text.assert_not_called()
        self.navigator._wait_detail_or_error.assert_not_called()
        self.navigator.on_detail_progress.assert_not_called()

    def test_standard_android_error_dialog_is_recorded_as_number_error(self):
        del self.navigator._wait_detail_or_error
        self.navigator._click_if_present = Mock()
        root = ET.fromstring(
            '<hierarchy><node resource-id="android:id/alertTitle" text="提示" />'
            '<node resource-id="android:id/message" text="查询失败：该号码不存在" />'
            '<node resource-id="android:id/button1" text="确定" /></hierarchy>'
        )
        self.navigator._adb_ui_root = Mock(return_value=root)
        result = self.navigator.query(self.PHONE)
        self.assertEqual(result["phone"], self.PHONE)
        self.assertEqual(result["status"], "empty")
        self.assertIn("该号码不存在", result["error"])
        self.navigator._adb_ui_root.assert_called_once_with()
        self.navigator._click_if_present.assert_called_once_with("confirm_button", timeout=2)
        self.navigator.on_detail_progress.assert_not_called()
        self.navigator._swipe_detail.assert_not_called()

    def test_network_error_dialog_is_session_interruption_not_empty_number_result(self):
        del self.navigator._wait_detail_or_error
        self.navigator._click_if_present = Mock()
        self.navigator._adb_ui_root = Mock(return_value=ET.fromstring(
            '<hierarchy><node resource-id="android:id/alertTitle" text="查询失败" />'
            '<node resource-id="android:id/message" text="当前网络不可用，请检查网络环境" />'
            '<node resource-id="android:id/button1" text="确定" /></hierarchy>'
        ))
        with self.assertRaises(automation.NetworkUnavailableError):
            self.navigator.query(self.PHONE)
        self.navigator._adb_ui_root.assert_called_once_with()
        self.navigator._click_if_present.assert_not_called()
        self.navigator.on_detail_progress.assert_not_called()

    def test_network_loss_after_entering_details_preserves_data_and_requests_pause(self):
        self.navigator._wait_detail_or_error.return_value = (["营销详情", "推荐方案甲"], None)
        self.navigator._detail_snapshot = Mock(side_effect=automation.NetworkUnavailableError("当前网络不可用"))
        result = self.navigator.query(self.PHONE)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["session_interrupted"])
        self.assertIn("推荐方案甲", result["raw_text"])
        self.assertFalse(result["collection_complete"])

    def test_detail_collection_reads_sms_label_without_clicking_send_button(self):
        del self.navigator._wait_detail_or_error
        root = ET.fromstring(
            '<hierarchy><node text="营销详情" />'
            '<node class="android.webkit.WebView" bounds="[0,200][1200,2300]">'
            '<node text="用户推荐方案" bounds="[20,300][1000,350]" />'
            '<node text="推荐方案甲" bounds="[20,400][1000,500]" />'
            '<node text="发送短信" clickable="true" resource-id="send_sms" bounds="[900,400][1100,500]" />'
            '<node text="属地推荐方案" bounds="[20,1900][1000,2000]" />'
            '</node></hierarchy>'
        )
        self.navigator._adb_ui_root = Mock(return_value=root)
        self.navigator._click_element = Mock()
        self.navigator._adb_tap_target = Mock()
        self.navigator._click_if_present = Mock()
        result = self.navigator.query(self.PHONE)
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["collection_complete"])
        self.assertIn("发送短信", result["raw_text"])
        self.navigator._click_text.assert_called_once_with("jump_button", timeout=10)
        self.navigator._click_element.assert_not_called()
        self.navigator._adb_tap_target.assert_not_called()
        self.navigator._click_if_present.assert_not_called()
        self.navigator.driver.find_elements.assert_not_called()
        self.navigator.driver.execute_script.assert_not_called()

    def test_recommendation_text_with_error_word_is_not_mistaken_for_a_dialog(self):
        root = ET.fromstring(
            '<hierarchy><node text="营销详情" /><node text="产品描述：生效失败后联系营业厅" />'
            '<node text="属地推荐方案" /></hierarchy>'
        )
        self.assertIsNone(self.navigator._business_dialog_error(root))


if __name__ == "__main__":
    unittest.main()
