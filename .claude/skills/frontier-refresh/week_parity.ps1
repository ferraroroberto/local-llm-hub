# Bi-weekly gate for run-weekly.bat: is this an "on" week for frontier-refresh?
#
# Whole weeks since Monday 2026-01-05, mod 2 - a fixed-epoch counter rather
# than ISO week numbers, so a 53-week ISO year never flips the cadence.
# PS 5.1-safe (no ISOWeek type).
#
# The answer is the process EXIT CODE, never stdout: run-weekly.bat shells
# here with -File and reads %ERRORLEVEL%, because a cmd-side `for /f` capture
# of PowerShell output has come back empty under non-interactive callers
# (see tray.bat), and an empty capture silently read as "on week".
#
#   10 = on week  -> run the skill
#   11 = off week -> skip
#
# Both are deliberately away from 0/1: PowerShell itself exits 1 on an
# unhandled error, and powershell.exe missing is 9009 in cmd, so any code
# other than 10/11 means "parity unknown" to the caller - never a verdict.

$ErrorActionPreference = "Stop"

$weeks = [math]::Floor(((Get-Date).Date - [datetime]::new(2026, 1, 5)).Days / 7)
exit (10 + ($weeks % 2))
