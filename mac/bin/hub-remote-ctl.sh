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
REPO="$HOME/local-llm-hub"

case "${SSH_ORIGINAL_COMMAND:-}" in
  bootstrap)
    launchctl kickstart -k "gui/$(id -u)/${LABEL}"
    ;;
  sync)
    cd "$REPO"
    git pull --ff-only
    launchctl kickstart -k "gui/$(id -u)/${LABEL}"
    ;;
  *)
    echo "hub-remote-ctl: rejected command: ${SSH_ORIGINAL_COMMAND:-<empty>}" >&2
    exit 1
    ;;
esac
