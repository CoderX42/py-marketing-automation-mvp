# 营销详情批量查询工具 v1.1

v1.1 针对应用网络提示增加同号码自动重试、异常弹窗关闭和断点续跑；已采集详情先落盘，持续失败才暂停任务。

这是一个 macOS / Windows 共用代码的桌面工具原型，针对 APK 中的“网格通”（版本 2.0.1）和视频中的安卓应用流程：

`首页 → 智慧营销 → 个人业务 → 全景视图 → 营销助手(免签入) → 输入手机号 → 跳转 → 读取营销详情`

当“智慧营销”页面显示“暂无数据”时，改走备用入口：

`首页 → 常用 → 营销助手(免签入) → 输入手机号 → 跳转 → 读取营销详情`

两条链路都只进入“营销助手(免签入)”，不会进入普通“营销助手”；详情滚动采集及 Excel 保存规则一致。

## 已支持

- 默认识别 APK 包名 `com.sh.cm.grid4a`
- 批量处理连接手机现有页面，不重启应用登录流程
- 读取带表头或不带表头的 `.xlsx`
- 自动识别第一列手机号、第二列姓名
- 单设备串行批量处理
- 每条固定间隔、随机波动、批次暂停
- 进入详情页即保存初始内容；每次向下读取同步写入恢复日志，每个号码完成后更新 Excel
- 输出汇总、营销详情、推荐明细、输入数据、运行日志等工作表
- 营销详情文本（含向下滚动区域）和错误状态保留；单个单元格超过 Excel 限制时自动分段
- 账号登录、短信验证码由使用者人工完成
- 使用同一个 Appium 会话读取页面文字，避免重复启动 UiAutomator 导致手机操作中断
- 默认信任手机当前页面，不在启动查询前主动判断登录状态，避免异步页面文案造成误判
- 遇到应用顶部红色“当前网络不可用”横幅或底部“网络连接超时，请稍后再试”提示，会先清除遮挡、等待页面稳定，并以 15 秒、30 秒退避重试当前号码；持续失败才暂停人工恢复

