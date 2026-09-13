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
