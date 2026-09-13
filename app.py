from __future__ import annotations

import json
import random
import re
import sys
import threading
import time
import warnings
from datetime import datetime
from pathlib import Path

# urllib3 2.x emits this advisory on the system Python shipped with some
# macOS versions (LibreSSL); the desktop app does not use TLS-specific APIs.
warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from automation import ManualLoginRequired, MarketingAutomation, NavigationError, NetworkUnavailableError
from appium_server import AppiumServer
from device import AdbController, AdbError, device_connection_message
from excel_io import (append_result_journal, create_input_template, load_existing_results,
                      load_result_journal, read_input, write_result)


ROOT = Path(__file__).resolve().parent
DEFAULT_APK = ROOT / "wgt.apk"


def readable_automation_error(error: Exception) -> str:
    raw = re.sub(r"(?<!\d)(1\d{2})\d{4}(\d{4})(?!\d)", r"\1****\2", str(error))
    if "INSTALL_FAILED_ABORTED" in raw or "User rejected permissions" in raw:
        return "手机端弹出了 UiAutomator2 辅助服务安装确认，但安装未获批准。请保持手机解锁，在弹窗中点击“了解风险/允许安装”（不要点取消），并在荣耀/MagicOS 开发者选项开启 USB 调试及允许通过 USB 安装后重试。"
    if "INSTALL_FAILED_USER_RESTRICTED" in raw or "Install canceled by user" in raw:
        return "手机阻止了 Appium 辅助服务安装。请在荣耀/MagicOS 开发者选项开启 USB 调试及允许通过 USB 安装，保持手机解锁并确认安装授权弹窗后重试。"
    if "uiautomator2ServerInstallTimeout" in raw or "timed out after 20000ms" in raw:
        return "手机端 UiAutomator2 辅助服务安装超时。请保持手机解锁并确认安装弹窗，检查 USB 线连接后重试；新版已将安装等待时间延长到 120 秒。"
    if "WRITE_SECURE_SETTINGS" in raw or "hidden_api_policy" in raw:
        return "手机系统拒绝隐藏 API 设置，已启用兼容模式；请重新运行任务。"
    if "ANDROID_HOME" in raw or "ANDROID_SDK_ROOT" in raw:
        launcher = "run_windows.bat" if sys.platform == "win32" else "run_mac.command"
        return f"Appium 找不到 Android SDK；请关闭应用后重新运行 {launcher}。"
    return raw.split("Stacktrace:", 1)[0].strip()[:1200] or type(error).__name__


class ResultPersistenceError(RuntimeError):
    """Stop phone operations when a durable result cannot be saved."""

    is_persistence_error = True


