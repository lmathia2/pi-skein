#!/usr/bin/env bash
set -euo pipefail

workspace=${1:?usage: prepare-runtime.sh /path/to/workspace}
pi_repo="$workspace/pi"
skein_repo="$workspace/pi-skein"
eval_dir="$workspace/deepswe-eval"
stage="$eval_dir/runtime/stage"
archive="$eval_dir/runtime/pi-skein-runtime.tar.gz"

test -f "$pi_repo/packages/coding-agent/dist/bundle/cli.js"
test -f "$skein_repo/src/index.ts"
test -d "$skein_repo/node_modules/typebox"
mkdir -p "$eval_dir/runtime"
rm -rf "$stage"
mkdir -p "$stage/pi/node_modules/@earendil-works" "$stage/pi-skein/node_modules"
cp -a "$pi_repo/packages/coding-agent/dist/bundle/." "$stage/pi/"
mkdir -p "$stage/pi/chunks/dist/modes/interactive"
cp -a "$pi_repo/packages/coding-agent/dist/modes/interactive/theme" "$stage/pi/chunks/dist/modes/interactive/"
mkdir -p "$stage/pi/node_modules/@earendil-works/chord"
cp -a "$pi_repo/packages/chord/package.json" "$stage/pi/node_modules/@earendil-works/chord/"
cp -a "$pi_repo/packages/chord/dist" "$stage/pi/node_modules/@earendil-works/chord/"
cp -aL "$pi_repo/node_modules/typebox" "$pi_repo/node_modules/undici" "$pi_repo/node_modules/jiti" "$stage/pi/node_modules/"
cp -a "$skein_repo/src" "$skein_repo/python" "$skein_repo/package.json" "$stage/pi-skein/"
cp -aL "$skein_repo/node_modules/typebox" "$stage/pi-skein/node_modules/"
tar -czf "$archive" -C "$stage" .
printf '%s\n' "$archive"
