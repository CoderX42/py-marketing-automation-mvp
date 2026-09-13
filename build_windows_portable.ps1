# 在 Windows 10/11 构建“免安装便携版”
# 输出：dist-portable\MarketingAutomation-v1.1\MarketingAutomation-v1.1.exe
# 用户运行 exe 时无需安装 Python/Node/JDK/ADB/Appium。
[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Output = "dist-portable"
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$AppiumVersion = "3.7.0"
$DriverVersion = "8.6.4"
$stage = Join-Path $PSScriptRoot "build-portable-runtime"
$runtime = Join-Path $stage "runtime"
$dist = Join-Path $PSScriptRoot $Output

function Download([string]$Url, [string]$Path) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "下载 $Url" -ForegroundColor Cyan
        Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing
    }
    if ((Get-Item -LiteralPath $Path).Length -lt 1000000) { throw "下载文件异常：$Path" }
}
function Expand-Zip([string]$Zip, [string]$Destination) {
    $temp = Join-Path $stage ("extract-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $temp | Out-Null
    try {
        Expand-Archive -LiteralPath $Zip -DestinationPath $temp -Force
        New-Item -ItemType Directory -Force -Path $Destination | Out-Null
        $items = Get-ChildItem -LiteralPath $temp
        foreach ($item in $items) { Move-Item -LiteralPath $item.FullName -Destination $Destination -Force }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
    }
}

Write-Host "[1/5] 准备构建目录" -ForegroundColor Cyan
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
New-Item -ItemType Directory -Force -Path $runtime | Out-Null

Write-Host "[2/5] 下载 Node.js、Java、ADB 便携运行时" -ForegroundColor Cyan
$downloads = Join-Path $stage "downloads"
$nodeZip = Join-Path $downloads "node.zip"
$javaZip = Join-Path $downloads "java.zip"
$adbZip = Join-Path $downloads "platform-tools.zip"
Download "https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip" $nodeZip
Download "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip" $javaZip
Download "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" $adbZip
Expand-Zip $nodeZip (Join-Path $runtime "node")
# Node 压缩包有一层目录，提取后统一整理为 runtime\node\node.exe
$nodePayload = Get-ChildItem (Join-Path $runtime "node") -Directory | Where-Object { Test-Path (Join-Path $_.FullName "node.exe") } | Select-Object -First 1
if ($nodePayload) {
    Get-ChildItem $nodePayload.FullName -Force | Move-Item -Destination (Join-Path $runtime "node") -Force
    Remove-Item $nodePayload.FullName -Recurse -Force
}
Expand-Zip $javaZip (Join-Path $runtime "java")
$javaPayload = Get-ChildItem (Join-Path $runtime "java") -Directory | Where-Object { Test-Path (Join-Path $_.FullName "bin\java.exe") } | Select-Object -First 1
if ($javaPayload) {
    Get-ChildItem $javaPayload.FullName -Force | Move-Item -Destination (Join-Path $runtime "java") -Force
    Remove-Item $javaPayload.FullName -Recurse -Force
}
Expand-Zip $adbZip (Join-Path $runtime "android-sdk")
# platform-tools 压缩包目录整理到 Android SDK/platform-tools
$platformPayload = Get-ChildItem (Join-Path $runtime "android-sdk") -Directory | Where-Object { Test-Path (Join-Path $_.FullName "adb.exe") } | Select-Object -First 1
if ($platformPayload -and $platformPayload.Name -ne "platform-tools") { Rename-Item $platformPayload.FullName "platform-tools" }
if (-not (Test-Path (Join-Path $runtime "node\node.exe"))) { throw "Node.js 运行时缺失 node.exe" }
if (-not (Test-Path (Join-Path $runtime "java\bin\java.exe"))) { throw "Java 运行时缺失 java.exe" }
if (-not (Test-Path (Join-Path $runtime "android-sdk\platform-tools\adb.exe"))) { throw "ADB 运行时缺失 adb.exe" }

Write-Host "[3/5] 将 Appium 和 UiAutomator2 安装到便携目录" -ForegroundColor Cyan
$node = Join-Path $runtime "node\node.exe"
$npm = Join-Path $runtime "node\node_modules\npm\bin\npm-cli.js"
$prefix = Join-Path $runtime "npm"
$appiumHome = Join-Path $runtime "appium-home"
$env:NPM_CONFIG_PREFIX = $prefix
$env:APPIUM_HOME = $appiumHome
New-Item -ItemType Directory -Force -Path $prefix,$appiumHome | Out-Null
& $node $npm install --global --prefix $prefix "appium@$AppiumVersion" --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { throw "Appium 安装失败：$LASTEXITCODE" }
$entry = Join-Path $prefix "node_modules\appium\index.js"
& $node $entry driver install "uiautomator2@$DriverVersion"
if ($LASTEXITCODE -ne 0) { throw "UiAutomator2 安装失败：$LASTEXITCODE" }

Write-Host "[4/5] 构建 PyInstaller 便携 EXE" -ForegroundColor Cyan
& $Python -m pip install --upgrade pyinstaller -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Python 构建依赖安装失败" }
$work = Join-Path $stage "pyinstaller-work"
& $Python -m PyInstaller --noconfirm --clean --windowed `
    --name "MarketingAutomation-v1.1" `
    --distpath $dist --workpath $work --specpath $stage `
    --add-data "$runtime;runtime" app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 构建失败" }

Write-Host "[5/5] 写入便携版说明并打包 ZIP" -ForegroundColor Cyan
$bundle = Join-Path $dist "MarketingAutomation-v1.1"
@"
营销详情批量查询 v1.1 · Windows 便携版

双击 MarketingAutomation-v1.1.exe 启动，无需安装 Python、Node.js、Java、Android SDK、ADB、Appium。
请先在手机开启 USB 调试并安装/授权对应 Android USB 驱动；这是 Windows 系统级驱动，无法由普通应用可靠替代。
首次使用需要联网完成 Appium 驱动初始化。请勿移动 runtime 文件夹。
"@ | Set-Content -LiteralPath (Join-Path $bundle "使用说明.txt") -Encoding UTF8
$zip = Join-Path $PSScriptRoot "marketing-automation-windows-v1.1-portable.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path $bundle -DestinationPath $zip -CompressionLevel Optimal
Write-Host "完成：$zip" -ForegroundColor Green
Write-Host "用户解压后双击：MarketingAutomation-v1.1\MarketingAutomation-v1.1.exe" -ForegroundColor Green
