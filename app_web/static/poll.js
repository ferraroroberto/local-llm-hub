/* Visible-only, single-flight background polling for the admin page.
 *
 * Every background read goes through startPoll so a hidden tab costs nothing:
 * while document.hidden no tick fires, and when the tab shows again each poll
 * makes one catch-up read before resuming its cadence. A read never overlaps
 * the one before it, and the next tick is timed from when a read settles, so a
 * slow endpoint stretches the cadence instead of piling reads up behind it.
 */

const polls = new Set();
let visibilityWired = false;

function wireVisibility() {
  if (visibilityWired) return;
  visibilityWired = true;
  document.addEventListener('visibilitychange', function () {
    polls.forEach(function (p) {
      if (document.hidden) p.pause(); else p.resume();
    });
  });
}

/* Call fn every ms while the page is visible. fn may return a promise; a
 * rejection is swallowed (each caller already renders its own failure state).
 * opts.immediate runs the first read now instead of after one interval.
 * Returns { stop() }. */
export function startPoll(fn, ms, opts) {
  wireVisibility();
  let timer = null;
  let inFlight = false;
  let stopped = false;

  function clear() {
    if (timer !== null) { clearTimeout(timer); timer = null; }
  }
  function schedule() {
    clear();
    if (stopped || document.hidden) return;
    timer = setTimeout(run, ms);
  }
  function run() {
    timer = null;
    if (stopped || document.hidden || inFlight) return;
    inFlight = true;
    Promise.resolve()
      .then(fn)
      .catch(function () { /* the caller renders its own failure state */ })
      .then(function () { inFlight = false; schedule(); });
  }

  const handle = {
    stop: function () { stopped = true; clear(); polls.delete(handle); },
    pause: clear,
    // The one catch-up read on return; a read still in flight schedules on settling.
    resume: function () { if (!stopped && !inFlight) { clear(); run(); } },
  };
  polls.add(handle);
  if (opts && opts.immediate) run(); else schedule();
  return handle;
}
