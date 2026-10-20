#!/usr/bin/env bash
# Build the linux/arm64 code bundle used by both the AgentCore Runtime (zip) and the
# dispatcher Lambda (directory asset). No Docker: wheels are downloaded for the target
# platform with --only-binary, as described in the AgentCore direct code deployment guide.
# PyAV publishes manylinux_2_28 aarch64 wheels, so the target tag is aarch64-manylinux_2_28.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build
mkdir -p build/bundle
uv pip install \
  --python-platform aarch64-manylinux_2_28 \
  --python-version 3.13 \
  --target build/bundle \
  --only-binary=:all: \
  --exclude-newer "7 days" \
  --quiet \
  -r src/agent/requirements.txt
cp -R src/video_events build/bundle/video_events
cp src/agent/main.py build/bundle/main.py
cp src/functions/dispatcher/handler.py build/bundle/dispatcher_handler.py
find build/bundle -name "__pycache__" -type d -prune -exec rm -rf {} +
find build/bundle -type d -exec chmod 755 {} +
find build/bundle -type f -exec chmod 644 {} +
find build/bundle -path "*/bin/*" -type f -exec chmod 755 {} +
(cd build/bundle && zip -qr -X ../agent.zip .)
du -sh build/bundle build/agent.zip
