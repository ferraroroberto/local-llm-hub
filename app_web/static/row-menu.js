/* Row ⋮ menu placement — keeps an open `.row-menu-list` reachable on a phone.
 *
 * The hub's row menu is a native <details class="row-menu"> whose list used to
 * hang `position: absolute` under its kebab, so on the last visible row it ran
 * behind the floating nav pill and off the screen (#767). While a menu is
 * open its list is `position: fixed` (see `.row-menu[open]` in styles.css) and
 * this module writes its top/left in viewport coordinates: right-aligned under
 * the kebab, flipped above it when below would reach the pill or the bottom
 * edge, and re-fitted on every scroll and resize while open.
 *
 * The placement rule is app-launcher's row menu (`row-menu.js`, app-launcher
 * #1507/#1508), reduced to this markup. Kept self-contained so it can be swapped
 * for the vendored menu when project-scaffolding#362 ships one.
 */

// Keep a viewport-placed menu this far inside the screen edges.
const EDGE = 8;

// The vertical band [lo, hi] (viewport px) an open menu may occupy: the
// viewport, minus the floating bottom-tab pill (`nav.tabs`, fixed on the phone)
// while it shows. The desktop rail starts at the top edge and is skipped; a
// menu inside a <dialog> sits in the top layer, above the pill.
function usableBand(menu) {
  let hi = document.documentElement.clientHeight - EDGE;
  const nav = document.querySelector('nav.tabs');
  if (nav && !menu.closest('dialog')) {
    const cs = getComputedStyle(nav);
    const r = nav.getBoundingClientRect();
    if (cs.position === 'fixed' && cs.visibility === 'visible' && r.height > 0 && r.top > 0) {
      hi = Math.min(hi, r.top - EDGE);
    }
  }
  return { lo: EDGE, hi: hi };
}

// Where a menu of `height` whose natural top is `top` should go: there when it
// fits the band, else mirrored across `ref` (the kebab's box), else clamped
// with its height capped to the band.
function fitVertical(top, height, ref, band) {
  const fits = function (t) { return t >= band.lo && t + height <= band.hi; };
  if (fits(top)) return { top: top, maxHeight: null };
  const flipped = ref.top + ref.bottom - top - height;
  if (fits(flipped)) return { top: flipped, maxHeight: null };
  const room = Math.max(0, band.hi - band.lo);
  if (height > room) return { top: band.lo, maxHeight: room };
  return { top: Math.min(Math.max(band.lo, top), band.hi - height), maxHeight: null };
}

function capHeight(list, maxHeight) {
  list.style.maxHeight = maxHeight == null ? '' : Math.floor(maxHeight) + 'px';
  list.style.overflowY = maxHeight == null ? '' : 'auto';
}

/** Place one open row menu. Safe to call on a closed one (no-op). */
export function placeRowMenu(details) {
  if (!details || !details.open) return;
  const list = details.querySelector(':scope > .row-menu-list');
  const toggle = details.querySelector(':scope > .row-menu-toggle');
  if (!list || !toggle) return;
  // Re-measuring drops the cap for a moment; keep a capped menu's scroll.
  const scrolled = list.scrollTop;
  capHeight(list, null);
  const anchor = toggle.getBoundingClientRect();
  const size = list.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const fit = fitVertical(anchor.bottom, size.height, anchor, usableBand(list));
  const left = Math.min(
    Math.max(EDGE, anchor.right - size.width),
    Math.max(EDGE, vw - EDGE - size.width)
  );
  list.style.top = Math.round(fit.top) + 'px';
  list.style.left = Math.round(left) + 'px';
  capHeight(list, fit.maxHeight);
  list.scrollTop = scrolled;
  list.dataset.placed = '';
}

function placeOpenMenus() {
  document.querySelectorAll('.row-menu[open]').forEach(placeRowMenu);
}

let frame = null;
function schedule(ev) {
  // A capped menu scrolling its own items moves nothing underneath it.
  if (ev && ev.type === 'scroll' && ev.target && ev.target.closest
      && ev.target.closest('.row-menu-list')) return;
  if (frame != null || !document.querySelector('.row-menu[open]')) return;
  frame = requestAnimationFrame(function () { frame = null; placeOpenMenus(); });
}

let wired = false;
/** Idempotent. Call once at startup. */
export function wireRowMenus() {
  if (wired) return;
  wired = true;
  // `toggle` does not bubble: listen in the capture phase. Placing here, in
  // the event's own task, is before the next paint of the opened list.
  document.addEventListener('toggle', function (ev) {
    const d = ev.target;
    if (!d || !d.matches || !d.matches('.row-menu')) return;
    if (d.open) placeRowMenu(d);
    else {
      const list = d.querySelector(':scope > .row-menu-list');
      if (list) { delete list.dataset.placed; capHeight(list, null); }
    }
  }, true);
  window.addEventListener('resize', schedule);
  // Capture: the page and any inner scroller move the row without bubbling.
  document.addEventListener('scroll', schedule, true);
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', schedule);
    window.visualViewport.addEventListener('scroll', schedule);
  }
}
