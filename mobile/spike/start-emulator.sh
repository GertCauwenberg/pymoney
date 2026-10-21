#!/bin/bash
# Start the headless Android emulator detached from the calling shell.
export ANDROID_HOME=/opt/android-sdk
export ANDROID_SDK_ROOT=/opt/android-sdk
export PATH="/opt/android-sdk/platform-tools:/opt/android-sdk/emulator:$PATH"

if adb -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | grep -q 1; then
  echo "emulator already booted"
  exit 0
fi

# Double-fork so the emulator is not in our process group.
python3 - <<'PY'
import os, sys
if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)
os.chdir("/")
os.umask(0)
devnull = os.open("/dev/null", os.O_RDWR)
os.dup2(devnull, 0)
os.dup2(devnull, 1)
os.dup2(devnull, 2)
log = os.open("/tmp/emulator.log", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
os.dup2(log, 1)
os.dup2(log, 2)
os.execvp("emulator", [
    "emulator", "-avd", "pymoney_spike",
    "-no-window", "-no-audio", "-no-boot-anim",
    "-gpu", "swiftshader_indirect",
    "-camera-back", "none", "-camera-front", "none",
    "-no-snapshot-save",
])
PY
echo "emulator daemon launched (pid file-less; see /tmp/emulator.log)"
