# Webapp design language

The admin SPA at `/admin` (FastAPI sub-app, `app_web/static/`) follows the
**fleet design system** — `~/.claude/design.md` (light) + `design.dark.md`
(dark), the same canon as home-automation (reference implementation) and
app-launcher. **Light + dark themed, phone-first, no-bundler vanilla JS**
(migration: local-llm-hub#211, part of fleet-config#279).

This file records the hub's instance of the canon. When the spec evolves,
the hub follows; when the hub finds a generalisable improvement, propagate
up to `project-scaffolding` per the fleet rule.

## Tokens

Token *names* are this app's; their *values* are the spec's. Light lives in
`:root`, dark in `[data-theme="dark"]`, saturated tokens carry P3 `oklch()`
twins behind `@media (color-gamut: p3)` — see the token block at the top of
`styles.css` for the authoritative values (don't mirror them here; they are
the spec's and change with it).

| Token | Spec role | Used for |
|---|---|---|
| `--bg` | `colors.canvas` | Page background |
| `--card` | `colors.card` | Card surface, tab bar background, toast |
| `--card-off` | `colors.canvas-subtle` | Active-tab / inset surfaces, sparkline tile |
| `--fg` / `--muted` | `colors.fg` / `colors.fg-muted` | Body / secondary text |
| `--border` / `--border-muted` | `colors.border` / `border-muted` | Hairlines / in-card dividers |
| `--accent` / `--accent-fg` | `colors.accent` / `accent-fg` | Primary action, links, focus ring; text on accent |
| `--success` / `--attention` / `--danger` | status colors | State only, never decoration |
| `--radius-sm/-md/(--radius)/-pill/-nav` | `rounded.*` | 8 / 12 / 16 (cards) / pill / nav |
| `--space-xs…xl`, `--gap` | `spacing.*` | Scale + the 12px uniform gutter |
| `--font-heading-xl…caption` | `typography.*` | The five text roles — no ad-hoc sizes |
| `--control-h` | `components.control.height` | 36px shared control height |

**One card-title scale:** every card header h2 renders at `--font-body`
(`.card-header h2`), matching the vendored disclosure's `.collapse-title` —
no per-card title sizes (#215). Every card title carries a leading Lucide
glyph at `--icon-title` (18px), same as the disclosure summaries (#217).

**One app font:** monospace only where console output genuinely lives —
`<pre>` panes (log tail, model reply, trace bodies) and the Recent-sessions
session-id chip. Inline `<code>` and the dense card lists render in the app
font (#215).

Derived hub tokens (mapped onto the roles above, re-theme for free):
`--input-bg`, `--code-bg` (logpane surface), `--scrim`, and the
`--accent-soft`/`--danger-border`-style `color-mix()` status composites.

**Theme selection:** a pre-paint boot script in `index.html` stamps
`html[data-theme]` from localStorage `llmhub.theme`, falling back to the OS
`prefers-color-scheme`; the sun/moon `#themeToggleBtn` in the Hub card
header flips and persists it (`main.js` theme block — the
home-automation/app-launcher mechanism).

## Frame

```
.app {
  max-width: 772px;       /* the spec's centered desktop measure */
  margin: 0 auto;
  padding: env(safe-area-inset-top, 0) var(--gap)
           calc(env(safe-area-inset-bottom, 0) + var(--space-lg));
}

html, body {
  -webkit-tap-highlight-color: transparent;
  -webkit-text-size-adjust: 100%;
  overscroll-behavior-y: contain;
  scrollbar-gutter: stable;   /* no left/right jitter on tab change */
}
```

## Vendored components

Beyond `icons/` and `nav/`, the hub adopts the fleet's vendored components (byte-for-byte from `project-scaffolding`, per-app variation is markup + tokens only): **card** (the base `.card` contract — the hub's own `.card-header`/`.card-actions` patterns layer on top), **disclosure** (`.card--collapsible` — Health & install, the four Hub diagnostic cards, and the three Playground testers, all folded by default), **switch** (the one boolean control — the Playground Stream toggle; green on-track), and **empty-state** (every zero-items list renders the icon + reason block, never a bare dash). `escapeHtml`/`fmtClock` live once in `api.js` (sibling dedup).

## Navigation

Primary nav is the **vendored fleet component** at `app_web/static/_vendored/nav/` (copied byte-for-byte from `project-scaffolding`; never edited per-app). Desktop renders a sticky top segmented control; coarse pointers get the floating bottom-tab pill, with the standalone-PWA fixed-inset `.app` scroller shell (home-automation#303). `tabs.js` is a thin adapter bridging `initNavTabs` to the app's `onTabChange`/`setTab` API; the active tab persists under `llmhub.tab`. The login overlay hides the bar via `body.nav-hidden` (the non-`<dialog>` hook).

## Buttons

- **`.button-primary launch-btn`** — the vendored fleet primary tier
  (`app_web/static/_vendored/button/`, byte-for-byte from
  `project-scaffolding`; never edited per-app) supplies the solid accent
  fill, border, radius, weight, `48 px` min-height, and the shared
  `:disabled` recipe. `.launch-btn` is the local layout helper (full-width
  flex, icon+label gap, padding, font-size) — the photo-ocr
  `class="button-primary extract-btn"` pattern. Exactly one per view: the
  Services card, the Health & install disclosure, and the login overlay
  each hold their own. Use for: Launch Docker + Langfuse, Fix all, Sign in.
- **`.ghost-btn`** — secondary text-style button, `min-height: 36 px`,
  border + transparent background. Use for: Re-check, Clear, Pause
  log, Choose file, the individual install-row fixers.
- **`.ghost-btn.primary`** — the in-form primary (Playground Send /
  Generate / Speak / Download): an accent *tint*, never a solid fill —
  home-automation's `.range-tab.active` recipe (`--accent-soft` background,
  accent text, `--accent-border-strong` border; #215).
- **`.icon-btn`** — row-level button, `52 × 60 px`, lives inside a
  `.row-actions` column on the right of an `.app-item`. Use for:
  Start / Stop / Log / Ping on the Models tab.

Segmented controls (Code vendor/period, Playground max-tokens) follow the
same ghost pattern: transparent container, card-off option pills, the active
one an accent tint (home-automation `.range-tabs`; #215). File pickers hide
the native input behind a ghost "Choose file" button with the selected
filename to its right (`.file-row`).

Every interactive element has `min-height: 36 px` and a
`:focus-visible` ring (`2px solid var(--accent); outline-offset: 2px`).

## Row pattern (`.app-item`)

Lifted verbatim from app-launcher's launcher tiles:

```
<li class="app-item">
  <div class="app-main">title + meta + status badge</div>
  <div class="row-actions">
    <button class="icon-btn"><svg class="icon"><use href="#i-play"></use></svg></button>
    <button class="icon-btn danger"><svg class="icon"><use href="#i-square"></use></svg></button>
    ...
  </div>
</li>
```

Inline detail panes (e.g. the per-model log tail) appear as a
**sibling `<li>` right after the row**, not as a child of the row.
This keeps the row's `display: flex` context intact while letting
the pane claim full container width — same trick as app-launcher's
`.jobs-history-li` (see the comment at
`app-launcher/app/webapp/static/styles.css:432`).

## Hub-specific layout

The hub diverges from app-launcher in one place — it has more
diagnostic surfaces (live request ring, per-backend counters, error
ring, server log) than the launcher needs. Each of the four is a
**vendored disclosure card** (`.card--collapsible`), folded by default —
the same pattern as Health & install (#215; this replaced the earlier
Compact/Expanded density toggle and its dual DOM trees). SSE/poll
renders keep updating the lists while folded, so opening one is
instant. The build-identity footer is home-automation's `.page-foot`:
one centered muted line, no links.

## Live status indicator

The `.hub-live-status` dot + word sits inline in the Hub / Telemetry card
headers (it replaced the old always-on status strip). It tints by state via
the status tokens — `--success` up, `--attention` degraded, `--danger`
unreachable — which signal state only, never decoration.

## Icons

The SPA's UI glyphs are **Lucide**, the canonical fleet icon set (`~/.claude/design.md` → "Icons"), adopted via the **vendored** component at `app_web/static/_vendored/icons/` (sprite + `icons.js` helper, copied verbatim from `project-scaffolding`; issue #139).

- **How to reference one.** Static markup: `<svg class="icon"><use href="#i-NAME"></use></svg>`. From JS: `import { icon } from './_vendored/icons/icons.js'` then `el.innerHTML = icon('NAME')`. Glyphs inherit `currentColor` (the `.icon` CSS contract), so they recolor for free in any context — tint a status glyph by setting its parent's `color`.
- **The sprite is injected server-side**, once, after `<body>` (see `app_web/routers/misc.py::_icon_sprite`) from the single `icons-sprite.html` source. It must stay **inline** in-document — iOS Safari silently fails to resolve external `<use href="file.svg#id">` references.
- **Vendor verbatim.** Don't edit `icons.js` per-app; the only per-app change is *which `<symbol>` glyphs* live in `icons-sprite.html` (add the ones a view needs from lucide-static 0.544.0, keeping the `i-NAME` id + `fill="none"`). To change the helper or share a new glyph fleet-wide, change it in `project-scaffolding` and re-vendor. Full recipe: `_vendored/icons/README.md`.
- **No backend-identity glyph.** The Models tab rows previously carried a per-provider emoji (Claude/Gemini/whisper/TTS/llama); these were **removed** entirely rather than substituted — provider brands have no faithful Lucide equivalent, and the backend name already labels each row. Emoji remain only in genuine prose/content (the page `<title>`, the transcription-dictionary editor's help text) — never in chrome.

## What we deliberately *don't* copy from app-launcher

- **`.detached-toggle` / `.edit-toggle`** — these label-as-button
  patterns are launcher-specific (Detached console mode, Settings
  edit-mode). The hub has no equivalent.
- **`.terminal-overlay` and the xterm.js host** — the launcher
  embeds full PTYs over WebSocket. The hub's "log" view is a
  one-way SSE tail, doesn't need a terminal grid.
- **`.coding-card` / `.coding-summary`** — launcher-only (the
  Coding tab and its options).

## Divergence we track

| Aspect | Hub | app-launcher | Reason |
|---|---|---|---|
| `--code-bg` token | present | absent | The hub's logpane needs its own inset surface (mapped to `--card-off`); launcher uses xterm.js which paints its own background. |
| Counters table | compact columns (`p50`/`p95` in seconds, one `I/O tok` column, `.td-trunc` first column) inside `.counters-wrap` | n/a — launcher has no equivalent | Fits the phone width without horizontal scrolling (#215); the scroll wrap stays as a safety net only. |
| Icon set | Lucide via vendored `_vendored/icons/` | varied | Adopted the canonical fleet Lucide set per `~/.claude/design.md` (issue #139); nav tabs now show icon + label. Backend-identity emoji were dropped (no faithful Lucide equivalent; the backend name labels the row). See the **Icons** section above. |

## When to update this file

- A new token lands in the canonical → mirror here.
- The hub picks a new pattern that's *generalisable* → file an issue
  on `project-scaffolding` (per the fleet rule), then update both
  this file and the canonical when the pattern lands.
- A divergence is deliberately introduced → add a row to the
  divergence table with the reason.
