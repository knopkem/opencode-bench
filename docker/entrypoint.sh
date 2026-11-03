#!/usr/bin/env bash
set -euo pipefail

mkdir -p /home/agent /home/agent/.config

# Keep the mounted config read-only on the host, but give OpenCode a writable
# XDG config directory inside the container. The harness mounts the user's
# OpenCode config directory at /config/opencode.
if [ -d /config/opencode ]; then
  mkdir -p /home/agent/.config
  rm -rf /home/agent/.config/opencode
  cp -a /config/opencode /home/agent/.config/opencode
fi

# The image ships a Linux install of the seed's node_modules. The workdir is a
# fresh copy from the macOS host, so use the preinstalled tree instead of
# running npm install inside the agent (network, platform binaries, time).
if [ -d /work ] && [ -w /work ] && [ ! -e /work/node_modules ] \
   && [ -d /opt/opencode-bench/node_modules ]; then
  cp -a /opt/opencode-bench/node_modules /work/node_modules
fi

exec "$@"
