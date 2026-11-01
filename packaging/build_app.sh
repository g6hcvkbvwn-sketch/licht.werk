#!/bin/sh
# Builds dist-app/LichtWerk.app (unsigned, Apple Silicon) and a zip for sharing.
set -e
cd "$(dirname "$0")/.."

(cd frontend && npm install --silent && npm run build)

.venv/bin/python -m pip install -q -U pip
.venv/bin/python -m pip install -q -e . pywebview pyinstaller

# App icon from the logo
ICONSET=build/LichtWerk.iconset
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size frontend/public/lichtwerk-logo.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size*2)) $((size*2)) frontend/public/lichtwerk-logo.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o build/LichtWerk.icns

# Default settings shipped inside the app (backups and temp files excluded)
STAGE=build/bundle-config
rm -rf "$STAGE" && mkdir -p "$STAGE"
rsync -a --exclude '*.bak' --exclude '.*.tmp' config/ "$STAGE/"

rm -rf dist-app build/pyinstaller
.venv/bin/python -m PyInstaller --noconfirm --windowed --name LichtWerk \
  --icon "$PWD/build/LichtWerk.icns" \
  --paths backend \
  --add-data "$PWD/frontend/dist:frontend/dist" \
  --add-data "$PWD/$STAGE:config" \
  --add-data "$PWD/packaging/ola:packaging/ola" \
  --collect-submodules uvicorn --collect-submodules rtmidi \
  --hidden-import mido.backends.rtmidi \
  --osx-bundle-identifier local.lichtwerk \
  --distpath dist-app --workpath build/pyinstaller --specpath build \
  packaging/launcher.py

(cd dist-app && ditto -c -k --keepParent LichtWerk.app LichtWerk-macOS-arm64.zip)
echo "Done: dist-app/LichtWerk.app and dist-app/LichtWerk-macOS-arm64.zip"
