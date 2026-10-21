#!/bin/bash
# Start Metro detached. Used by the E0.0 spike to serve the app to the AVD.
export PATH="/opt/android-sdk/platform-tools:$PATH"
cd /home/gert/repos/pymoney/mobile

if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8081/status 2>/dev/null | grep -q 200; then
  echo "metro already up"
  exit 0
fi

python3 - <<'PY'
import os, sys
if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)
os.chdir("/home/gert/repos/pymoney/mobile")
os.umask(0)
log = os.open("/tmp/metro.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(log, 1)
os.dup2(log, 2)
devnull = os.open("/dev/null", os.O_RDWR)
os.dup2(devnull, 0)
os.execvp("npx", ["npx", "expo", "start", "--port", "8081"])
PY
echo "metro daemon launched (see /tmp/metro.log)"
