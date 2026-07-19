#!/bin/bash
# Forced-command dispatcher for the local-llm-hub-remote-ctl SSH key (#181).
#
# Installed on the Mac Mini and referenced from ~/.ssh/authorized_keys via a
# `command="~/local-llm-hub/mac/bin/hub-remote-ctl.sh"` restriction on the
# dedicated automation-only key — that key has no shell access beyond what
# this script explicitly allows. OpenSSH preserves whatever command the
# client tried to run in $SSH_ORIGINAL_COMMAND even though it's never
# executed directly; this script reads that and dispatches on a strict
# two-value allowlist. Anything else is rejected — no general shell is ever
# reachable through this key.
set -euo pipefail

LABEL="com.ferraroroberto.local-llm-hub"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
REPO="$HOME/local-llm-hub"

# bootout-then-bootstrap (not `kickstart -k`) so this works whether the job
# is currently loaded (a live, reachable hub — bootout+rebootstrap is a
# clean restart) or fully unloaded (a genuinely dead hub, e.g. after
# /admin/api/hub/stop's own bootout, or the LaunchAgent was never
# registered) — `kickstart` alone only operates on an already-loaded job
# and fails outright on the dead case this endpoint exists to recover from.
#
# launchd needs a beat to fully release the label after bootout — an
# immediate bootstrap right after can transiently fail with "Input/output
# error" (confirmed live). Retry a few times with a short pause instead of
# a single blind sleep.
restart_hub() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  local attempt
  for attempt in 1 2 3 4 5; do
    sleep 1
    if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
      return 0
    fi
  done
  echo "hub-remote-ctl: launchctl bootstrap failed after 5 attempts" >&2
  exit 1
}

# Power action (reboot / shutdown) for the Machines console (#309). The
# actual `shutdown` drops the SSH connection the instant the box goes down,
# which would race this command's own exit and surface as a spurious ssh
# failure to the caller. So we detach a short-delayed shutdown with `nohup`
# (survives the closing SSH channel) and return 0 immediately — the caller
# gets a clean exit, and ~2 s later the machine powers down/reboots. `-r`
# reboots, `-h` halts (powers off). Requires passwordless sudo for the
# login user (the geek-out sudoers drop-in already grants it).
power_off() {
  local flag="$1"   # -r (reboot) | -h (shutdown)
  nohup sh -c "sleep 2; sudo -n /sbin/shutdown ${flag} now" >/dev/null 2>&1 &
}

case "${SSH_ORIGINAL_COMMAND:-}" in
  bootstrap)
    restart_hub
    ;;
  sync)
    cd "$REPO"
    git pull --ff-only
    restart_hub
    ;;
  reboot)
    power_off "-r"
    echo "hub-remote-ctl: reboot scheduled"
    ;;
  shutdown)
    power_off "-h"
    echo "hub-remote-ctl: shutdown scheduled"
    ;;
  *)
    echo "hub-remote-ctl: rejected command: ${SSH_ORIGINAL_COMMAND:-<empty>}" >&2
    exit 1
    ;;
esac
