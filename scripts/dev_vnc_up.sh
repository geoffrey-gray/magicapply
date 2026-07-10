#!/usr/bin/env bash
# Start guest Xvfb + openbox + x11vnc for headed Playwright on magicapply-dev.
# Run *inside* the VM:  bash scripts/dev_vnc_up.sh
# Then on the host:     ssh -fN -L 5901:127.0.0.1:5901 magicapply-dev
#                       vncviewer 127.0.0.1:5901
set -euo pipefail

DISPLAY_NUM="${DISPLAY_NUM:-1}"
GEOMETRY="${GEOMETRY:-1400x900x24}"
RFBPORT="${RFBPORT:-5901}"
export DISPLAY=":${DISPLAY_NUM}"

need() { command -v "$1" >/dev/null 2>&1 || {
  echo "missing command: $1 — install: sudo apt-get install -y xvfb x11vnc openbox xterm x11-apps x11-utils scrot" >&2
  exit 1
}; }

need Xvfb
need x11vnc
need openbox

# Stop prior instances of these exact binaries (not -f: avoid killing this script).
for bin in x11vnc openbox Xvfb; do
  if pgrep -x "$bin" >/dev/null 2>&1; then
    pkill -x "$bin" || true
  fi
done
sleep 1

Xvfb ":${DISPLAY_NUM}" -screen 0 "${GEOMETRY}" -ac +extension GLX +render -noreset &
sleep 1

openbox &
sleep 0.5

if command -v xsetroot >/dev/null 2>&1; then
  xsetroot -solid '#2b4c6f' || true
fi

x11vnc -display ":${DISPLAY_NUM}" -rfbport "${RFBPORT}" \
  -localhost -shared -forever -nopw -xkb -bg \
  -o /tmp/x11vnc.log

if command -v xmessage >/dev/null 2>&1; then
  xmessage -geometry 480x100+80+80 "MagicApply VNC OK — DISPLAY=${DISPLAY} port ${RFBPORT}" &
fi

echo "VNC stack up: DISPLAY=${DISPLAY} rfbport=${RFBPORT} (localhost only)"
echo "Host tunnel:  ssh -fN -L ${RFBPORT}:127.0.0.1:${RFBPORT} magicapply-dev"
echo "Host viewer:  vncviewer 127.0.0.1:${RFBPORT}"
echo "Auth login:   DISPLAY=${DISPLAY} uv run magicapply auth login linkedin --root configs --force --auto-save"
