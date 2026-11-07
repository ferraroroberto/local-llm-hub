#!/bin/bash
# Forced-command dispatcher for the read-only results-puller SSH key.
#
# Referenced from the runner user's ~/.ssh/authorized_keys as
#   restrict,command="<path to this script>" ssh-ed25519 AAAA... hub-results-pull
# `restrict` switches off the pty, agent/X11/port forwarding and ~/.ssh/rc, so
# the key can run this script and nothing else. OpenSSH keeps the command the
# client asked for in $SSH_ORIGINAL_COMMAND without running it; this script
# parses that against a three-verb allowlist and reads only under
# ~/mac-nightly-results. It writes nothing.
#
#   list                      one NOW line, then a RUN line per run folder
#   files <repo> <run id>     one line per *.png entry of any type in the run
#   get <repo> <run id> <rel> the base64 of one regular .png file
#   audit <repo> <run id>     the base64 of the run's regular audit.json
#
# Run folders are written by adopter code, so this script's own output is
# data the hub re-validates; the checks here only keep the reads in bounds.
set -u

ROOT="$HOME/mac-nightly-results"
MAX_BYTES=$((8 * 1024 * 1024))
MAX_AUDIT_BYTES=$((256 * 1024))

fail() { echo "hub-results-pull: $1" >&2; exit "${2:-1}"; }

# repo: an adopter name. run: a numeric GitHub run id. rel: up to four path
# components of plain characters ending in .png.
valid_repo() { [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]]; }
valid_run() { [[ "$1" =~ ^[0-9]{1,20}$ ]]; }
valid_rel() { [[ "$1" =~ ^[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+){0,3}\.png$ ]] && [[ "$1" != *..* ]]; }

# The run folder must be a real directory (not a link) under a real repo
# folder; anything else is rejected before a path is ever built from it.
run_dir() {
  local repo="$1" run="$2"
  valid_repo "$repo" || fail "bad repo"
  valid_run "$run" || fail "bad run id"
  [ -d "$ROOT/$repo" ] && [ ! -L "$ROOT/$repo" ] || fail "no such repo folder" 3
  [ -d "$ROOT/$repo/$run" ] && [ ! -L "$ROOT/$repo/$run" ] || fail "no such run folder" 3
  printf '%s' "$ROOT/$repo/$run"
}

# `<type>|<size>|<path>` for every *.png entry, links included, so the caller
# can refuse a link instead of silently skipping it. BSD stat reports a link
# itself (not its target): %HT is "Regular File" or "Symbolic Link".
list_pngs() {
  find "$1" -mindepth 1 -maxdepth 4 -name '*.png' -exec stat -f '%HT|%z|%N' {} + 2>/dev/null
}

cmd_list() {
  echo "NOW $(date +%s)"
  local repo_dir run_path repo run stamp e2e smoke audit
  for repo_dir in "$ROOT"/*/; do
    repo_dir="${repo_dir%/}"
    [ -d "$repo_dir" ] && [ ! -L "$repo_dir" ] || continue
    repo="${repo_dir##*/}"
    valid_repo "$repo" || continue
    for run_path in "$repo_dir"/*/; do
      run_path="${run_path%/}"
      [ -d "$run_path" ] && [ ! -L "$run_path" ] || continue
      run="${run_path##*/}"
      valid_run "$run" || continue
      if [ -f "$run_path/READY" ] && [ ! -L "$run_path/READY" ]; then
        stamp="$(stat -f %m "$run_path/READY")"
        # Any character outside the plain set (the workflow writes "not run")
        # becomes "_", so a value can never add a field to the RUN line.
        e2e="$(sed -n 's/^e2e=//p' "$run_path/READY" | head -n 1 | tr -d '\r\n' | cut -c1-32 | tr -c 'A-Za-z0-9_-' '_')"
        smoke="$(sed -n 's/^smoke=//p' "$run_path/READY" | head -n 1 | tr -d '\r\n' | cut -c1-32 | tr -c 'A-Za-z0-9_-' '_')"
        audit="$(sed -n 's/^audit=//p' "$run_path/READY" | head -n 1 | tr -d '\r\n' | cut -c1-32 | tr -c 'A-Za-z0-9_-' '_')"
        echo "RUN $repo $run ready $stamp ${e2e:--} ${smoke:--} ${audit:--}"
      else
        echo "RUN $repo $run pending $(stat -f %m "$run_path") - - -"
      fi
    done
  done
}

cmd_files() {
  local dir
  dir="$(run_dir "$1" "$2")" || exit $?
  # Strip the run-folder prefix from the third field. awk, not a bash
  # substitution: macOS's bash 3.2 keeps the quotes of a quoted pattern.
  list_pngs "$dir" | awk -v prefix="$dir/" '{
    first = index($0, "|")
    second = first + index(substr($0, first + 1), "|")
    printf "%s|%s|%s\n", substr($0, 1, first - 1), substr($0, first + 1, second - first - 1), substr($0, second + 1 + length(prefix))
  }'
}

cmd_get() {
  local dir path size
  dir="$(run_dir "$1" "$2")" || exit $?
  valid_rel "$3" || fail "bad file name"
  path="$dir/$3"
  # Re-list and compare exactly: only a regular file the listing itself
  # reports can be read, so no component of the path is a link.
  size="$(list_pngs "$dir" | awk -F'|' -v want="$path" '$1 == "Regular File" && $3 == want { print $2; exit }')"
  [ -n "$size" ] || fail "not a regular png in this run" 3
  [ "$size" -le "$MAX_BYTES" ] || fail "file over the size cap" 4
  head -c "$MAX_BYTES" "$path" | base64
}

cmd_audit() {
  local dir path size
  dir="$(run_dir "$1" "$2")" || exit $?
  path="$dir/audit.json"
  # The file name is fixed, so only the run folder (checked above) and the file
  # itself can be links: refuse either rather than follow it.
  [ -f "$path" ] && [ ! -L "$path" ] || fail "no regular audit.json in this run" 3
  size="$(stat -f %z "$path")"
  [ "$size" -le "$MAX_AUDIT_BYTES" ] || fail "audit.json over the size cap" 4
  head -c "$MAX_AUDIT_BYTES" "$path" | base64
}

# shellcheck disable=SC2086  # the allowlist split on spaces is the point
set -- ${SSH_ORIGINAL_COMMAND:-}
case "${1:-}" in
  list) [ "$#" -eq 1 ] || fail "list takes no arguments"; cmd_list ;;
  files) [ "$#" -eq 3 ] || fail "files takes <repo> <run id>"; cmd_files "$2" "$3" ;;
  get) [ "$#" -eq 4 ] || fail "get takes <repo> <run id> <file>"; cmd_get "$2" "$3" "$4" ;;
  audit) [ "$#" -eq 3 ] || fail "audit takes <repo> <run id>"; cmd_audit "$2" "$3" ;;
  *) fail "rejected command" ;;
esac
