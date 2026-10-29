"""Machine health watch: a slow resident sampler that spots leaks and pressure.

Every ``interval_s`` (config/health_watch.json) the hub samples its own
machine, keeps a rolling history and judges it ``ok`` / ``watch`` / ``act`` /
``unknown`` with a one-line reason and a suggested fix (restart a process
before reboot). Read-only by design: nothing here kills, restarts or closes
anything.

  * ``win_probe``  — native Windows reads (one syscall for the whole process
    table; psutil's per-process reads cost seconds on a busy box).
  * ``sampler``    — one sample: system pressure + the top processes per metric.
  * ``classifier`` — pure verdict over the history; thresholds from config.
  * ``watch``      — the loop, the history, and the act/cleared alerts.
"""
