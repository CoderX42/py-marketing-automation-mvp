# Windows PowerShell 5.1; run from this folder as a normal user.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtime = Join-Path $env:LOCALAPPDATA 'MarketingAutomation\runtime'
$downloads = Join-Path $runtime 'downloads'
$sdk = Join-Path $runtime 'android-sdk'
New-Item -ItemType Directory -Force -Path $runtime,$downloads,$sdk | Out-Null
function Need([string]$n) { $p=Join-Path $here $n; if (!(Test-Path -LiteralPath $p -PathType Leaf)) { throw "缺少安装包：$n" }; return $p }
Write-Host '[1/5] 安装 Python 3.12'
$py=Join-Path $runtime 'python312\python.exe'
if (!(Test-Path -LiteralPath $py)) { $p=Need 'python-3.12.10-amd64.exe'; Start-Process $p -Wait -ArgumentList '/quiet','InstallAllUsers=0','Include_pip=1','PrependPath=0',('TargetDir='+ (Join-Path $runtime 'python312')) } 
if (!(Test-Path -LiteralPath $py)) { throw 'Python 安装后未找到 python.exe' }
Write-Host '[2/5] 安装 Node.js 22'
if (!(Get-Command node.exe -ErrorAction SilentlyContinue)) { $p=Need 'node-v22.14.0-x64.msi'; Start-Process msiexec.exe -Wait -ArgumentList '/i', $p, '/qn', '/norestart' }
Write-Host '[3/5] 解压 Microsoft OpenJDK 17'
$java=Join-Path $runtime 'java\bin\java.exe'
if (!(Test-Path -LiteralPath $java)) { $p=Need 'microsoft-jdk-17-windows-x64.zip'; $tmp=Join-Path $runtime '.jdk-stage'; if(Test-Path $tmp){Remove-Item $tmp -Recurse -Force}; Expand-Archive $p $tmp -Force; $j=Get-ChildItem $tmp -Filter java.exe -Recurse -File | Select-Object -First 1; if(!$j){throw 'JDK 压缩包无 java.exe'}; $payload=Split-Path (Split-Path $j.FullName -Parent) -Parent; if(Test-Path $java){Remove-Item (Split-Path $java -Parent) -Recurse -Force}; Move-Item $payload (Join-Path $runtime 'java'); Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host '[4/5] 安装 Android SDK / ADB（长路径兼容）'
$adb=Join-Path $sdk 'platform-tools\adb.exe'
$manager=Join-Path $sdk 'cmdline-tools\19.0\bin\sdkmanager.bat'
if (!(Test-Path $adb)) { $p=Need 'platform-tools-latest-windows.zip'; $tmp=Join-Path $runtime '.platform-stage'; if(Test-Path $tmp){Remove-Item $tmp -Recurse -Force}; Expand-Archive $p $tmp -Force; Move-Item (Join-Path $tmp 'platform-tools') (Join-Path $sdk 'platform-tools') -Force; Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
if (!(Test-Path $manager)) { $p=Need 'commandlinetools-win-13114758_latest.zip'; $helper=Join-Path (Split-Path $here -Parent) 'android_sdk_tools.py'; if(!(Test-Path $helper)){throw '请将 android_sdk_tools.py 放在安装包根目录'}; & $py $helper $p (Join-Path $sdk 'cmdline-tools\19.0') '--sha1' '54a582f3bf73e04253602f2d1c80bd5868aac115'; if($LASTEXITCODE){throw 'Android 命令行工具解压失败'} }
Write-Host '[5/5] 运行应用环境检查（Appium/桌面依赖）'
$project=$here
if(Test-Path (Join-Path $project 'setup_windows.ps1')) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $project 'setup_windows.ps1') }
Write-Host '环境准备完成。现在可双击项目目录中的 run_windows.bat。' -ForegroundColor Green
Read-Host '按回车退出'
