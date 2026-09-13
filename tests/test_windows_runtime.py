"""Windows transport regressions, using fake executables without a phone."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import appium_server
import device


class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.local = Path(self.temp.name) / "用户 & name" / "AppData" / "Local"
        self.local.mkdir(parents=True)
        platform = patch("sys.platform", "win32")
        platform.start()
        self.addCleanup(platform.stop)
        environ = patch.dict(os.environ, {"LOCALAPPDATA": str(self.local), "PATH": ""}, clear=True)
        environ.start()
        self.addCleanup(environ.stop)

    def file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_windows_sdk_is_found_without_environment_or_path(self):
        sdk = self.local / "Android/Sdk"
        adb = self.file(sdk / "platform-tools/adb.exe")
        with patch("shutil.which", return_value=None):
            self.assertEqual(device.resolve_adb(), str(adb))
            self.assertEqual(appium_server.configure_android_sdk(), str(sdk))
        self.assertEqual(os.environ["ANDROID_HOME"], str(sdk))
        self.assertEqual(os.environ["ANDROID_SDK_ROOT"], str(sdk))
        self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(adb.parent))

    def test_configured_windows_sdk_wins_over_default(self):
        configured = Path(self.temp.name) / "custom sdk"
        self.file(configured / "platform-tools/adb.exe")
        self.file(self.local / "Android/Sdk/platform-tools/adb.exe")
        os.environ["ANDROID_HOME"] = str(configured)
        with patch("shutil.which", return_value=None):
            self.assertEqual(appium_server.configure_android_sdk(), str(configured))

    def test_appium_uses_node_entry_without_cmd_shell_even_with_spaces(self):
        prefix = self.local / "MarketingAutomation/runtime/npm"
        shim = self.file(prefix / "appium.cmd")
        entry = self.file(prefix / "node_modules/appium/index.js")
        node = self.file(self.local / "Program Files/nodejs/node.exe")
        with patch("shutil.which", side_effect=lambda name: str(node) if name == "node.exe" else None):
            self.assertEqual(appium_server.resolve_appium(), str(shim))
            self.assertEqual(appium_server.appium_launch_command(str(shim)), [str(node), str(entry)])

    def test_configured_appium_prefix_wins_over_unrelated_global_install(self):
        prefix = self.local / "MarketingAutomation/runtime/npm"
        shim = self.file(prefix / "appium.cmd")
        os.environ["NPM_CONFIG_PREFIX"] = str(prefix)
        with patch("shutil.which", return_value="unrelated-appium.cmd"):
            self.assertEqual(appium_server.resolve_appium(), str(shim))

    def test_incomplete_npm_install_reports_repair_instead_of_launching_shell(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(appium_server.AppiumServerError, "run_windows.bat"):
                appium_server.appium_launch_command(str(self.local / "npm/appium.cmd"))

    def test_adb_reads_utf8_and_hides_windows_console(self):
        controller = device.AdbController()
        with patch("device.resolve_adb", return_value="adb.exe"), \
             patch("device.subprocess.run", return_value=Mock(returncode=0, stdout="中文页面", stderr="")) as run:
            self.assertEqual(controller.run("shell", "dumpsys"), "中文页面")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_only_owned_appium_process_tree_is_stopped(self):
        server = appium_server.AppiumServer()
        with patch("appium_server.subprocess.run") as run:
            server.stop()
            run.assert_not_called()
            process = Mock(pid=321)
            process.poll.side_effect = [None, 0]
            server.process = process
            server.stop()
        self.assertEqual(run.call_args.args[0], ["taskkill.exe", "/PID", "321", "/T", "/F"])
        process.terminate.assert_not_called()
        process.wait.assert_called_once_with(timeout=5)
        self.assertIsNone(server.process)

    def test_failed_taskkill_falls_back_to_owned_process(self):
        server = appium_server.AppiumServer()
        process = Mock(pid=321)
        process.poll.return_value = None
        server.process = process
        with patch("appium_server.subprocess.run", side_effect=subprocess.TimeoutExpired("taskkill", 5)):
            server.stop()
        process.terminate.assert_called_once()
        self.assertIsNone(server.process)


if __name__ == "__main__":
    unittest.main()
