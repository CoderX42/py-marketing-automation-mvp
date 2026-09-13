#!/bin/zsh
set -e
setopt NULL_GLOB
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/Library/Android/sdk/platform-tools:$PATH"
# Qt may log a harmless font-fallback timing message on macOS; keep the
# launcher output focused on actionable ADB/Appium errors.
export QT_LOGGING_RULES="${QT_LOGGING_RULES:-qt.qpa.fonts=false}"
# The system Python on older macOS releases links LibreSSL while urllib3 v2
# prefers OpenSSL.  Appium still works; hide this known startup warning so the
# terminal only shows actionable setup/runtime errors.
export PYTHONWARNINGS="${PYTHONWARNINGS:-ignore:urllib3 v2 only supports OpenSSL}"
cd "$(dirname "$0")"

# A new Mac may not ship with Python or Homebrew. Run the idempotent setup
# script first so the virtual environment can be created reliably.
if ! command -v brew >/dev/null 2>&1; then
  ./setup_mac.sh
fi
if ! command -v python3 >/dev/null 2>&1; then
  brew install python
fi

# Homebrew 的 Platform-Tools 是独立 cask，Appium 仍需要 ANDROID_HOME。
if [[ -z "${ANDROID_HOME:-}" ]]; then
  for sdk_root in /opt/homebrew/Caskroom/android-platform-tools/* /usr/local/Caskroom/android-platform-tools/*; do
    if [[ -x "$sdk_root/platform-tools/adb" ]]; then
      export ANDROID_HOME="$sdk_root"
      export ANDROID_SDK_ROOT="$sdk_root"
      break
    fi
  done
fi
if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import PySide6, appium, openpyxl' >/dev/null 2>&1; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
source .venv/bin/activate

# 首次运行自动补齐桌面程序实际需要的 ADB、Node.js、Appium 和 UiAutomator2。
if ! command -v adb >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install --cask android-platform-tools
  else
    echo "未找到 Homebrew，请先运行 ./setup_mac.sh"
    exit 1
  fi
fi
if [[ -s "$HOME/.nvm/nvm.sh" ]]; then
  set +e
  source "$HOME/.nvm/nvm.sh"
  nvm use --silent default >/dev/null 2>&1 || true
  set -e
fi
if ! command -v node >/dev/null 2>&1; then
  brew install node
fi
if ! command -v appium >/dev/null 2>&1; then
  npm install --global appium
fi
if ! appium driver list --installed 2>&1 | grep -qi uiautomator2; then
  appium driver install uiautomator2
fi

# Appium's Android driver requires a JDK. Prefer Java 17 when several JDKs
# are installed, while leaving an existing compatible JAVA_HOME untouched.
if [[ -x /usr/libexec/java_home ]]; then
  java_home_17="$(/usr/libexec/java_home -v 17 2>/dev/null || true)"
  if [[ -n "$java_home_17" ]]; then
    export JAVA_HOME="$java_home_17"
  fi
fi

python app.py
