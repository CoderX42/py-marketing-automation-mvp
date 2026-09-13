# Windows 首次使用（v1.1）

适用：Windows 10/11 的 Intel/AMD 64 位电脑，Android 8 及以上手机。

1. 将压缩包完整解压到“文档”等可写目录。
2. 联网，双击 `run_windows.bat`。看到六项环境检查时请等待；如弹出系统安装授权，请确认。
3. 环境检查通过后自动打开程序。以后也使用此文件启动，不必手动打开 PowerShell。
4. 手机开启 USB 调试，用支持数据传输的数据线连接，在手机上点击“允许 USB 调试”。小米手机可能还需开启“USB 调试（安全设置）”和“通过 USB 安装”。
5. 在手机上安装并登录业务应用。在电脑程序中点击“检测设备”，下载 Excel 模板填写手机号，再选择表格。
6. 保持“试运行 1 个号码”勾选，确认营销详情和 Excel 内容正确后，再批量运行。

程序会补齐 Python、Node.js/npm、Java JDK、Android SDK/ADB、Appium、UiAutomator2 和桌面依赖。首次安装需要下载文件；已经安装完整的组件不会在每次启动时重新下载。安装耗时取决于网络速度。

## 遇到问题

- **第 4 步提示 `expanded\cmdline-tools\bin` 不存在**：旧脚本的临时目录加上 SDK 内部长文件名可能超过 Win10 传统路径限制。SDK 修正版使用已安装的 Python 解压，支持长路径，并验证 `sdkmanager.bat`、库文件和版本信息后才替换安装目录。将补丁中的 `windows_bootstrap.ps1`、`android_sdk_tools.py` 一起复制到 `run_windows.bat` 所在文件夹，覆盖同名文件后再次启动；已完成的 Python、Node.js、Java 会继续复用。不要删除 `%LOCALAPPDATA%\MarketingAutomation\runtime`。
- **提示没有 WinGet**：新版脚本会自动改用 Python.org、Node.js 和 Microsoft OpenJDK 官方下载并安装到当前用户目录，无需先安装 WinGet 或管理员权限。若官方下载也失败，请检查代理/防火墙是否允许访问 `python.org`、`nodejs.org`、`aka.ms`、`dl.google.com`，修复后再次双击启动。
- **下载失败**：检查电脑能否访问软件安装源、Google Android SDK 与 npm，恢复网络后重新运行。脚本会重新检查缺失组件。
- **窗口报错**：保留错误提示，详细日志在 `%LOCALAPPDATA%\MarketingAutomation\logs`。不要删除已有 Excel 或同名 `.jsonl` 文件。
- **提示已有窗口**：使用已打开的程序，不要重复运行启动脚本。
- **未发现手机**：确认数据线、USB 调试授权；在设备管理器检查 Android ADB 驱动，必要时安装手机品牌官方驱动。
- **手机弹出 `io.appium.uiautomator2.server` 或“应用未备案”安装页**：这是 Appium 的自动化辅助服务。保持手机解锁，在弹窗中点击“了解风险/允许安装”（不要点“取消”）；荣耀/MagicOS、小米等系统还要在开发者选项开启 USB 调试及允许通过 USB 安装。新版已将安装和 ADB 等待时间延长到 120 秒。
- **每次启动都重复要求安装 UiAutomator2**：新版会检测手机上是否已经同时安装服务 APK 和测试 APK，完整时自动跳过重复安装。若曾取消过其中一个弹窗，可按上一条重新确认一次；确认成功后后续启动不会重复弹窗。
- **没有业务 APK**：手机已经安装业务应用就无需再次安装；也可在电脑程序中选择 APK，或将安装包命名为 `wgt.apk` 放在程序目录。
- **手机网络失效或掉登录**：程序会先关闭可识别的网络横幅并重试当前号码；若仍失败，在手机恢复网络并登录，然后在同一个电脑程序窗口点击“开始处理”继续。首次安装环境不会替你登录业务账号。
- **Excel 无法保存**：关闭正在占用结果文件的 Excel/WPS，再在同一个程序窗口继续。

目前已通过代码回归和 Windows 行为模拟检查；尚未在 Windows 真机完成首次安装与手机全链路验证。此包是带自动环境安装的程序文件包，需要联网准备环境，不是离线免安装 EXE。
