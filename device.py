from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class AdbError(RuntimeError):
    pass


def subprocess_options() -> dict:
    """Keep background ADB/Node commands from flashing console windows."""
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}


def adb_candidates() -> list[Path]:
    """Search standard SDK locations even when Finder omits shell PATH entries."""
    executable = "adb.exe" if sys.platform == "win32" else "adb"
    candidates = []
    for variable in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(variable)
        if sdk:
            candidates.append(Path(sdk).expanduser() / "platform-tools" / executable)
    user_home = Path.home()
    candidates.extend([
        user_home / "Library/Android/sdk/platform-tools" / executable,
        user_home / "Android/Sdk/platform-tools" / executable,
        user_home / "AppData/Local/Android/Sdk/platform-tools" / executable,
    ])
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk/platform-tools" / executable)
    if sys.platform != "win32":
        candidates.extend([Path("/opt/homebrew/bin/adb"), Path("/usr/local/bin/adb")])
    return candidates


def resolve_adb(command: str = "adb") -> str:
    requested = os.environ.get("ADB_PATH") or command
    on_path = shutil.which(str(Path(requested).expanduser()))
    if on_path:
        return on_path
    if requested not in {"adb", "adb.exe"}:
        raise AdbError("指定的 ADB 路径不可执行，请检查 ADB_PATH 或 ADB 路径配置。")
    for candidate in adb_candidates():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise AdbError(
        "电脑未找到 ADB（Platform-Tools）。请先安装：macOS 可运行 "
        "brew install --cask android-platform-tools；Windows 请运行 setup_windows.ps1。"
        "安装后再次点击“检测设备”。"
    )


def device_connection_message(devices: list[tuple[str, str]]) -> str:
    if not devices:
        computer_hint = ("请检查 Windows 设备管理器中的 Android ADB 驱动，"
                         if sys.platform == "win32" else "请在 Mac 配件弹窗点“允许”，")
        return (
            "ADB 已就绪，尚未发现手机。" + computer_hint +
            "并打开手机 USB 调试、确认数据线支持传输后重新检测。"
        )
    hints = {
        "device": "已连接，USB 调试授权正常",
        "unauthorized": "等待手机授权：解锁手机并点击“允许 USB 调试”，然后重新检测",
        "offline": "设备离线：重新插拔数据线并检查手机 USB 调试，然后重新检测",
    }
    messages = [f"{serial} · {hints.get(state, '设备状态：' + state)}" for serial, state in devices]
    if len(devices) > 1:
        messages.append("检测到多台设备，第一版请只保留一台连接。")
    return "\n".join(messages)


class AdbController:
    def __init__(self, adb: str = "adb") -> None:
        self._command = adb

    @property
    def adb(self) -> str:
        # Resolve on each operation so installing ADB does not require a restart.
        return resolve_adb(self._command)

    def run(self, *args: str, timeout: int = 30) -> str:
        try:
            proc = subprocess.run([self.adb, *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout,
                                  **subprocess_options())
        except FileNotFoundError as exc:
            raise AdbError("ADB 文件不存在，请重新安装 Platform-Tools 后再次检测。") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError("ADB 响应超时，请检查手机授权、重新连接数据线后再次检测。") from exc
        if proc.returncode:
            raise AdbError(proc.stderr.strip() or proc.stdout.strip() or "adb 命令失败")
        return proc.stdout.strip()

    def devices(self) -> list[tuple[str, str]]:
        lines = [line for line in self.run("devices", timeout=10).splitlines()
                 if line.strip() and not line.startswith(("List of devices", "*"))]
        return [(parts[0], parts[1] if len(parts) > 1 else "") for line in lines if (parts := line.split())]

    def install(self, apk: str | Path) -> str:
        return self.run("install", "-r", str(apk), timeout=180)

    def launch(self, package: str, activity: str) -> str:
        return self.run("shell", "am", "start", "-n", f"{package}/{activity}")

    def resume(self, package: str) -> str:
        """Bring an existing task forward without deliberately starting SplashActivity.

        Reordering the activity that is already in the task preserves the
        current authenticated page. The launcher/monkey fallback is used only
        when Android has no existing activity to reorder (for example after a
        force-stop or reboot).
        """
        try:
            dump = self.run("shell", "dumpsys", "activity", "activities", timeout=10)
            pattern = re.compile(re.escape(package) + r"/([A-Za-z0-9_.$]+)")
            candidates = []
            for match in pattern.finditer(dump):
                activity = match.group(1)
                if activity.endswith(("SplashActivity", "LoginActivity")):
                    continue
                component = f"{package}/{activity}"
                if component not in candidates:
                    candidates.append(component)
            if candidates:
                return self.run("shell", "am", "start", "-n", candidates[0], "-f", "0x00020000", timeout=30)
        except AdbError:
            pass
        return self.run("shell", "monkey", "-p", package, "1", timeout=30)

    def screenshot(self, output: str | Path) -> Path:
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        data = subprocess.check_output([self.adb, "exec-out", "screencap", "-p"], timeout=30,
                                       **subprocess_options())
        output.write_bytes(data)
        return output