## 安装依赖

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
brew install android-platform-tools
brew install node
npm install -g appium
appium driver install uiautomator2
python app.py
```

新设备也可以直接双击发行包中的 `首次运行.command`。脚本会按需安装 Python、Java 17、Android Platform-Tools、Node.js、Appium 和 UiAutomator2，然后打开桌面应用；首次安装需要联网，并可能要求输入 macOS 密码。

### Windows 首次使用

适配目标：Windows 10/11，Intel/AMD x64，安卓手机 Android 8 及以上。Windows ARM、32 位系统暂不支持此自动安装入口。Windows 运行时代码及模拟回归已检查；尚未在 Windows 真机完成首次安装和手机端到端验证。

1. 将完整程序文件夹解压到可写目录（例如“文档”），不要在压缩包内直接运行。
2. 联网后双击 `run_windows.bat`。脚本自动检查、按需安装 Python、Node.js/npm、Java JDK、Android SDK/ADB、Appium、UiAutomator2 和桌面 Python 依赖。
3. 出现系统安装授权提示时确认。环境检查全部通过后，脚本会直接打开桌面应用，无需重新开终端或手动配置 PATH。
4. 在手机上开启 USB 调试、允许此电脑调试并登录应用；在电脑应用中检测设备，先试运行一个号码。

下次仍双击 `run_windows.bat`。已有且完整的环境不会重复下载安装。安装失败会停在错误信息处，修复网络或权限后可重试；日志位于 `%LOCALAPPDATA%\MarketingAutomation\logs`。同时打开第二个启动窗口会提示使用已有窗口，避免并发安装和操作手机。

脚本使用 Windows 自带的 PowerShell 5.1。优先使用 WinGet；如果电脑没有 WinGet、Microsoft Store 被禁用或 WinGet 安装失败，脚本会自动从 Python.org、Node.js 和 Microsoft OpenJDK 官方地址下载用户目录内的便携运行时，不需要手动安装 WinGet，也不要求管理员权限。Android SDK、ADB、Appium 和 UiAutomator2 同样由脚本补齐。手机品牌 USB 驱动若未被 Windows 自动识别，仍需要人工安装。

工具使用独立 `.venv-windows`，不会复用从 Mac 拷来的 `.venv`。Appium 3.7.0、UiAutomator2 8.6.4 安装在 `%LOCALAPPDATA%\MarketingAutomation\runtime`，SDK 默认位于 `%LOCALAPPDATA%\Android\Sdk`。启动时向本进程配置环境，不要求修改系统环境变量；不会改动已有的全局 Appium。安装过程会接受所安装软件和 Android SDK 的标准许可。

只安装环境、不打开应用，可执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

环境依据：[Appium 系统要求](https://appium.io/docs/en/3.3/quickstart/requirements/)、[UiAutomator2 要求](https://github.com/appium/appium-uiautomator2-driver#requirements)、[WinGet 安装说明](https://learn.microsoft.com/en-us/windows/package-manager/winget/)。

第一次运行前：

1. 在安卓手机打开开发者选项和 USB 调试。
2. 用数据线连接电脑并在手机上允许调试授权。
3. 通过对应系统的启动脚本打开工具；处理任务时会自动启动 Appium Server。
4. 在工具中选择 APK，点击“安装 APK”（可选）。
5. 如果应用在后台，点击“唤醒应用”将已有页面带到前台；批量处理不会主动重启应用或登录流程。
6. 登录成功后再选择 Excel 并开始批量查询。

## 操作指引

1. 点击“检测设备”，确认出现一台 `device` 状态的安卓设备；点击“唤醒应用”只唤醒现有任务，不主动重启登录页。
2. 点击“下载 Excel 模板”生成手机号模板，或选择已有的 `工作簿200.xlsx`。
3. 确认手机号列。无表头文件默认读取第一列，第二列作为姓名。
4. 设置每条间隔、随机波动和每批暂停。界面默认每条间隔 30 秒、随机增加 5 秒，适合先以较保守的速度试跑。
5. 第一次运行保持“先试运行 1 个号码”勾选，确认手机页面确实进入输入页、点击“跳转”并显示营销详情后，再取消勾选进行批量处理。应用会逐条进入“营销助手(免签入)”。
6. 任务结束后点击“打开结果 Excel”，或在输入文件所在目录打开带时间戳的 `*_营销结果_*.xlsx`。结果工作簿的“营销详情”页按号码和“详情分段”保存完整滚动内容；同一号码的多段按段号顺序拼接即可恢复全文，“推荐明细”页保存可拆分的明细。

## Excel 格式

推荐模板表头：

```text
手机号 | 姓名
13800000000 | 张三
```

手机号按文本读取，避免 Excel 自动转换格式。原始文件不会被覆盖。输出文件包括：

- `结果汇总`：每个手机号一行，包含状态、时间、推荐数量、摘要、错误信息。
- `营销详情`：每个手机号的详情全文；超过 30,000 字符会分成多行，避免 Excel 32,767 字符单元格限制。
- `推荐明细`：每条推荐业务一行。
- `运行日志`：便于断点续跑和排查问题。

## 常见问题

### 找不到设备

检查 USB 调试和授权弹窗，重新插拔数据线，在终端执行 `adb devices`，确认状态是 `device` 而不是 `unauthorized`。

### 页面要求重新登录或提示网络不可用

手机显示该提示不一定代表整台手机断网：业务接口超时、账号凭证失效、公司网络/VPN不可达、Wi-Fi 与移动网络切换，都可能被应用统一显示为“当前网络不可用”。程序会识别顶部红色横幅及底部超时 Toast；横幅会点击右上角关闭控件（无文字标签时按横幅坐标处理），Toast 则等待自然消失，不会误点详情页面。提示清除后在 15 秒、30 秒后自动重试同一个号码，不会跳到下一号码。重试期间已进入详情页的内容仍会先写入恢复日志和 Excel。

如果两次重试后仍失败，程序会暂停本批任务并弹出提示，避免把其他号码写错。请在手机恢复业务网络并完成必要的重新登录，然后在同一个电脑应用窗口再次点击“开始处理”。输入文件及号码清单、试运行模式未变时，会继续原输出文件，跳过成功或明确无推荐的号码，重新处理未完成号码。修改输入清单、切换试运行模式或关闭后重开工具会创建新的输出文件。启动查询前默认不做登录状态预检；如需严格预检，可在 `config.json` 设置 `check_login_state: true`。

批量运行前建议：只保留一个稳定且能正常打开业务页面的网络，避免 Wi-Fi/5G 自动切换；若该业务必须使用公司网络、指定 SIM 或 VPN，请保持该通道在线；在小米“设置 → 应用 → 网格通”中允许 WLAN、移动数据和后台数据，将省电策略设为“不限制”；保持手机充电、解锁并让网格通位于前台。执行间隔先用 30 秒、随机增加 5 秒，稳定后再逐步缩短。

“唤醒应用”按钮会优先把已有的非登录页面带到前台；批量处理不会主动启动 `SplashActivity`。如果 Android 已经结束应用进程，或应用服务端登录令牌已经过期，手机仍可能显示登录页，这是应用本身没有保留有效会话，需要重新登录一次。

### 小米手机提示 `INSTALL_FAILED_USER_RESTRICTED`

这是手机阻止 Appium 安装自动化辅助服务。打开“开发者选项”中的“USB 调试（安全设置）”或“通过 USB 安装”，解锁手机并确认安装授权弹窗，然后重新点击“开始处理”。不同 MIUI/HyperOS 版本的菜单名称可能略有差异。

### 营销详情读取为空

进入详情后即保存当前内容，再等待推荐正文加载；只有标题或未确认读到页尾时标记“采集不完整”，保留已采集文本并注明原因，不计为成功。程序使用慢速重叠滑动，看到“属地推荐方案”且连续两次滑动正文不再变化才确认完整。达到滚动上限或滑动失败时也保留内容。

### 手机页面出现错误弹窗

程序会读取弹窗内容，将当前号码写入“营销详情”并自动点击“确定”，随后继续处理 Excel 的下一条号码。常见的“该号码非上海移动现网号码”会作为该号码的错误信息保留，不会中断整批任务。

### 页面一直加载

检查手机网络和应用提示。单条查询有等待上限；已取得的详情会保留。无法确认入口或出现登录、网络失效时会停止并提示，避免后续号码进入错误页面。

如果系统浏览器能上网但业务应用仍提示网络不可用，优先测试关闭家庭/公共 Wi-Fi，仅保留已经验证可访问业务系统的 5G 或公司网络。此应用包含网络类型、VPN和登录有效性检查，普通互联网连通不代表业务接口可用。

### Excel 保存失败或被占用

程序先把采集结果写入同名 `.jsonl` 恢复日志，再更新 Excel。保存失败会立即停止，并明确提示“Excel 未完成更新”，不会把未保存的记录计为成功。请关闭占用结果文件的 Excel 窗口、检查磁盘空间和输出目录权限，再在同一个应用窗口点击“开始处理”；程序会先从恢复日志重建 Excel。保留 `.jsonl` 文件可用于找回中断前的数据。

### Excel 第一行就是手机号

这是工作簿200的当前结构。导入向导支持无表头模式，默认把第一列当作手机号、第二列当作姓名。

### 频率怎么设置

先使用较保守的间隔，配合随机波动和批次暂停。单设备串行执行，不要同时连接多台设备运行同一账号。

## 打包

```bash
pip install pyinstaller
pyinstaller --noconfirm --windowed --name MarketingAutomation app.py
```

macOS 生成 `.app` 后可用 `pyinstaller --windowed` 配合签名和公证流程生成分发包；Windows 生成 `dist/MarketingAutomation/MarketingAutomation.exe`，再使用 Inno Setup 或 NSIS 制作安装包。

## 当前限制

- 需要先人工完成一次登录和短信验证码。
- 如果“营销详情”是 WebView 且没有可访问文本，需要补充 WebView DOM 读取或 OCR 适配。
- 页面文案变化时，需要在 `config.json` 中调整选择器。
- APK 的营销页面依赖远程 H5/网络服务，离线状态下无法读取推荐结果。
- 第一版只查询和导出，不办理业务、不提交订单。

### Windows 便携 EXE 构建（维护者）

在一台可联网的 Windows 10/11 x64 构建机上，准备 Python 3.12 后，在项目目录执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_windows_portable.ps1
```

脚本会下载 Node.js、Microsoft OpenJDK、Android Platform-Tools，并将 Appium 3.7.0 与 UiAutomator2 8.6.4 安装到 `runtime`，再用 PyInstaller 生成 `marketing-automation-windows-v1.1-portable.zip`。用户解压后直接双击 `MarketingAutomation-v1.1\MarketingAutomation-v1.1.exe`，不需要安装 Python、Node.js、Java、Android SDK、ADB 或 Appium。Android 手机的 USB ADB 驱动属于 Windows 系统驱动，仍需由系统/手机厂商提供并授权。
