#!/bin/bash
# Forced-command dispatcher for the local-llm-hub-remote-ctl SSH key on a
# Linux peer — the Machines console's reboot/shutdown channel (#309).
#
# Sibling of mac/bin/hub-remote-ctl.sh, trimmed to the two power verbs. A
# Linux peer (e.g. OpenClaw) is a *managed-only* machine — it does not run
# the hub — so there is no bootstrap/sync here, only reboot/shutdown.
#
# Install on the peer and reference it from ~/.ssh/authorized_keys via a
#   command="~/local-llm-hub/linux/bin/hub-remote-ctl.sh"
# restriction on the dedicated automation-only key, so that key has no shell
# access beyond the strict allowlist below. OpenSSH preserves the client's
# requested command in $SSH_ORIGINAL_COMMAND even though it is never executed
# directly; this script dispatches on it and rejects anything else.
set -euo pipefail

# Detach a short-delayed power action so this SSH command returns cleanly
# (exit 0) before the box drops the connection; nohup survives the closing
# SSH channel. Requires passwordless sudo for the login user (the geek-out
# sudoers drop-in already grants it). `-r` reboots, `-h` halts (powers off).
power_off() {
  local flag="$1"
  nohup sh -c "sleep 2; sudo -n /sbin/shutdown ${flag} now" >/dev/null 2>&1 &
}

case "${SSH_ORIGINAL_COMMAND:-}" in
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
