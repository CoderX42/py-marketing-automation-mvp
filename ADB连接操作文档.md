# 安卓手机使用 ADB 连接电脑操作文档

本说明用于连接“营销详情批量查询工具”使用的安卓手机。macOS 和 Windows 都适用。

ADB 是 Android Debug Bridge 的简称。手机端需要打开的是**开发者选项中的 USB 调试**，不是单独名为“ADB 模式”的开关。

## 一、准备工作

需要准备：

- 一台安卓手机
- 一条支持数据传输的 USB 数据线
- 一台 macOS 或 Windows 电脑
- 手机解锁密码
- 可以安装软件的电脑账户
- 目标应用 APK 或已安装的“网格通”应用

连接时建议使用可信的数据线和可信电脑。完成任务后可以关闭 USB 调试并撤销 USB 调试授权。

## 二、一键安装电脑环境

### macOS

打开“终端”，执行：

```bash
cd /Users/karl/doc/study/odsidian-md/outputs/marketing-automation-mvp
chmod +x setup_mac.sh
./setup_mac.sh
```

脚本会安装：

- Python（若系统没有可用的 python3）
- Android Platform-Tools（包含 `adb`）
- Node.js 和 Java 17
- Appium
- Appium UiAutomator2 驱动

脚本可能要求输入 macOS 登录密码。Homebrew 安装完成后，如果当前终端仍找不到 `brew`，关闭终端并重新打开一次，再重新运行脚本。

### Windows

Windows 10/11 的 Intel/AMD x64 电脑，将完整程序文件夹解压到可写目录后，直接双击 `run_windows.bat`。首次联网自动安装缺失环境，完成后直接打开应用；后续启动复用已安装环境。手机需 Android 8 及以上。

只安装环境时也可用 PowerShell 执行。建议以普通用户运行，安装程序需要管理员权限时按系统提示确认：

```powershell
cd "C:\你的路径\marketing-automation-mvp"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup_windows.ps1
```

脚本会按需安装：

- Python 及桌面应用依赖
- Node.js/npm、Java JDK（优先 WinGet；没有 WinGet 时由脚本从官方下载便携版）
- Android 命令行工具、Platform-Tools、Build-Tools（Google 官方下载）
- Appium 与 UiAutomator2 驱动（工具专用目录）

脚本会在当前进程配置 PATH、JAVA_HOME 和 ANDROID_HOME，无需重新打开终端。运行应用请始终使用 `run_windows.bat`。安装失败时查看终端提示和 `%LOCALAPPDATA%\MarketingAutomation\logs` 中的日志，修复后再次双击即可。

如果 Windows 没有 `winget`，无需先从 Microsoft Store 安装“应用安装程序”：`run_windows.bat` 会自动从 Python.org、Node.js 和 Microsoft OpenJDK 官方地址下载到用户目录。若公司代理或防火墙拦截官方下载，请让管理员放行相关域名后重试。若设备管理器有未知 Android 设备，还需安装手机品牌官方 USB 驱动。USB 调试授权与应用登录需在手机上手动完成。

## 三、手机端打开 USB 调试

不同品牌菜单名称会有差异，下面是通用步骤：

1. 打开“设置 → 关于手机”。
2. 连续点击“版本号”或“软件版本” 7 次，直到提示“您已处于开发者模式”。
3. 返回设置，搜索“开发者选项”。常见位置是“系统设置 → 开发者选项”。
4. 打开“开发者选项”。
5. 打开“USB 调试”。
6. 如果存在“默认 USB 配置”，选择“文件传输 / Android Auto”。
7. 如果存在“仅充电时允许 ADB 调试”，按需要打开。

首次连接时，手机会弹出“允许 USB 调试吗？”：

- 核对电脑 RSA 指纹后点击“允许”。
- 可以勾选“始终允许使用这台计算机进行调试”。
- 如果没有弹窗，先关闭再重新打开 USB 调试，然后重新插拔数据线。

## 四、USB 连接验证

### 1. 连接手机

1. 解锁手机屏幕。
2. 用数据线连接电脑。
3. 下拉通知栏，把 USB 用途从“仅充电”改为“文件传输”。
4. 等待 USB 调试授权弹窗并点击允许。

### 2. 查看设备状态

macOS 执行：

```bash
adb devices
```

Windows 默认安装位置可执行以下命令（若复用了自定义 SDK，请替换为对应路径）；也可直接在桌面应用中点击“检测设备”：

