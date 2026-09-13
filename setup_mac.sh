#!/usr/bin/env bash
set -euo pipefail

echo "== 安卓 ADB + Appium 环境安装（macOS）=="

if ! command -v brew >/dev/null 2>&1; then
  echo "未检测到 Homebrew，将安装 Homebrew。安装过程可能需要输入 macOS 密码。"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  if [[ -x /opt/homebrew/bin/brew ]]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew ]]; then
    eval "$(/usr/local/bin/brew shellenv)"
  fi
fi

# Recent macOS installations may not include a usable python3. Install the
# Homebrew interpreter before creating the project virtual environment.
if ! command -v python3 >/dev/null 2>&1; then
  brew install python
fi

# UiAutomator2 uses the Java toolchain on the computer. Install a supported
# JDK only when no Java 17 runtime is available.
if [[ -x /usr/libexec/java_home ]] && ! /usr/libexec/java_home -v 17 >/dev/null 2>&1; then
  brew install --cask temurin@17
fi

brew update
brew install --cask android-platform-tools
brew install node
npm install --global appium
appium driver list --installed 2>&1 | grep -q uiautomator2 || appium driver install uiautomator2

echo
echo "安装完成："
adb version | head -n 1
appium --version
echo "下一步：连接手机并执行：adb devices"
