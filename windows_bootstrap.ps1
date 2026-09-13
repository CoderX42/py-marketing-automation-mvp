# Windows PowerShell 5.1 compatible. Loaded by setup_windows.ps1.
$ErrorActionPreference = 'Stop'
$script:AppiumVersion = '3.7.0'
$script:DriverVersion = '8.6.4'
$script:RuntimeRoot = Join-Path $env:LOCALAPPDATA 'MarketingAutomation\runtime'
$script:PythonZipUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.zip'
$script:PythonZipSha256 = '8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d'
$script:NodeZipUrl = 'https://nodejs.org/dist/v22.14.0/node-v22.14.0-win-x64.zip'
$script:NodeZipSha256 = '55b639295920b219bb2acbcfa00f90393a2789095b7323f79475c9f34795f217'
$script:JavaZipUrl = 'https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip'

function Invoke-Checked {
    param([string]$File, [string[]]$Arguments, [string]$Description)
    & $File @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败（退出码 $LASTEXITCODE）。请检查上方错误、网络连接及安装权限。"
    }
}

function Update-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machinePath;$userPath;$env:Path"
}

function Invoke-Download {
    param(
        [string]$Url,
        [string]$Destination,
        [string]$Description,
        [string]$Sha256,
        [int64]$MinimumBytes = 1048576,
        [ValidateSet('SHA256', 'SHA1')][string]$HashAlgorithm = 'SHA256'
    )
    $parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $valid = $false
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        try {
            $item = Get-Item -LiteralPath $Destination
            if ($item.Length -ge $MinimumBytes) {
                if ($Sha256) {
                    $valid = (Get-FileHash -LiteralPath $Destination -Algorithm $HashAlgorithm).Hash.ToLowerInvariant() -eq $Sha256.ToLowerInvariant()
                } else { $valid = $true }
            }
        } catch { $valid = $false }
    }
    if ($valid) { return $Destination }
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue }
    $partial = "$Destination.partial"
    if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
    $lastError = $null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Write-Host "$Description（第 $attempt/3 次）…" -ForegroundColor Cyan
            Invoke-WebRequest -Uri $Url -OutFile $partial -UseBasicParsing -TimeoutSec 180
            if (-not (Test-Path -LiteralPath $partial -PathType Leaf)) { throw '下载文件不存在。' }
            $item = Get-Item -LiteralPath $partial
            if ($item.Length -lt $MinimumBytes) { throw "下载文件过小（$($item.Length) 字节）。" }
            if ($Sha256) {
                $actual = (Get-FileHash -LiteralPath $partial -Algorithm $HashAlgorithm).Hash.ToLowerInvariant()
                if ($actual -ne $Sha256.ToLowerInvariant()) { throw '下载文件校验值不匹配。' }
            }
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return $Destination
        } catch {
            $lastError = $_.Exception.Message
            if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue }
            if ($attempt -lt 3) { Start-Sleep -Seconds (2 * $attempt) }
        }
    }
    throw "$Description 下载失败：$lastError。请检查网络代理后重新运行。"
}

function Try-Install-WithWinget {
    param([string]$Id)
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { return $false }
    try {
        Write-Host "正在通过 WinGet 安装 $Id；失败时将自动切换官方下载…" -ForegroundColor Cyan
        Invoke-Checked $winget.Source @('install', '--id', $Id, '--exact', '--source', 'winget', '--accept-source-agreements', '--accept-package-agreements', '--silent', '--disable-interactivity') $Id
        Update-ProcessPath
        return $true
    } catch {
        Write-Warning "WinGet 安装 $Id 失败，将切换官方下载：$($_.Exception.Message)"
        return $false
    }
}

