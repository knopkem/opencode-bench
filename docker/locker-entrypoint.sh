#!/usr/bin/env bash
set -euo pipefail

# The harness mounts the host OpenCode config read-only at /config/opencode.
# OpenCode expects a writable XDG config directory, so copy it into place.
mkdir -p /home/agent /home/agent/.config
if [ -d /config/opencode ]; then
  rm -rf /home/agent/.config/opencode
  cp -a /config/opencode /home/agent/.config/opencode
fi
mkdir -p /home/agent/.local/share/opencode

exec "$@"
