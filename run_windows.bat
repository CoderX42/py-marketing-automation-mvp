@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" -Launch
set "appExitCode=%ERRORLEVEL%"
if not "%appExitCode%"=="0" pause
exit /b %appExitCode%