function Install-PythonDirect {
    $zip = Join-Path $script:RuntimeRoot 'downloads\python-3.12.10-amd64.zip'
    $target = Join-Path $script:RuntimeRoot 'python312'
    Invoke-Download $script:PythonZipUrl $zip '正在下载 Python 3.12（官方下载）' $script:PythonZipSha256 20000000 | Out-Null
    $staging = Join-Path $script:RuntimeRoot ('python-extract-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
        if (-not (Test-Path -LiteralPath (Join-Path $staging 'python.exe'))) { throw 'Python 压缩包中未找到 python.exe。' }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $staging -Destination $target
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    Write-Host "Python 已安装到 $target（无需 WinGet 或管理员权限）。" -ForegroundColor Green
}

function Install-NodeDirect {
    $zip = Join-Path $script:RuntimeRoot 'downloads\node-v22.14.0-win-x64.zip'
    $target = Join-Path $script:RuntimeRoot 'nodejs'
    Invoke-Download $script:NodeZipUrl $zip '正在下载 Node.js 22（官方下载）' $script:NodeZipSha256 20000000 | Out-Null
    $staging = Join-Path $script:RuntimeRoot ('node-extract-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
        $payload = Get-ChildItem -LiteralPath $staging -Directory | Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'node.exe') } | Select-Object -First 1
        if (-not $payload) { throw 'Node.js 压缩包中未找到 node.exe。' }
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $payload.FullName -Destination $target
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    Write-Host "Node.js 已安装到 $target（无需 WinGet 或管理员权限）。" -ForegroundColor Green
}

function Install-JavaDirect {
    $zip = Join-Path $script:RuntimeRoot 'downloads\microsoft-jdk-17-windows-x64.zip'
    $target = Join-Path $script:RuntimeRoot 'java'
    Invoke-Download $script:JavaZipUrl $zip '正在下载 Microsoft OpenJDK 17（官方下载）' $null 50000000 | Out-Null
    $staging = Join-Path $script:RuntimeRoot ('java-extract-' + [guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $staging | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $staging -Force
        $javac = Get-ChildItem -LiteralPath $staging -Filter 'javac.exe' -Recurse -File | Select-Object -First 1
        if (-not $javac) { throw 'JDK 压缩包中未找到 javac.exe。' }
        $payload = Split-Path (Split-Path $javac.FullName -Parent) -Parent
        if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
        Move-Item -LiteralPath $payload -Destination $target
    } catch {
        # The Microsoft alias is intentionally cached, but never keep a bad
        # archive after an interrupted download or proxy error.
        if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue }
        throw
    } finally {
        if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    }
    Write-Host "Java JDK 17 已安装到 $target（无需 WinGet 或管理员权限）。" -ForegroundColor Green
}

function Find-Python {
    $candidates = @(
        (Join-Path $script:RuntimeRoot 'python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $located = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)'
        if ($LASTEXITCODE -eq 0) { $candidates += [string]($located | Select-Object -Last 1) }
    }
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*\WindowsApps\*') { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            & $candidate -c 'import sys, struct; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) and struct.calcsize(''P'') == 8 else 1)' | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}

function Find-Node {
    $candidates = @(
        (Join-Path $script:RuntimeRoot 'nodejs\node.exe'),
        (Join-Path $env:ProgramFiles 'nodejs\node.exe')
    )
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            & $candidate -e 'const [a,b]=process.versions.node.split(''.'').map(Number);process.exit(((a===20&&b>=19)||(a===22&&b>=12)||a>=24)?0:1)' | Out-Null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}

