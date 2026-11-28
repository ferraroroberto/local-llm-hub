# `settings-group` — the full Settings page

The fleet's **Settings page**: a full page opened from the header gear, never a small pop-up. On a phone it fills the screen; on desktop it is a centred column at the layout measure (772px). Its body is **iOS-style inset groups** on a sunk canvas: each group a sentence-case label above a borderless card of one-line rows. Contract: the fleet design system → **settings group** and the `settings-group` token entry (`pageInset` 20px, `rounded` 20px, no border, content `spacing.md` inside, labels 16px/700 `fg-muted` in sentence case). The shared piece is the page shell, the groups, the row types and the detail sheet's fit; each app supplies its own rows.

## Files

| File | Role |
| --- | --- |
| `settings-group.css` | Visual contract: the page shell and header, the group label, the inset card, the row and its four trailing variants, the footnote, and the detail sheet's fit. References design tokens only. |
| `settings-group.html` | Markup skeleton to copy and adapt. |

## How to vendor

1. Copy this `settings-group/` folder **verbatim** into your app's static dir (`app/webapp/static/_vendored/settings-group/`). Do **not** edit `settings-group.css` per-app.
2. Vendor `card/`, `switch/`, `icon-button/`, `modal/`, `button/` and `icons/` too: a group is a `.card`, the inline boolean is the vendored switch, the close is an icon-button, a detail sheet is the vendored modal, its Done is `button-primary`, the glyphs come from the sprite.
3. Link the CSS after `card.css`, `switch.css` and `modal.css`:
   ```html
   <link rel="stylesheet" href="/static/_vendored/settings-group/settings-group.css">
   ```
4. Paste the skeleton once: the page `<dialog>` with its header, then one label + card per group, keeping the row variants you need. Open it with `showModal()` from the header gear and close it from its close button (Esc closes it too). Wire each control in your own JS (save as it changes, text and numbers on blur, with a `toast`).

## Markup contract

```html
<dialog class="settings-page" aria-labelledby="settingsTitle">
  <header class="settings-page-head">
    <h2 class="settings-page-title" id="settingsTitle">Settings</h2>
    <button type="button" class="icon-button settings-page-close" aria-label="Close settings">…#i-x…</button>
  </header>
  <div class="settings-page-body">
    <h3 class="settings-group-label" id="groupDisplay">Display</h3>
    <section class="card settings-group" aria-labelledby="groupDisplay">
      <button type="button" class="settings-row" aria-haspopup="dialog">…</button>
      <label class="settings-row">…</label>
    </section>
    <p class="settings-footnote">…</p>
  </div>
</dialog>
```

- **The page is a modal `<dialog class="settings-page">`** that fills the viewport (`100dvh`), scrolls itself and paints the sunk canvas behind the cards: in light a mix of 8% ink into the card colour, in dark the app canvas (`--bg`), one step under the `#161b22` cards. Its sticky **header** is the app header's colour (`--bg`), holds the title (body size, 700) on the content edge and the close glyph ending on the card's inner edge, and carries the top safe-area inset. On a wide screen the head and body pad themselves so the content is a centred column at `--measure` (fallback 772px).
- **Geometry** (390px phone): cards at x 20–370 with 20px corners and no border, content at 36–354; at 1440px cards at 354–1086. The header title, the group labels, the row glyphs and the footnote all start on the content edge.
- **The group label sits outside and above its card**: `.settings-group-label`, muted, sentence case, 16px/700, about 30px above each group after the first. It is never all caps; the `overline` role is the date-group header only, never a Settings label.
- **A row** is `.settings-row`: `.settings-row-icon` (the lead glyph, `--icon-title`), `.settings-row-label`, then one trailing item. The label never wraps and keeps its whole width up to 70% of the row; the trailing item takes the rest, so a long value ellipsizes first. A row with one muted hint line wraps the two in `.settings-row-text` (`.settings-row-label` + `.settings-row-hint`). A row may drop the lead glyph.
- **Every trailing item ends on the card's inner edge** (16px in, symmetric with the left), whatever the variant.
- **Separators** run from the row's title to the card's inner edge: past the lead glyph when the row has one, from the label when it has none. The first row has none.
- **Trailing variants** (one per row):

  | Variant | Element | Trailing markup |
  | --- | --- | --- |
  | Value + chevron (opens a sheet) | `<button class="settings-row" aria-haspopup="dialog">` | `.settings-row-value` + `.settings-row-chevron` (`#i-chevron-right`) |
  | Select (a picker in place) | `<label class="settings-row">` | `<select class="settings-row-select">` + `.settings-row-chevron` (`#i-chevron-down`) |
  | Switch | `<label class="settings-row">` | the vendored `.toggle` switch; the label names it and a tap anywhere on the row toggles it |
  | Number field | `<label class="settings-row">` | `.settings-row-number` holding `<input class="settings-row-input" type="number" inputmode="numeric">` + `.settings-row-unit` |

