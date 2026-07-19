# `xterm` — in-browser terminal renderer

Vendored **byte-for-byte** from `app-launcher`'s
`app/webapp/static/vendor/{xterm.js,xterm.css,addon-fit.js}` (verified via
`md5sum` at copy time — local-llm-hub#309). Those files are themselves the
upstream [xterm.js](https://xtermjs.org/) UMD bundle (MIT license) plus its
official `FitAddon`; app-launcher is just the closest existing fleet copy, not
the source of truth. Only the two files this app actually uses are vendored —
`addon-web-links.js` and `addon-webgl.js` (also present in app-launcher) are
**not** copied here; the Machines terminal shim (`machines_terminal.js`) does
not use either.

## Files

| File | Role |
| --- | --- |
| `xterm.js` | The core `Terminal` class — exposed as `window.Terminal` when loaded via a plain `<script>` tag. |
| `xterm.css` | Required visual styling for the terminal's DOM (cursor, selection, scrollback viewport). |
| `addon-fit.js` | `FitAddon` — resizes the terminal to fill its host element; exposed as `window.FitAddon.FitAddon`. |

## Usage

```html
<link rel="stylesheet" href="/admin/static/_vendored/xterm/xterm.css">
<script src="/admin/static/_vendored/xterm/xterm.js"></script>
<script src="/admin/static/_vendored/xterm/addon-fit.js"></script>
```

```js
const term = new window.Terminal({ ... });
const fit = new window.FitAddon.FitAddon();
term.loadAddon(fit);
term.open(hostElement);
fit.fit();
```

See `machines_terminal.js` for this app's minimal connection shim (WebSocket
protocol documented there) — deliberately NOT a copy of app-launcher's
`terminal*.js` modules, which are welded to its own SPA (session cache,
mirror windows, on-screen keys, image paste). This app only needs open →
stream → resize → close against `WS /admin/api/machines/{id}/terminal`.

## Don't diverge

Do not hand-edit `xterm.js` / `xterm.css` / `addon-fit.js` — to pick up a
newer xterm.js release, re-copy from an upstream build (or app-launcher's
copy, if it has updated) and re-verify with `md5sum`.
