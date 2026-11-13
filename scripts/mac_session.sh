#!/bin/bash
# On a Codemagic Mac held by codemagic.yaml: build one product from ~/fb/inputs
# with local_build.py, then prove its editor page with page_probe.
#   mac_session.sh SLUG INDEX_SHA256 VST3_BUNDLE_NAME
# Logs: ~/fb/session-SLUG.log. Outputs: ~/fb/outputs (queue layout) and ~/fb/probe-SLUG.
set -euo pipefail
slug=$1; index=$2; vst3=$3
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
  page="/Library/Audio/Plug-Ins/VST3/$vst3/Contents/Resources/web/index.html"
  ls -la "$page"
  for mode in memory file; do
    "probe-$slug/page_probe" "$mode" "$page" "probe-$slug/$mode.png" | tee "probe-$slug/$mode.json"
  done
  date -u; echo "SESSION DONE $slug"
} >"$base/session-$slug.log" 2>&1