function Find-JavaHome {
    $roots = @($env:JAVA_HOME, [Environment]::GetEnvironmentVariable('JAVA_HOME', 'User'), [Environment]::GetEnvironmentVariable('JAVA_HOME', 'Machine'))
    $roots += Join-Path $script:RuntimeRoot 'java'
    $roots += @(Get-ChildItem -Path (Join-Path $env:ProgramFiles 'Microsoft\jdk-17*') -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    $javac = Get-Command javac.exe -ErrorAction SilentlyContinue
    if ($javac) { $roots += Split-Path (Split-Path $javac.Source -Parent) -Parent }
    foreach ($root in $roots) {
        if (-not $root) { continue }
        $release = Join-Path $root 'release'
        if ((Test-Path -LiteralPath (Join-Path $root 'bin\javac.exe')) -and (Test-Path -LiteralPath $release)) {
            $version = Select-String -LiteralPath $release -Pattern '^JAVA_VERSION="(\d+)'
            if ($version -and [int]$version.Matches[0].Groups[1].Value -ge 17) { return $root }
        }
    }
    return $null
}

function Install-AndroidTools {
    param([string]$SdkRoot)
    $destination = Join-Path $SdkRoot 'cmdline-tools\19.0'
    $manager = Join-Path $destination 'bin\sdkmanager.bat'
    $adb = Join-Path $SdkRoot 'platform-tools\adb.exe'
    $signer = Join-Path $SdkRoot 'build-tools\35.0.0\apksigner.bat'
    if ((Test-Path -LiteralPath $adb) -and (Test-Path -LiteralPath $signer) -and
        (Test-Path -LiteralPath (Join-Path $SdkRoot 'platform-tools\package.xml')) -and
        (Test-Path -LiteralPath (Join-Path $SdkRoot 'build-tools\35.0.0\lib\apksigner.jar'))) { return }
    $toolsComplete = (Test-Path -LiteralPath $manager -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $destination 'lib\sdkmanager-classpath.jar') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $destination 'source.properties') -PathType Leaf)
    if (-not $toolsComplete) {
        $python = Find-Python
        if (-not $python) { throw '请先完成第 1 步 Python 环境检查。' }
        $extractor = Join-Path $PSScriptRoot 'android_sdk_tools.py'
        if (-not (Test-Path -LiteralPath $extractor -PathType Leaf)) {
            throw '更新文件不完整：请将 android_sdk_tools.py 与 windows_bootstrap.ps1 一起放到 run_windows.bat 所在文件夹。'
        }
        Write-Host '正在读取 Android 命令行工具下载信息…' -ForegroundColor Cyan
        [xml]$repository = (Invoke-WebRequest 'https://dl.google.com/android/repository/repository2-1.xml' -UseBasicParsing).Content
        $archive = $repository.SelectSingleNode("//*[local-name()='remotePackage' and @path='cmdline-tools;19.0']/*[local-name()='archives']/*[local-name()='archive'][*[local-name()='host-os']='windows']/*[local-name()='complete']")
        if (-not $archive) { throw 'Google SDK 清单中未找到 Windows 命令行工具 19.0。' }
        $filename = [string]$archive.url
        if ($filename -notmatch '^commandlinetools-win-[0-9]+_latest\.zip$') { throw 'Android 下载地址不符合官方文件格式。' }
        # Keep a verified download between attempts. Do not use PS 5.1
        # Expand-Archive here: deep Java paths plus the old GUID staging path
        # can exceed MAX_PATH and leave a misleading "bin not found" error.
        $zip = Join-Path (Join-Path $script:RuntimeRoot 'downloads') $filename
        Invoke-Download -Url ("https://dl.google.com/android/repository/" + $filename) `
            -Destination $zip -Description '正在下载 Android 命令行工具（官方下载）' `
            -Sha256 ([string]$archive.checksum) -HashAlgorithm SHA1 -MinimumBytes ([int64]$archive.size) | Out-Null
        Write-Host '正在使用 Python 解压 Android 工具并检查目录完整性…' -ForegroundColor Cyan
        Invoke-Checked $python @($extractor, $zip, $destination, '--sha1', [string]$archive.checksum) 'Android 命令行工具解压'
    }
    Write-Host '正在安装 Android Platform-Tools 和 Build-Tools；首次安装会接受所需 SDK 许可。' -ForegroundColor Cyan
    $answers = 1..30 | ForEach-Object { 'y' }
    $answers | & $manager "--sdk_root=$SdkRoot" 'platform-tools' 'build-tools;35.0.0' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Android SDK 安装失败（退出码 $LASTEXITCODE），请检查网络或 SDK 许可提示。" }
    if (-not (Test-Path -LiteralPath $adb) -or -not (Test-Path -LiteralPath $signer)) { throw 'Android SDK 安装未完成，请重新运行启动脚本。' }
}

