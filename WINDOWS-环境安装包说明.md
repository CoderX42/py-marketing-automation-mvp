# Windows 10 环境安装包说明

本项目源码仓库包含环境安装脚本：

- `install_windows_env.bat`
- `install_windows_env.ps1`
- `android_sdk_tools.py`

完整的 Python、Node.js、JDK、Android SDK/ADB 安装包及 npm 缓存体积较大，未纳入 Git 历史。使用交付目录中的 `windows-env-cache` 文件夹，或下载项目发布的 Windows 环境压缩包。

在项目根目录运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install_windows_env.ps1
```

脚本会将运行时安装到 `%LOCALAPPDATA%\MarketingAutomation\runtime`，然后调用 `setup_windows.ps1` 检查 Appium、UiAutomator2 和桌面 Python 依赖。

适用 Windows 10/11 x64。手机首次连接仍需开启 USB 调试并在手机上允许授权；手机厂商 USB 驱动可能需要另行安装。