```powershell
& "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe" devices
```

正常结果类似：

```text
List of devices attached
ABC1234567    device
```

状态含义：

- `device`：连接正常，可以继续。
- `unauthorized`：手机还没有允许此电脑调试。
- `offline`：设备连接异常，需要重新插拔或重启 ADB。
- 没有任何设备：电脑没有识别手机或 USB 调试未打开。

### 3. 获取设备信息

```bash
adb shell getprop ro.product.manufacturer
adb shell getprop ro.product.model
adb shell getprop ro.build.version.release
```

### 4. 重启 ADB 服务

```bash
adb kill-server
adb start-server
adb devices
```

## 五、安装并启动目标应用

如果 APK 文件名是 `wgt.apk.1`，可以直接尝试安装：

```bash
adb install -r "/路径/wgt.apk.1"
```

如果提示扩展名问题，可以复制为 `.apk` 后再安装：

```bash
cp "/路径/wgt.apk.1" "/路径/wgt.apk"
adb install -r "/路径/wgt.apk"
```

查看是否安装成功：

```bash
adb shell pm list packages | grep com.sh.cm.grid4a
```

Windows PowerShell 使用：

```powershell
adb shell pm list packages | Select-String "com.sh.cm.grid4a"
```

启动应用：

```bash
adb shell am start -n com.sh.cm.grid4a/com.sh.cm.grid4a.SplashActivity
```

也可以使用更宽松的启动方式：

```bash
adb shell monkey -p com.sh.cm.grid4a 1
```

应用的登录和营销页面依赖网络，手机需要能够访问应用要求的网络环境。首次登录仍需要在手机上人工输入账号、密码和短信验证码。

## 六、启动 Appium

桌面工具会自动启动 Appium，正常使用无需手动开启服务。以下仅供手动排查，不是首次使用的必要步骤：

```bash
appium --base-path /wd/hub
```

看到类似以下内容说明 Appium 已启动：

```text
Appium REST http interface listener started
```

不要关闭这个终端窗口。然后启动桌面工具 v1.1：

### macOS

```bash
cd /Users/karl/doc/study/odsidian-md/outputs/marketing-automation-mvp
./run_mac.command
```

### Windows

双击：

```text
run_windows.bat
```

在工具中点击“检测设备”，确认状态为 `device`，再选择 Excel、设置间隔并开始处理。

## 七、出现“当前网络不可用”时

这个提示不一定表示手机完全断网。当前 APK 的网络弹窗是通用提示，同一类提示还覆盖服务器请求超时、服务器响应超时、无法连接服务器、登录凭证失效和安全隧道断开。业务页面由远程 H5/API 提供，批量查询过快、Wi‑Fi 与 5G 切换、VPN/公司网络不可达、账号令牌过期，都会触发相同文案。

排查顺序：

1. 先在手机浏览器确认当前网络能打开普通网页，再回到业务应用重试；普通网页可用并不等于业务接口或公司专网可用。
2. 查询手机是否正在自动切换 Wi‑Fi 和移动数据。批量运行期间尽量只保留一个稳定网络；如果业务必须使用公司网络或指定 SIM，保持该通道在线。
3. 在“设置 → 应用 → 网格通”中允许 WLAN、移动数据和后台数据，把电池策略设为“不限制”。小米系统若有“省电策略”“后台活动限制”或“应用联网控制”，也要允许网格通联网。
4. 若应用提示登录失效、认证凭证失败或安全隧道断开，请在手机完成登录后再继续；不要反复点击“跳转”造成重复请求。
5. 工具默认每条基础间隔 30 秒、随机增加 5 秒，并使用批次暂停；稳定后再逐步调整，不建议一开始设得过短。

桌面工具 v1.1 会识别顶部红色网络横幅和底部“网络连接超时，请稍后再试”提示。横幅会优先点击右上角关闭控件（没有可访问标签时按横幅坐标关闭），底部 Toast 不会被当成按钮误点，而是等待其消失；随后在 15 秒、30 秒后重试**同一个号码**。重试成功就继续批量；连续失败则暂停，不会把当前号码误记为“无结果”，也不会直接操作下一号码。已经进入营销详情页的文字会先写入同名 `.jsonl` 恢复日志和 Excel，恢复网络/登录后在同一个桌面窗口再次点击“开始处理”即可续跑。

若网络一直正常但仍反复弹窗，问题更可能是业务会话、远程接口限流或安全隧道，而不是 ADB 连接。此类服务端状态无法由桌面自动化永久绕过，只能通过稳定网络、较低频率和重新登录降低发生率。