class BatchWorker(QObject):
    progress = Signal(int, int, str)
    statistics = Signal(int, int, int, int)
    message = Signal(str)
    manual_login = Signal(str)
    finished = Signal()

    def __init__(self, records, config, output_path):
        super().__init__()
        self.records = list(records[:1] if config.get("single_record_trial", False) else records)
        self.config = config
        self.output_path = Path(output_path)
        self.results = {}
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.had_error = False
        self.output_saved = False
        self.saved_count = 0
        self.attempted_count = 0
        self.success_count = 0
        self.empty_count = 0
        self.failure_count = 0
        self.skipped_count = 0
        self.end_reason = ""
        self.save_failed = False
        self.journal_saved = False
        self.current_phone = None
        self.detail_snapshot = None
        self.detail_excel_saved = False

    def _emit_statistics(self):
        self.statistics.emit(self.success_count, self.empty_count, self.failure_count, self.skipped_count)

    def completion_message(self):
        counts = f"成功 {self.success_count}，空结果 {self.empty_count}，失败 {self.failure_count}，跳过 {self.skipped_count}"
        reason = self.end_reason or ("已停止" if self.stop_event.is_set() else "任务结束")
        if self.save_failed:
            recovery = f"采集内容已保留在恢复日志：{self.output_path.with_suffix('.jsonl')}" if self.journal_saved else "无法写入恢复日志，请检查目录权限和磁盘空间"
            return f"{reason} · {counts}。Excel 未完成更新；{recovery}。"
        if self.output_saved:
            return f"{reason} · {counts}。本次已写入 {self.saved_count} 条结果：{self.output_path}"
        return f"{reason} · {counts}。本次未写入结果文件。"

    def _save_excel(self):
        try:
            write_result(self.output_path, self.records, self.results)
        except Exception as exc:
            self.save_failed = True
            raise ResultPersistenceError(f"Excel 保存失败，请关闭占用该文件的 Excel 窗口并检查目录权限和磁盘空间：{exc}") from exc
        self.output_saved = True
        self.saved_count = sum(record.phone in self.results for record in self.records)

    def _journal_result(self, phone, result):
        try:
            append_result_journal(self.output_path, phone, result)
            self.journal_saved = True
        except Exception as exc:
            self.save_failed = True
            raise ResultPersistenceError(f"结果恢复日志保存失败，已停止手机操作：{exc}") from exc

    def _on_detail_progress(self, result):
        if not isinstance(result, dict) or result.get("phone") != self.current_phone:
            raise ResultPersistenceError("营销详情与当前查询号码不一致，已停止以免保存到错误号码")
        snapshot = dict(result)
        snapshot["status"] = "partial"
        snapshot.setdefault("query_time", datetime.now().isoformat(sep=" ", timespec="seconds"))
        self._journal_result(self.current_phone, snapshot)
        self.detail_snapshot = snapshot
        self.results[self.current_phone] = snapshot
        if not self.detail_excel_saved:
            self._save_excel()
            self.detail_excel_saved = True
            self.message.emit(f"已保存详情初始内容，号码后四位 {self.current_phone[-4:]}；继续向下读取")

    def _ready_to_query(self):
        self.pause_event.wait()
        return not self.stop_event.is_set()

    @Slot()
    def run(self):
        appium_server = None
        automation = None
        stage = "任务启动"
        try:
            if not self._ready_to_query():
                return
            journal_results = load_result_journal(self.output_path)
            try:
                self.results.update(load_existing_results(self.output_path))
            except Exception:
                if not journal_results:
                    raise
                self.message.emit("原结果 Excel 无法读取，正在从恢复日志重新生成")
            allowed_phones = {record.phone for record in self.records}
            self.results.update(journal_results)
            self.results = {phone: value for phone, value in self.results.items() if phone in allowed_phones}
            if journal_results:
                self.journal_saved = True
                stage = "恢复结果保存"
                self._save_excel()
            appium_server = AppiumServer(self.config.get("appium_url", "http://127.0.0.1:4723/wd/hub"), self.message.emit)
            automation = MarketingAutomation(self.config, self.output_path.parent, self.message.emit)
            automation.on_detail_progress = self._on_detail_progress
            # Let transient network retry backoffs honor the same pause/stop
            # events as the normal between-record and batch waits.  The
            # automation layer still falls back to time.sleep for standalone
            # callers that do not provide this callback.
            self.config.setdefault("timing", {})["retry_wait"] = self._interruptible_sleep
            appium_server.ensure()
            if not self._ready_to_query():
                return
            # Do not relaunch the APK from a batch run. Relaunching a cold app
            # can legitimately show its LoginActivity even when the user had
            # already authenticated in the previous desktop session. The
            # user can use “唤醒应用” first when the task is in the background.
            self.message.emit("保留手机当前应用页面，不主动重启登录流程")
            self.message.emit("正在建立手机自动化连接…")
            automation.connect()
            self.message.emit("手机自动化已连接，开始检查页面与查询入口")
            timing = self.config.get("timing", {})
            base_delay = float(timing.get("between_records_seconds", 30))
            jitter = float(timing.get("random_jitter_seconds", 5))
            pause_every = int(timing.get("pause_every_records", 50))
            pause_seconds = int(timing.get("pause_minutes", 2) * 60)
            consecutive_failures = 0
            for index, record in enumerate(self.records, start=1):
                if not self._ready_to_query():
                    break
                if record.phone in self.results and self.results[record.phone].get("status") in {"success", "empty"}:
                    self.skipped_count += 1
                    self.message.emit(f"跳过已完成号码，后四位：{record.phone[-4:]}")
                    self._emit_statistics()
                    self.progress.emit(index, len(self.records), record.phone[-4:])
                    continue
                stage = "查询"
                self.attempted_count += 1
                self.current_phone = record.phone
                previous_result = self.results.get(record.phone, {})
                self.detail_snapshot = None
                self.detail_excel_saved = False
                abort_reason = ""
                self.progress.emit(index - 1, len(self.records), record.phone[-4:])
                self.message.emit(f"正在查询 {index}/{len(self.records)}，号码后四位 {record.phone[-4:]}")
                try:
                    result = automation.query(record.phone)
                    if self.save_failed:
                        raise ResultPersistenceError("采集过程中保存失败，已停止；请修复文件占用或目录权限后重新开始")
                    if not isinstance(result, dict) or "status" not in result:
                        raise RuntimeError("查询未返回有效结果，已停止以免误报成功")
                except ResultPersistenceError:
                    stage = "结果保存"
                    raise
                except (ManualLoginRequired, NetworkUnavailableError) as exc:
                    # Login/network expiry is a session-level interruption, not
                    # a bad phone number. Keep the row resumable and stop the
                    # batch so the user can log in again safely.
                    error = readable_automation_error(exc)
                    self.manual_login.emit(error)
                    result = {"status": "login_required", "items": [], "error": error}
                    abort_reason = "检测到登录或网络失效，任务已暂停；请在手机重新登录后再次点击“开始处理”，当前号码将重新处理"
                except Exception as exc:
                    result = {"status": "failed", "items": [], "error": readable_automation_error(exc)}
                    if isinstance(exc, NavigationError):
                        abort_reason = "页面操作未通过验证，任务已停止"
                # An exception after entering details must not replace captured
                # text with an empty error row. Earlier interrupted text also
                # survives a retry that fails before opening details again.
                captured = self.detail_snapshot or previous_result
                if not result.get("raw_text") and captured.get("raw_text"):
                    result = {**captured, **result, "raw_text": captured["raw_text"],
                              "items": captured.get("items", []), "status": "partial",
                              "collection_complete": False}
                result.setdefault("query_time", datetime.now().isoformat(sep=" ", timespec="seconds"))
                if result.get("status") == "success" and result.get("collection_complete") is False:
                    result["status"] = "partial"
                    result.setdefault("error", "详情已保留，但尚未确认采集到页面底部")
                if result.get("session_interrupted"):
                    self.manual_login.emit(result.get("error") or "手机登录或网络失效")
                    abort_reason = "检测到登录或网络失效，已保存采集内容并暂停；重新登录后再次开始将重试当前号码"
                status = result.get("status")
                stage = "结果保存"
                self.results[record.phone] = result
                self._journal_result(record.phone, result)
                self._save_excel()
                # Only report success after both the journal and Excel commit.
                if status == "success":
                    self.success_count += 1
                    consecutive_failures = 0
                    self.message.emit(f"查询成功并已保存，号码后四位 {record.phone[-4:]}，推荐 {len(result.get('items', []))} 条")
                elif status == "empty":
                    self.empty_count += 1
                    consecutive_failures = 0
                    self.message.emit(f"查询完成并已保存，号码后四位 {record.phone[-4:]}：{result.get('error') or '页面明确显示无推荐'}")
                else:
                    self.had_error = True
                    self.failure_count += 1
                    consecutive_failures += 1
                    error = readable_automation_error(RuntimeError(result.get("error") or f"状态异常：{status}"))
                    if status == "partial":
                        self.message.emit(f"详情部分内容已保存，号码后四位 {record.phone[-4:]}：{error}")
                    else:
                        self.message.emit(f"查询失败，号码后四位 {record.phone[-4:]}：{error}")
                    if not abort_reason and self.attempted_count == 1 and status != "partial":
                        abort_reason = "首条查询失败，已停止；修复后先完成单条试运行"
                    elif not abort_reason and consecutive_failures >= 3:
                        abort_reason = "连续 3 条查询未完整完成，已停止；请检查手机页面和网络"
                self._emit_statistics()
                self.progress.emit(index, len(self.records), record.phone[-4:])
                if abort_reason:
                    self.end_reason = abort_reason
                    self.message.emit(abort_reason)
                    break
                if self.stop_event.is_set():
                    break
                if index < len(self.records):
                    delay = base_delay + random.uniform(0, jitter)
                    self._interruptible_sleep(delay)
                if not self.stop_event.is_set() and pause_every and self.attempted_count % pause_every == 0 and index < len(self.records):
                    self.message.emit(f"已处理 {self.attempted_count} 条，批次暂停 {pause_seconds // 60} 分钟")
                    self._interruptible_sleep(pause_seconds)
        except Exception as exc:
            self.had_error = True
            self.end_reason = f"{stage}失败，任务已停止"
            self.message.emit(f"{self.end_reason}：{readable_automation_error(exc)}")
        finally:
            try:
                if automation is not None:
                    try:
                        automation.close()
                    except Exception as exc:
                        self.message.emit(f"关闭手机自动化连接时出错：{readable_automation_error(exc)}")
                if appium_server is not None:
                    try:
                        appium_server.stop()
                    except Exception as exc:
                        self.message.emit(f"关闭 Appium 服务时出错：{readable_automation_error(exc)}")
            finally:
                self.finished.emit()

    def _interruptible_sleep(self, seconds: float):
        end = time.monotonic() + max(0, seconds)
        while time.monotonic() < end:
            if not self._ready_to_query():
                return
            self.stop_event.wait(min(0.5, max(0, end - time.monotonic())))

    def pause(self):
        self.pause_event.clear()

    def resume(self):
        self.pause_event.set()

    def stop(self):
        self.stop_event.set()
        self.pause_event.set()


