#!/bin/bash
# On a Codemagic Mac held by codemagic.yaml: build one product from ~/fb/inputs
# with local_build.py, then prove its editor: page_probe loads the installed page
# both ways, and au_view_probe opens the installed Audio Unit the way Logic does.
#   mac_session.sh SLUG INDEX_SHA256 VST3_BUNDLE_NAME AU_TYPE AU_SUBTYPE AU_MANUFACTURER
# Logs: ~/fb/session-SLUG.log. Outputs: ~/fb/outputs (queue layout) and ~/fb/probe-SLUG.
set -euo pipefail
slug=$1; index=$2; vst3=$3; au_type=$4; au_subtype=$5; au_manufacturer=$6
base="$HOME/fb"
cd "$base"
if [ -d /Applications/Xcode-16.4.app ]; then export DEVELOPER_DIR=/Applications/Xcode-16.4.app/Contents/Developer; fi
{
  date -u; sw_vers; xcodebuild -version; python3 --version
  rm -rf "run-$slug"; mkdir "run-$slug"; cd "run-$slug"
  python3 "$base/repo/scripts/local_build.py" "$slug" "$index" "$base/inputs" "$base/outputs"
  cd "$base"
  mkdir -p "probe-$slug"
  clang++ -std=c++20 -fobjc-arc -framework Cocoa -framework WebKit "$base/repo/scripts/page_probe.mm" -o "probe-$slug/page_probe"
  clang++ -std=c++20 -fobjc-arc -framework Cocoa -framework AudioToolbox -framework CoreAudioKit \
    "$base/repo/scripts/au_view_probe.mm" -o "probe-$slug/au_view_probe"
  page="/Library/Audio/Plug-Ins/VST3/$vst3/Contents/Resources/web/index.html"
  ls -la "$page"
  for mode in memory file; do
    "probe-$slug/page_probe" "$mode" "$page" "probe-$slug/$mode.png" | tee "probe-$slug/$mode.json"
  done
  # The builder's login keychain is locked in a remote session, so the licence panel would only say the device
  # identity cannot be stored. A customer's login keychain is unlocked: give the probe an unlocked default
  # keychain for the run, then put the user's keychain settings back.
  keychain="$HOME/Library/Keychains/fb-probe.keychain-db"
  old_default=$(security default-keychain | tr -d ' "')
  restore_keychain() {
    security default-keychain -s "$old_default" || true
    security list-keychains -d user -s "$HOME/Library/Keychains/login.keychain-db" || true
    rm -f "$keychain"
  }
  trap restore_keychain EXIT
  rm -f "$keychain"
  security create-keychain -p fb-probe "$keychain"
  security set-keychain-settings "$keychain"
  security unlock-keychain -p fb-probe "$keychain"
  security list-keychains -d user -s "$keychain" "$HOME/Library/Keychains/login.keychain-db"
  security default-keychain -s "$keychain"
  "probe-$slug/au_view_probe" "$au_type" "$au_subtype" "$au_manufacturer" "probe-$slug/au-window.png" | tee "probe-$slug/au-view.txt"
  restore_keychain
  trap - EXIT
  date -u; echo "SESSION DONE $slug"
} >"$base/session-$slug.log" 2>&1
