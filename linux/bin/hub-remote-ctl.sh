#!/bin/bash
# Forced-command dispatcher for the local-llm-hub-remote-ctl SSH key (#181, #368).
#
# The Linux/systemd counterpart to mac/bin/hub-remote-ctl.sh. Installed on a
# systemd satellite (gaming, later openclaw) and referenced from
# ~/.ssh/authorized_keys via a
#   command="/home/<user>/local-llm-hub/linux/bin/hub-remote-ctl.sh"
# restriction on the dedicated automation-only key — that key has no shell
# access beyond the two verbs this script allows. OpenSSH preserves whatever
# command the client tried to run in $SSH_ORIGINAL_COMMAND even though it is
# never executed directly; this script reads that and dispatches on a strict
# two-value allowlist. Anything else is rejected — no general shell is ever
# reachable through this key.
#
# Verbs (same contract the tower's src/remote_bootstrap.py speaks to the Mac):
#   sync      — git pull --ff-only + dependency delta into the repo venv, then
#               `systemctl restart`. For an already-running peer being brought
#               up to the tower's latest main.
#   bootstrap — the same pull + deps, then bring the hub UP whether the unit is
#               running, stopped, or was never started (restart, falling back to
#               start). This is the fleet reconcile loop's cold-wake path
#               (#353/#364): a freshly-woken satellite converges to latest main
#               without a human. (Unlike the Mac dispatcher, whose bootstrap is
#               restart-only, the Linux bootstrap pulls too — the reconcile
#               wake→bootstrap chain is the only automated caller and a cold
#               satellite should come up current.)
#
# systemctl runs under passwordless sudo (a sudoers.d drop-in on the box — see
# docs/machines.md). `sudo -n` never prompts: a missing/incorrect sudoers rule
# fails fast with "sudo: a password is required" on stderr instead of hanging
# on a password prompt no TTY will ever answer over this forced-command channel.
set -euo pipefail

SERVICE="local-llm-hub"
REPO="$HOME/local-llm-hub"
VENV_PY="$REPO/.venv/bin/python"

# Pull to latest main + install any dependency delta into the repo venv.
# Shared by both verbs. The explicit repo-venv python (never a system pip) so
# the install lands in the exact environment the systemd unit's run_hub.sh
# execs (./.venv/bin/python -m src.run_backend hub).
update_repo() {
  cd "$REPO"
  git pull --ff-only
  "$VENV_PY" -m pip install -q -r requirements.txt
}

# A commanded `systemctl restart` bounces a running unit and cleanly starts a
# loaded-but-stopped one — the plain sync path.
restart_hub() {
  sudo -n systemctl restart "$SERVICE"
}

# bootstrap tolerates a dead/never-started unit. `systemctl restart` already
# starts a loaded-but-stopped unit, so this only diverges when restart itself
# fails — then fall back to a plain `start`. A genuinely missing unit file (the
# box was never `systemctl enable`d — install it via `python -m src.install
# --fix`, see linux/systemd/local-llm-hub.service) is a real error surfaced by
# both branches, not silently swallowed.
ensure_hub_up() {
  if sudo -n systemctl restart "$SERVICE"; then
    return 0
  fi
  echo "hub-remote-ctl: 'systemctl restart $SERVICE' failed; trying 'start'" >&2
  sudo -n systemctl start "$SERVICE"
}

case "${SSH_ORIGINAL_COMMAND:-}" in
  bootstrap)
    update_repo
    ensure_hub_up
    ;;
  sync)
    update_repo
    restart_hub
    ;;
  *)
    echo "hub-remote-ctl: rejected command: ${SSH_ORIGINAL_COMMAND:-<empty>}" >&2
    exit 1
    ;;
esac