class MainWindow(QMainWindow):
    VERSION = "v1.1"

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"营销详情批量查询 {self.VERSION}")
        self.resize(1020, 780)
        # Keep enough vertical room for the three cards and the log panel so
        # Qt never compresses their child controls into clipped rows.
        self.setMinimumSize(860, 760)
        self.thread = None
        self.worker = None
        self.records = []
        self.results_path = None
        self._resume_signature = None
        self._resume_output_path = None
        self._active_signature = None
        self.adb = AdbController()
        self.open_result_button = None
        self._build_ui()

    def _build_ui(self):
        root = QWidget(); root.setObjectName("root")
        layout = QVBoxLayout(root); layout.setContentsMargins(28, 24, 28, 22); layout.setSpacing(14)
        title = QLabel(f"营销详情批量查询  {self.VERSION}")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        files = QGroupBox("①  数据与应用")
        form = QFormLayout(files); form.setContentsMargins(18, 18, 18, 16); form.setVerticalSpacing(12); form.setHorizontalSpacing(18)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.excel_edit = QLineEdit()
        self.excel_edit.setPlaceholderText("选择手机号 Excel（支持工作簿200模板）")
        self.excel_edit.setMinimumHeight(40)
        excel_button = QPushButton("选择 Excel")
        excel_button.clicked.connect(self.choose_excel)
        template_button = QPushButton("下载 Excel 模板")
        template_button.clicked.connect(self.download_template)
        form.addRow("手机号 Excel", self._with_buttons(self.excel_edit, excel_button, template_button))
        self.apk_edit = QLineEdit(str(DEFAULT_APK) if DEFAULT_APK.exists() else "")
        self.apk_edit.setPlaceholderText("可选：选择 APK 文件进行安装")
        self.apk_edit.setMinimumHeight(40)
        apk_button = QPushButton("选择 APK")
        apk_button.clicked.connect(self.choose_apk)
        install_button = QPushButton("安装 APK")
        install_button.clicked.connect(self.install_apk)
        form.addRow("安卓 APK", self._with_buttons(self.apk_edit, apk_button, install_button))
        layout.addWidget(files)

        device = QGroupBox("②  设备连接")
        device_layout = QHBoxLayout(device); device_layout.setContentsMargins(18, 14, 18, 14); device_layout.setSpacing(10)
        self.device_label = QLabel("尚未检测设备")
        self.device_label.setObjectName("deviceStatus")
        self.device_label.setProperty("connected", False)
        self.device_label.setWordWrap(True)
        detect = QPushButton("检测设备")
        detect.clicked.connect(self.detect_device)
        launch = QPushButton("唤醒应用")
        launch.setToolTip("唤醒手机上已有的应用任务，不主动重启登录页")
        launch.clicked.connect(self.launch_app)
        device_layout.addWidget(self.device_label, 1)
        device_layout.addWidget(detect)
        device_layout.addWidget(launch)
        layout.addWidget(device)

        settings = QGroupBox("③  执行策略")
        settings_form = QGridLayout(settings); settings_form.setContentsMargins(18, 16, 18, 16); settings_form.setHorizontalSpacing(24); settings_form.setVerticalSpacing(12)
        self.interval = QSpinBox(); self.interval.setRange(1, 300); self.interval.setValue(30); self.interval.setSuffix(" 秒")
        self.interval.setToolTip("默认 30 秒；建议批量查询保持 30 秒以上，降低业务接口限流概率")
        self.jitter = QSpinBox(); self.jitter.setRange(0, 60); self.jitter.setValue(5); self.jitter.setSuffix(" 秒")
        self.jitter.setToolTip("每条间隔在基础值上随机增加 0 至设定秒数")
        self.batch_count = QSpinBox(); self.batch_count.setRange(0, 10000); self.batch_count.setValue(50); self.batch_count.setSuffix(" 条，0 表示不暂停")
        self.batch_pause = QSpinBox(); self.batch_pause.setRange(0, 120); self.batch_pause.setValue(2); self.batch_pause.setSuffix(" 分钟")
        settings_form.addWidget(QLabel("每条基础间隔"), 0, 0); settings_form.addWidget(self.interval, 0, 1)
        settings_form.addWidget(QLabel("随机增加"), 0, 2); settings_form.addWidget(self.jitter, 0, 3)
        settings_form.addWidget(QLabel("批次暂停"), 1, 0); settings_form.addWidget(self.batch_count, 1, 1)
        settings_form.addWidget(QLabel("暂停时长"), 1, 2); settings_form.addWidget(self.batch_pause, 1, 3)
        self.trial_checkbox = QCheckBox("试运行 1 个号码（确认无误后再批量；网络异常自动重试 2 次）")
        self.trial_checkbox.setChecked(True)
        settings_form.addWidget(self.trial_checkbox, 2, 0, 1, 4)
        layout.addWidget(settings)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        help_button = QPushButton("操作指引 / 常见问题")
        help_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "README.md"))))
        self.start_button = QPushButton("开始处理")
        self.start_button.setObjectName("primaryButton")
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self.start_batch)
        self.pause_button = QPushButton("暂停")
        self.pause_button.setEnabled(False)
        self.pause_button.clicked.connect(self.toggle_pause)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_batch)
        self.open_result_button = QPushButton("打开结果 Excel")
        self.open_result_button.setEnabled(False)
        self.open_result_button.clicked.connect(self.open_result)
        actions.addWidget(self.start_button)
        actions.addWidget(self.pause_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.open_result_button)
        actions.addStretch(1)
        actions.addWidget(help_button)
        layout.addLayout(actions)

        self.progress = QProgressBar(); self.progress.setValue(0); self.progress.setTextVisible(False); self.progress.setFixedHeight(8)
        self.progress_label = QLabel("等待开始"); self.progress_label.setObjectName("progressLabel")
        self.statistics_label = QLabel("成功 0   ·   空结果 0   ·   失败 0   ·   跳过 0"); self.statistics_label.setObjectName("statistics")
        layout.addWidget(self.progress)
        info_row = QHBoxLayout(); info_row.addWidget(self.progress_label); info_row.addStretch(1); info_row.addWidget(self.statistics_label)
        layout.addLayout(info_row)
        log_title = QLabel("运行日志"); log_title.setObjectName("sectionLabel")
        layout.addWidget(log_title)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setPlaceholderText("准备就绪后，运行过程会显示在这里"); self.log.setMinimumHeight(150); self.log.setMaximumHeight(230)
        layout.addWidget(self.log, 1)
        self.setCentralWidget(root)

    @staticmethod
    def _with_button(line, button):
        box = QWidget(); box.setMinimumHeight(42)
        row = QHBoxLayout(box); row.setContentsMargins(0, 0, 0, 0); row.addWidget(line, 1); row.addWidget(button); return box

    @staticmethod
    def _with_buttons(line, *buttons):
        box = QWidget(); box.setMinimumHeight(42)
        row = QHBoxLayout(box); row.setContentsMargins(0, 0, 0, 0); row.addWidget(line, 1)
        for button in buttons: row.addWidget(button)
        return box

    def choose_excel(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择手机号 Excel", "", "Excel 文件 (*.xlsx)")
        if path:
            self.excel_edit.setText(path)

    def download_template(self):
        path, _ = QFileDialog.getSaveFileName(self, "保存手机号 Excel 模板", str(Path.home() / "手机号码模板.xlsx"), "Excel 文件 (*.xlsx)")
        if not path:
            return
        try:
            template = create_input_template(path)
            self.excel_edit.setText(str(template))
            self.append_log(f"Excel 模板已保存：{template}")
            QMessageBox.information(self, "模板已保存", f"模板已保存到：\n{template}\n\n请在“手机号清单”第 2 行开始填写号码。")
        except Exception as exc:
            QMessageBox.critical(self, "模板生成失败", str(exc))

    def choose_apk(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 APK", "", "Android APK (*.apk *.1)")
        if path:
            self.apk_edit.setText(path)

    def detect_device(self):
        try:
            devices = self.adb.devices()
            self.device_label.setText(device_connection_message(devices))
            self.device_label.setProperty("connected", bool(devices and any(state == "device" for _, state in devices)))
            self.device_label.style().unpolish(self.device_label); self.device_label.style().polish(self.device_label)
            self.append_log(f"ADB 路径：{self.adb.adb}")
        except Exception as exc:
            self.device_label.setText(f"检测失败：{exc}")
            self.device_label.setProperty("connected", False)
            self.device_label.style().unpolish(self.device_label); self.device_label.style().polish(self.device_label)

    def install_apk(self):
        try:
            self.adb.install(self.apk_edit.text())
            self.append_log("APK 安装完成")
        except Exception as exc:
            QMessageBox.critical(self, "安装失败", str(exc))

    def launch_app(self):
        try:
            self.adb.resume("com.sh.cm.grid4a")
            self.append_log("已唤醒手机上现有应用页面，未重置登录状态")
        except Exception as exc:
            QMessageBox.critical(self, "启动失败", str(exc))

    def start_batch(self):
        if self.thread is not None and self.thread.isRunning():
            return
        if not self.excel_edit.text().strip():
            QMessageBox.warning(self, "缺少 Excel", "请先选择手机号 Excel")
            return
        try:
            self.records = read_input(self.excel_edit.text())
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", str(exc)); return
        if not self.records:
            QMessageBox.warning(self, "没有有效手机号", "请确认手机号列为 11 位数字")
            return
        imported_count = len(self.records)
        trial = self.trial_checkbox.isChecked()
        source = Path(self.excel_edit.text()).expanduser().resolve()
        self._active_signature = (str(source), trial,
                                  tuple((record.row_number, record.phone, record.name) for record in self.records))
        if trial:
            self.records = self.records[:1]
        mode = "试运行" if trial else "营销结果"
        if self._active_signature == self._resume_signature and self._resume_output_path:
            self.results_path = self._resume_output_path
            self.append_log("继续本窗口的未完成任务，跳过已完成号码，重试未完成号码")
        else:
            self.results_path = source.with_name(f"{source.stem}_{mode}_{datetime.now():%Y%m%d_%H%M%S_%f}.xlsx")
        config = {
            "single_record_trial": trial,
            "check_login_state": False,
            "app_package": "com.sh.cm.grid4a",
            "app_activity": "com.sh.cm.grid4a.SplashActivity",
            "appium_url": "http://127.0.0.1:4723/wd/hub",
            "selectors": {"home_marker": "个人业务", "smart_marketing_tab": "智慧营销", "full_view_marker": "全景视图", "login_marker": "立即登录", "marketing_entry": "营销助手(免签入)", "phone_input_hint": "手机号码", "jump_button": "跳转", "confirm_button": "确定", "detail_marker": "营销详情"},
            "timing": {"between_records_seconds": self.interval.value(), "random_jitter_seconds": self.jitter.value(), "pause_every_records": self.batch_count.value(), "pause_minutes": self.batch_pause.value(), "page_timeout_seconds": 60, "uiautomator2_server_read_timeout_seconds": 60, "appium_command_timeout_seconds": 75, "network_retries": 2, "network_retry_seconds": 15},
        }
        self.thread = QThread(self); self.worker = BatchWorker(self.records, config, self.results_path)
        self.worker.moveToThread(self.thread); self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.on_progress); self.worker.message.connect(self.append_log); self.worker.manual_login.connect(self.on_manual_login)
        self.worker.statistics.connect(self.on_statistics)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.on_finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.start_button.setEnabled(False); self.pause_button.setEnabled(True); self.stop_button.setEnabled(True)
        if self.open_result_button:
            self.open_result_button.setEnabled(False)
        self.trial_checkbox.setEnabled(False)
        self.on_statistics(0, 0, 0, 0)
        self.progress.setMaximum(len(self.records)); self.progress.setValue(0)
        self.progress_label.setText("正在连接自动化服务…")
        self.append_log(f"导入 {imported_count} 个号码，本次{'试运行' if trial else '处理'} {len(self.records)} 个；输出：{self.results_path}")
        self.thread.start()

    def toggle_pause(self):
        if not self.worker: return
        if self.pause_button.text() == "暂停": self.worker.pause(); self.pause_button.setText("继续"); self.append_log("已暂停")
        else: self.worker.resume(); self.pause_button.setText("暂停"); self.append_log("继续处理")

    def stop_batch(self):
        if self.worker: self.worker.stop(); self.append_log("正在停止，当前记录完成后退出")

    @Slot(int, int, str)
    def on_progress(self, done, total, suffix):
        self.progress.setMaximum(total); self.progress.setValue(done); self.progress_label.setText(f"已处理 {done}/{total} · 当前号码后四位 {suffix}")

    @Slot(int, int, int, int)
    def on_statistics(self, success, empty, failed, skipped):
        self.statistics_label.setText(f"成功 {success} · 空结果 {empty} · 失败 {failed} · 跳过 {skipped}")

    def on_manual_login(self, message):
        self.append_log(message)
        if "网络" in message and "登录凭证" not in message:
            title = "网络异常，任务已暂停"
            action = "请恢复手机业务网络；如果应用仍提示异常，再重新登录。"
        else:
            title = "需要人工登录"
            action = "请在手机上重新登录，完成后回到本窗口再次点击“开始处理”。"
        QMessageBox.information(self, title, f"{message}\n\n{action}")

    def on_finished(self):
        self.start_button.setEnabled(True); self.pause_button.setEnabled(False); self.stop_button.setEnabled(False); self.pause_button.setText("暂停")
        self.trial_checkbox.setEnabled(True)
        if self.open_result_button:
            self.open_result_button.setEnabled(bool(self.results_path and self.results_path.exists()))
        if self.worker:
            incomplete = self.worker.save_failed or any(
                self.worker.results.get(record.phone, {}).get("status") not in {"success", "empty"}
                for record in self.records
            )
            self._resume_signature = self._active_signature if incomplete else None
            self._resume_output_path = self.results_path if incomplete else None
            self.append_log(self.worker.completion_message())
            self.progress_label.setText(self.worker.end_reason or ("已停止" if self.worker.stop_event.is_set() else "任务结束"))
        self.thread = None

    def open_result(self):
        if self.results_path and self.results_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.results_path)))

    def closeEvent(self, event):
        if self.thread is not None and self.thread.isRunning():
            self.stop_batch()
            self.append_log("正在结束手机连接，任务停止后可关闭窗口")
            event.ignore()
            return
        event.accept()

    def append_log(self, text):
        self.log.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet("""
        * { font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 13px; }
        QMainWindow, QWidget#root { background: #0f172a; color: #e5e7eb; }
        QLabel { color: #d7deea; font-size: 13px; }
        QLabel#pageTitle { color: #f8fafc; font-size: 26px; font-weight: 750; padding: 2px 0 7px 0; }
        QLabel#sectionLabel { color: #94a3b8; font-size: 12px; font-weight: 700; padding-top: 2px; }
        QLabel#deviceStatus { color: #94a3b8; background: #111c2f; border: 1px solid #26364e; border-radius: 7px; padding: 9px 12px; }
        QLabel#deviceStatus[connected="true"] { color: #86efac; background: #132e2a; border-color: #1d5145; }
        QLabel#progressLabel { color: #cbd5e1; font-size: 12px; }
        QLabel#statistics { color: #94a3b8; font-size: 12px; }
        QGroupBox { background: #172235; border: 1px solid #26364e; border-radius: 12px; margin-top: 14px; padding-top: 8px; font-size: 13px; font-weight: 700; color: #cbd5e1; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 7px; color: #dbeafe; background: #0f172a; }
        QLineEdit, QSpinBox { background: #0e1728; color: #f8fafc; border: 1px solid #334155; border-radius: 7px; padding: 0 10px; selection-background-color: #2563eb; font-size: 13px; }
        QLineEdit { min-height: 40px; }
        QSpinBox { min-height: 40px; padding: 0 28px 0 10px; }
        QSpinBox QLineEdit { background: transparent; color: #f8fafc; border: 0; padding: 0; }
        QLineEdit:focus, QSpinBox:focus { border: 1px solid #60a5fa; }
        QLineEdit:disabled, QSpinBox:disabled { color: #64748b; background: #111a2a; }
        QPushButton { background: #26364e; color: #e2e8f0; border: 1px solid #3a4c67; border-radius: 7px; min-height: 40px; padding: 0 14px; font-size: 13px; font-weight: 650; }
        QPushButton:hover { background: #334967; border-color: #5b78a0; }
        QPushButton:pressed { background: #1e2c42; }
        QPushButton:disabled { background: #1a2638; color: #58677c; border-color: #27364b; }
        QPushButton#primaryButton { background: #2563eb; border-color: #3b82f6; color: white; padding: 9px 20px; }
        QPushButton#primaryButton:hover { background: #3b82f6; }
        QPushButton#dangerButton { color: #fecaca; border-color: #713f46; }
        QPushButton#dangerButton:hover { background: #552b36; border-color: #a34b58; }
        QCheckBox { color: #cbd5e1; spacing: 8px; padding-top: 3px; }
        QCheckBox::indicator { width: 17px; height: 17px; }
        QProgressBar { background: #111c2f; border: 0; border-radius: 4px; }
        QProgressBar::chunk { background: #3b82f6; border-radius: 4px; }
        QPlainTextEdit { background: #0b1220; color: #a9bdd6; border: 1px solid #26364e; border-radius: 9px; padding: 10px; font-family: Menlo, Monaco, monospace; font-size: 11px; }
        QScrollBar:vertical { background: #111c2f; width: 10px; margin: 2px; }
        QScrollBar::handle:vertical { background: #334967; border-radius: 5px; min-height: 24px; }
    """)
    window = MainWindow(); window.show()
    sys.exit(app.exec())