function Initialize-WindowsEnvironment {
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or -not [Environment]::Is64BitOperatingSystem) { throw '此启动入口适用于 Windows 10/11 64 位电脑。' }
    if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64' -or $env:PROCESSOR_ARCHITEW6432 -eq 'ARM64') { throw '当前自动安装脚本面向 Intel/AMD x64；Windows ARM 设备尚未验证，请使用 x64 电脑。' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    Write-Host '营销详情批量查询 v1.1 · Windows 环境检查' -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $script:RuntimeRoot | Out-Null
    Update-ProcessPath

    Write-Host '[1/6] Python'
    $python = Find-Python
    if (-not $python) {
        if (-not (Try-Install-WithWinget 'Python.Python.3.12')) { Install-PythonDirect }
        $python = Find-Python
    }
    if (-not $python) { Install-PythonDirect; $python = Find-Python }
    if (-not $python) { throw '未找到可用的 64 位 Python 3.10–3.12，请安装 Python 3.12 后重试。' }

    Write-Host '[2/6] Node.js / npm'
    $node = Find-Node
    if (-not $node) {
        if (-not (Try-Install-WithWinget 'OpenJS.NodeJS.LTS')) { Install-NodeDirect }
        $node = Find-Node
    }
    if (-not $node) { Install-NodeDirect; $node = Find-Node }
    if (-not $node) { throw 'Node.js 版本不满足 Appium 要求，请安装当前 Node.js LTS 后重试。' }
    $nodeDirectory = Split-Path $node -Parent
    $env:Path = "$nodeDirectory;$env:Path"
    $npm = Join-Path $nodeDirectory 'node_modules\npm\bin\npm-cli.js'
    if (-not (Test-Path -LiteralPath $npm)) { throw 'Node.js 安装缺少 npm，请修复 Node.js LTS 安装。' }
    $npmVersion = & $node $npm --version
    if ($LASTEXITCODE -ne 0 -or [int](([string]$npmVersion).Split('.')[0]) -lt 10) { throw '需要 npm 10 或以上版本，请修复 Node.js LTS 安装。' }

    Write-Host '[3/6] Java JDK'
    $javaHome = Find-JavaHome
    if (-not $javaHome) {
        if (-not (Try-Install-WithWinget 'Microsoft.OpenJDK.17')) { Install-JavaDirect }
        $javaHome = Find-JavaHome
    }
    if (-not $javaHome) { Install-JavaDirect; $javaHome = Find-JavaHome }
    if (-not $javaHome) { throw '未找到 Java JDK 17 或以上版本，请检查 Java 安装。' }
    $env:JAVA_HOME = $javaHome
    $env:Path = "$javaHome\bin;$env:Path"
    Invoke-Checked (Join-Path $javaHome 'bin\java.exe') @('-version') 'Java 检查'

    Write-Host '[4/6] Android SDK / ADB'
    $sdkRoot = Join-Path $env:LOCALAPPDATA 'Android\Sdk'
    foreach ($existing in @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT)) {
        if ($existing -and (Test-Path -LiteralPath (Join-Path $existing 'platform-tools\adb.exe'))) { $sdkRoot = $existing; break }
    }
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    Install-AndroidTools $sdkRoot
    $env:Path = "$sdkRoot\platform-tools;$env:Path"
    $env:ADB_PATH = Join-Path $sdkRoot 'platform-tools\adb.exe'
    Invoke-Checked $env:ADB_PATH @('version') 'ADB 检查'
    Invoke-Checked (Join-Path $javaHome 'bin\java.exe') @('-jar', (Join-Path $sdkRoot 'build-tools\35.0.0\lib\apksigner.jar'), '--version') 'Android Build-Tools 检查'

    Write-Host '[5/6] Appium / UiAutomator2'
    $prefix = Join-Path $script:RuntimeRoot 'npm'
    $env:NPM_CONFIG_PREFIX = $prefix
    $env:APPIUM_HOME = Join-Path $script:RuntimeRoot 'appium-home'
    $env:Path = "$prefix;$env:Path"
    $entry = Join-Path $prefix 'node_modules\appium\index.js'
    $manifest = Join-Path $prefix 'node_modules\appium\package.json'
    $appiumInstalled = $false
    if ((Test-Path -LiteralPath $entry) -and (Test-Path -LiteralPath $manifest)) {
        try { $appiumInstalled = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).version -eq $script:AppiumVersion } catch { $appiumInstalled = $false }
    }
    if ($appiumInstalled) {
        & $node $entry --version | Out-Host
        $appiumInstalled = $LASTEXITCODE -eq 0
    }
    if (-not $appiumInstalled) {
        Invoke-Checked $node @($npm, 'install', '--global', '--prefix', $prefix, "appium@$script:AppiumVersion", '--no-fund', '--no-audit') 'Appium 安装'
    }
    Invoke-Checked $node @($entry, '--version') 'Appium 检查'
    $driverJson = & $node $entry driver list --installed --json
    if ($LASTEXITCODE -ne 0) { throw '无法读取 Appium 驱动列表，请检查安装日志。' }
    $drivers = ($driverJson -join "`n") | ConvertFrom-Json
    if (-not $drivers.uiautomator2) {
        Invoke-Checked $node @($entry, 'driver', 'install', "uiautomator2@$script:DriverVersion") 'UiAutomator2 安装'
    } elseif ($drivers.uiautomator2.version -ne $script:DriverVersion) {
        throw "工具专用目录中的 UiAutomator2 版本不符，期望 $script:DriverVersion；请联系维护者修复，避免混用驱动。"
    }
    $driverJson = & $node $entry driver list --installed --json
    if ($LASTEXITCODE -ne 0) { throw 'UiAutomator2 驱动验证失败。' }
    $drivers = ($driverJson -join "`n") | ConvertFrom-Json
    if ($drivers.uiautomator2.version -ne $script:DriverVersion) { throw 'UiAutomator2 未正确安装。' }

    Write-Host '[6/6] 桌面应用依赖'
    $venv = Join-Path $PSScriptRoot '.venv-windows'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $venvHealthy = $false
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)' | Out-Null
        $venvHealthy = $LASTEXITCODE -eq 0
    }
    if (-not $venvHealthy) { Invoke-Checked $python @('-m', 'venv', $venv) 'Python 虚拟环境创建或修复' }
    & $venvPython -c 'import pip' | Out-Null
    if ($LASTEXITCODE -ne 0) { Invoke-Checked $venvPython @('-m', 'ensurepip', '--upgrade') 'pip 修复' }
    $probe = @'
import pathlib, importlib.metadata as m
from pip._vendor.packaging.requirements import Requirement
for line in pathlib.Path('requirements.txt').read_text().splitlines():
    if line.strip() and not line.startswith('#'):
        r = Requirement(line)
        assert m.version(r.name) in r.specifier, r.name
from PySide6.QtWidgets import QApplication
import app, automation, excel_io
'@
    & $venvPython -c $probe | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Invoke-Checked $venvPython @('-m', 'pip', 'install', '--upgrade', '--force-reinstall', '-r', (Join-Path $PSScriptRoot 'requirements.txt')) '桌面依赖安装'
        Invoke-Checked $venvPython @('-c', $probe) '桌面依赖检查'
    }
    Invoke-Checked $venvPython @('-m', 'pip', 'check') 'Python 依赖兼容性检查'
    return $venvPython
}