## 七、可选：无线 ADB

### Android 11 及以上

1. 手机和电脑连接同一个 Wi-Fi。
2. 手机进入“开发者选项 → 无线调试”。
3. 点击“使用配对码配对设备”。
4. 电脑执行：

```bash
adb pair 手机IP:配对端口
```

5. 输入手机显示的 6 位配对码。
6. 查看无线调试页面显示的连接端口，然后执行：

```bash
adb connect 手机IP:连接端口
adb devices
```

### Android 10 及以下的传统方式

先用 USB 连接并确认 `adb devices` 正常：

```bash
adb tcpip 5555
adb shell ip route
adb connect 手机IP:5555
adb devices
```

无线连接不稳定时，优先恢复 USB 连接。任务完成后可以执行：

```bash
adb disconnect
```

## 八、常见问题

### `adb: command not found` 或“不是内部或外部命令”

应用中显示 `[Errno 2] No such file or directory: 'adb'` 也属于同一问题：电脑找不到 ADB，尚未进行手机连接检查。仅插入 Type-C 数据线不会自动安装 ADB。

macOS 可以只安装缺少的 Platform-Tools：

```bash
brew install --cask android-platform-tools
```

应用会自动查找 Homebrew、Android SDK 和 PATH 中的 ADB。安装后重新点击“检测设备”；更新了应用代码时，关闭应用并重新运行 `run_mac.command`。

也可以用完整路径验证：

```bash
/opt/homebrew/bin/adb devices
```

上面的路径适用于 Apple Silicon Mac 的标准 Homebrew 安装。Intel Mac 通常是 `/usr/local/bin/adb`。

Mac 的“允许配件连接”与手机的“允许 USB 调试”是两次独立授权。Mac 弹窗点击“允许”后，解锁手机并确认 USB 调试的 RSA 授权弹窗。只有检测结果为 `device` 才表示 ADB 已连接并授权。

验证 ADB 版本：

```bash
adb version
```

参考：[Android 官方 ADB 连接说明](https://developer.android.com/tools/adb#Enabling)。

### `unauthorized`

手机没有确认 RSA 授权。解锁手机，重新插拔数据线，点击“允许 USB 调试”。仍无弹窗时，在开发者选项中点击“撤销 USB 调试授权”，然后重新连接。

### `offline`

依次尝试：

```bash
adb kill-server
adb start-server
adb devices
```

仍失败时更换数据线或 USB 接口，并把 USB 用途切换为“文件传输”。

### Windows 找不到设备

某些品牌需要安装厂商 USB 驱动。安装手机品牌的官方 USB 驱动后，打开设备管理器，确认没有黄色感叹号，再重新运行 `adb devices`。

### macOS 弹出“无法验证开发者”

优先使用 Homebrew 安装 Platform-Tools。若系统阻止已下载的工具，到“系统设置 → 隐私与安全性”允许本次运行。

### 手机只有充电，没有数据连接

更换支持数据传输的数据线，下拉通知栏选择“文件传输”。部分充电线只能充电，无法用于 ADB。

### Appium 找不到 UiAutomator2 驱动

执行：

```bash
appium driver install uiautomator2
appium driver list --installed
```

### 应用打开但营销页面加载失败

先在手机上直接操作应用确认网络可用。该应用的营销页面是远程 H5/uni-app 页面，手机离线或不在所需内网时，ADB 连接正常也无法得到营销详情。

### 应用要求重新登录

暂停桌面工具，在手机上完成账号、密码和短信验证码登录，再重新开始任务。工具会读取之前保存的结果，跳过已经成功处理的号码。

### 小米手机提示 `INSTALL_FAILED_USER_RESTRICTED`

ADB 已经连接，但系统阻止了 Appium 辅助服务 APK 安装。到“设置 → 更多设置 → 开发者选项”打开“USB 调试（安全设置）”或“通过 USB 安装”，保持手机解锁并确认安装授权弹窗，然后重新点击桌面工具的“开始处理”。如果看不到弹窗，先关闭再打开 USB 调试，重新插拔数据线，并确认手机没有开启禁止通过 USB 安装应用的安全策略。

## 九、完成后的清理

任务结束后可以：

```bash
adb disconnect
```

并在手机“开发者选项”中关闭“USB 调试”。如果电脑不是个人电脑，建议同时点击“撤销 USB 调试授权”。
