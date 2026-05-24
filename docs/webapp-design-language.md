# Webapp design language

The admin SPA at `/admin` (FastAPI sub-app, `app_web/static/`) mirrors
the canonical fleet implementation in
`E:\automation\app-launcher\app\webapp\static\`. Both webapps are
**dark, phone-first, no-bundler vanilla JS**. The intent is that the
two webapps in the user's fleet feel like the same product, not
sibling-but-divergent UIs.

This file records the mapping. When the canonical evolves, the hub
follows; when the hub finds a generalisable improvement, propagate up
to `project-scaffolding` per the fleet rule.

## Tokens

Both stylesheets use the same `:root` token set:

| Token | Value | Used for |
|---|---|---|
| `--bg` | `#0a0a0a` | Page background, input fields, log-history tile |
| `--bg-elev` | `#161616` | Card surface, tab bar background |
| `--bg-elev-2` | `#202020` | Active-tab background, sparkline tile, toast |
| `--fg` | `#f3f3f3` | Body text |
| `--muted` | `#9a9a9a` | Labels, secondary text, button-ghost text |
| `--accent` | `#4a8af3` | Primary action, links, focus ring, sparkline stroke |
| `--good` | `#4caf50` | "Up" status, success toast border, running badge |
| `--warn` | `#f0a100` | Warning state, adopted-process badge |
| `--danger` | `#cc3344` | Destructive action, error border, stopped state |
| `--radius` | `14px` | Card border-radius |
| `--pad` | `16px` | Default container padding |
| `--border` | `#2a2f42` | All hairline borders |

Hub-only token: `--code-bg: #0d0d0d` for `<pre class="logpane">`
(llama-server log tails need a darker surface than the cards).
Mirrors app-launcher's `.jobs-output-tail` background.

## Frame

```
.app {
  max-width: 720px;       /* phone-first; counters table scrolls in-card */
  margin: 0 auto;
  padding: env(safe-area-inset-top, 0) var(--pad)
           calc(env(safe-area-inset-bottom, 0) + 24px);
}

html, body {
  -webkit-tap-highlight-color: transparent;
  -webkit-text-size-adjust: 100%;
  overscroll-behavior-y: contain;
  scrollbar-gutter: stable;   /* no left/right jitter on tab change */
}
```

## Buttons

Three primitives, no modifier matrix:

- **`.launch-btn`** — primary per-card action, `min-height: 56 px`,
  full-width, accent background. Use for: Restart hub, Fix all, Send
  prompt, Sign in.
- **`.ghost-btn`** — secondary text-style button, `min-height: 36 px`,
  border + transparent background. Use for: Re-check, Clear, Pause
  log, the individual install-row fixers.
- **`.icon-btn`** — row-level button, `52 × 60 px`, lives inside a
  `.row-actions` column on the right of an `.app-item`. Use for:
  Start / Stop / Log / Ping on the Models tab.

Every interactive element has `min-height: 36 px` and a
`:focus-visible` ring (`2px solid var(--accent); outline-offset: 2px`).

## Row pattern (`.app-item`)

Lifted verbatim from app-launcher's launcher tiles:

```
<li class="app-item">
  <div class="app-main">title + meta + status badge</div>
  <div class="row-actions">
    <button class="icon-btn">▶</button>
    <button class="icon-btn danger">■</button>
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
ring, server log) than the launcher needs. To keep the
above-the-fold surface manageable on a phone:

- A **density toggle** (`.segmented` with Compact / Expanded) lives
  in a small "Layout" card. The preference is persisted to
  `localStorage` under `llmhub.hub.density`.
- **Compact** (default) collapses the four diagnostic surfaces into
  one card with internal sub-tabs.
- **Expanded** restores the classic stack of four cards.

Both modes render to the same backing `state.*` arrays — `hub.js`
writes to both DOM trees, CSS controls which is visible. This avoids
DOM moves on toggle and means SSE updates land in both regardless of
the active mode.

## Status strip

Replaces the small status dot that used to live in the topbar. The
strip is sticky-adjacent (sits between `.tabs` and the panes) and
shows on every tab, not just Hub. Background tints by state:

- `var(--good)` 14% alpha when the hub is up
- `var(--warn)` 14% alpha when degraded
- `var(--danger)` 14% alpha when unreachable

Border alpha matches at 45%.

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
| `--code-bg` token | present | absent | The hub's logpane needs a surface darker than `--bg-elev`; launcher uses xterm.js which paints its own background. |
| Counters table | wrapped in `.counters-wrap` for horizontal scroll | n/a — launcher has no equivalent | Seven-column table doesn't fit 720 px phone width; alternative was bumping the container to 820 px (declined, would diverge from canonical). |
| Tab icons | `🌐 🧠 🧪` | varied | Hub set chosen for Windows Segoe UI Emoji compatibility; the satellite glyph rendered as tofu on default Windows. Will retire when the shared icon family from issue #6 ships. |

## When to update this file

- A new token lands in the canonical → mirror here.
- The hub picks a new pattern that's *generalisable* → file an issue
  on `project-scaffolding` (per the fleet rule), then update both
  this file and the canonical when the pattern lands.
- A divergence is deliberately introduced → add a row to the
  divergence table with the reason.
