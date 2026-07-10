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

# Bright root + large canaries so an empty black WM is never mistaken for "VNC broken".
if command -v xsetroot >/dev/null 2>&1; then
  xsetroot -solid '#00aa00' || true
fi

# -noxdamage: more reliable full updates on Xvfb; avoid stale black client view.
x11vnc -display ":${DISPLAY_NUM}" -rfbport "${RFBPORT}" \
  -localhost -shared -forever -nopw -xkb -noxdamage -bg \
  -o /tmp/x11vnc.log

if command -v xterm >/dev/null 2>&1; then
  xterm -geometry 90x28+40+40 -bg white -fg black \
    -e bash -c 'echo "MagicApply VNC OK (DISPLAY='"${DISPLAY}"')"; echo "If you see this white terminal, the stream works."; sleep 86400' &
fi
if command -v xmessage >/dev/null 2>&1; then
  xmessage -geometry 560x160+200+420 -bg yellow -fg black \
    "MagicApply VNC OK — yellow = stream works" &
fi
if command -v xrefresh >/dev/null 2>&1; then
  xrefresh || true
fi

echo "VNC stack up: DISPLAY=${DISPLAY} rfbport=${RFBPORT} (localhost only)"
echo "Host tunnel:  ssh -fN -L ${RFBPORT}:127.0.0.1:${RFBPORT} magicapply-dev"
echo "Host viewer:  vncviewer -RemoteResize=0 127.0.0.1:${RFBPORT}"
echo "  (RemoteResize=0 avoids TigerVNC black/letterbox on fixed Xvfb size)"
echo "Auth login:   DISPLAY=${DISPLAY} uv run magicapply auth login linkedin --root configs --force --auto-save"
echo "Verify from host (non-GUI): scripts/vnc_host_snapshot.py → /tmp/vnc_host_capture.png"
