#!/usr/bin/env bash
set -euo pipefail

mkdir -p /home/agent /home/agent/.config

# Keep the mounted config read-only on the host, but give OpenCode a writable
# XDG config directory inside the container.
if [ -f /config/opencode.json ]; then
  mkdir -p /home/agent/.config/opencode
  cp /config/opencode.json /home/agent/.config/opencode/opencode.json
fi

# The image ships a Linux install of the seed's node_modules. The workdir is a
# fresh copy from the macOS host, so use the preinstalled tree instead of
# running npm install inside the agent (network, platform binaries, time).
if [ -d /work ] && [ -w /work ] && [ ! -e /work/node_modules ] \
   && [ -d /opt/spritebench/node_modules ]; then
  cp -a /opt/spritebench/node_modules /work/node_modules
fi

exec "$@"