- **A value that can be cut off carries a `title` with its full text** (a path, a long name), so the ellipsis never hides it for good.
- **A value is never boxed.** The value is muted text, and a select inside a row is `.settings-row-select` (borderless, muted, right-aligned), never a bordered `.select-native` pill. Keep `.select-native` for forms and modal rows.
- **A number field is 80px wide in every app**, at the body size (16px, so iOS never zooms on focus), figures right-aligned in `tabular-nums`, with its unit after it in `.settings-row-unit`, and no prefix sign. Its box is the `--hit-min` floor (44px) tall on every pointer, because an input cannot grow its hit area with a `::before`: the visible 36px control is the padding box inside a transparent vertical band, and its boundary is an inset shadow.
- **Tone:** `data-tone="attention"` or `data-tone="danger"` on `.settings-row-value` for an exception only (a failing check, a setting that needs you). A health check that passes reads as a plain value.
- **Wide layout:** `aria-current="true"` on the row whose sheet is open beside the list tints it `accent-soft` and moves its value and glyphs to `accent-text`.
- A row that acts on tap (Delete all…) carries no chevron. A control changed often (Text size, a segmented mode) is placed in a row without a value or chevron.
- **The footnote** (`.settings-footnote`) sits on the canvas under its card, on the content edge.
- Usage tracking: each interactive control carries a constant `data-track`, and the page and each sheet a constant `data-track-view`, as everywhere in the fleet.

## Detail sheets

A value + chevron row opens a detail sheet: the vendored modal with `settings-sheet` added.

```html
<dialog class="detail-dialog settings-sheet" aria-labelledby="sheetTitle">
  <div class="detail-card">
    <div class="detail-header"><h2 id="sheetTitle" tabindex="-1" autofocus>Language</h2><button class="icon-button detail-close" aria-label="Close">…</button></div>
    <div class="settings-sheet-body">…</div>
    <div class="detail-actions"><button type="button" class="button-primary detail-save-btn">Done</button></div>
  </div>
</dialog>
```

The sheet always fits the visible screen (a `dvh` cap less both safe-area insets), its title and close stay pinned at the top, only `.settings-sheet-body` scrolls, and the Done footer stays pinned at the bottom with the home-indicator inset under it. Every sheet has the footer; a read-only sheet's footer holds Done alone.

## Adopting over an `overline` Settings pane

An app whose Settings is a pane of `overline` headers adopts the page this way: wrap the groups in the page `<dialog>` and header above and open it from the gear; rename `.settings-overline` to `.settings-group-label` and write each label in sentence case; add `settings-sheet` and a `.settings-sheet-body` to each row's detail sheet; delete any app rule that re-borders or re-pads the groups.

## Required design tokens

| Token | Light value | Used for |
| --- | --- | --- |
| `--font-body` | `1rem` | page title, group label, row label, number field |
| `--font-body-sm` | `0.875rem` | value, select, hint, unit, footnote |
| `--muted` | `#656d76` | group label, glyphs, value, hint, unit, footnote |
| `--ink` | `#1f2328` | label, number field text; mixed into the light canvas |
| `--bg` | `#ffffff` | header; the dark canvas |
| `--card` | `#ffffff` | the cards; the light canvas's base |
| `--line-muted` | `#d8dee4` | the separator between rows |
| `--card-2` | `#f6f8fa` | the pressed row |
| `--accent` | `#0969da` | focus ring |
| `--accent-soft`, `--accent-text` | mixed / `#0550ae` | the selected row on a wide screen |
| `--attention-text`, `--danger-text` | `#7d4e00` / `#a40e26` | a toned value |
| `--control-h`, `--row-sm`, `--row-md` | `36px` / `44px` / `52px` | number field's visible height, select height, row and header height |
| `--hit-min` (fallback `44px`) | `44px` | the number field's box height (target floor) |
| `--measure` (fallback `772px`) | `772px` | the desktop column |
| `--control-border`, `--input-bg` | `#818b98` / `var(--card-off)` | number field border and fill |
| `--radius-md` | `12px` | number field corners |
| `--icon-title`, `--icon-inline` | `18px` / `16px` | lead glyph and close, chevron |
| `--space-sm`, `--space-md`, `--space-lg` | `8px` / `16px` / `24px` | label, content padding, sheet inset, page bottom |

## Don't diverge

`settings-group.css` is vendored verbatim: to change the contract, change it **here in `project-scaffolding`** and re-vendor downstream. An app's own Settings stylesheet keeps only what is its own (a sheet's contents, a one-off row); it never re-declares `.settings-page`, `.settings-group-label`, `.settings-group` or `.settings-row`. Streamlit POC spikes are exempt.
