#!/bin/bash
# macOS 에서 더블클릭(또는 터미널에서 실행)하면 "AI Usage.app" 을 만듭니다.
# 만들 때만 Python 3 가 필요하고, 완성된 .app 은 Python 없이 바로 실행됩니다.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null; then
  echo "Python 3 가 필요합니다 (https://www.python.org/downloads/ 에서 설치 후 다시 실행)"; exit 1
fi

python3 -m venv .venv
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q pyinstaller rumps

rm -rf build dist "AI Usage.spec"
pyinstaller --noconfirm --windowed --name "AI Usage" \
  --osx-bundle-identifier com.local.aiusage \
  --hidden-import rumps \
  usage_mac.py

# Dock 아이콘 없이 상태표시줄에만 표시
plutil -insert LSUIElement -bool true "dist/AI Usage.app/Contents/Info.plist" 2>/dev/null \
  || plutil -replace LSUIElement -bool true "dist/AI Usage.app/Contents/Info.plist"

# 로컬 실행용 임시(ad-hoc) 서명
codesign --force --deep --sign - "dist/AI Usage.app"

echo
echo "완료: $(pwd)/dist/AI Usage.app"
echo "Applications 폴더로 옮겨 실행하세요. 처음에 '확인되지 않은 개발자' 경고가 뜨면"
echo "앱을 우클릭 > 열기 를 누르거나, 터미널에서: xattr -cr \"dist/AI Usage.app\""
open dist
