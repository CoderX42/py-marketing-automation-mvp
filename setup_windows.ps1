param([switch]$Launch)
$ErrorActionPreference = 'Stop'
$exitCode = 0
$transcriptStarted = $false
$lock = $null
$logPath = $null
try {
    Set-Location -LiteralPath $PSScriptRoot
    $dataFolder = Join-Path $env:LOCALAPPDATA 'MarketingAutomation'
    $logFolder = Join-Path $dataFolder 'logs'
    New-Item -ItemType Directory -Force -Path $logFolder | Out-Null
    try {
        $lock = [IO.File]::Open((Join-Path $dataFolder 'windows.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch { throw '工具或环境安装已在另一个窗口运行，请先使用已有窗口。' }
    $logPath = Join-Path $logFolder ('setup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
    Start-Transcript -Path $logPath | Out-Null
    $transcriptStarted = $true
    . (Join-Path $PSScriptRoot 'windows_bootstrap.ps1')
    $python = Initialize-WindowsEnvironment
    Write-Host '环境检查完成。连接手机、开启 USB 调试并允许授权后，即可开始试运行。' -ForegroundColor Green
    if ($Launch) {
        & $python (Join-Path $PSScriptRoot 'app.py')
        if ($LASTEXITCODE -ne 0) { throw "应用退出异常（退出码 $LASTEXITCODE）。" }
    }
} catch {
    $exitCode = 1
    Write-Host ("安装或启动失败：" + $_.Exception.Message) -ForegroundColor Red
    Write-Host '修复原因后重新双击 run_windows.bat，会重新检查并补齐环境。'
    if ($logPath) { Write-Host "详细日志：$logPath" }
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    if ($lock) { $lock.Dispose() }
}
exit $exitCode
