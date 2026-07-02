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
restart_hub() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
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
  *)
    echo "hub-remote-ctl: rejected command: ${SSH_ORIGINAL_COMMAND:-<empty>}" >&2
    exit 1
    ;;
esac
