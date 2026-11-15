# `settings-group` — the Settings pane's inset groups

The fleet's **Settings pane**: an `overline` header over an inset `card`, the card holding one-line rows split by a hairline. Each row is a lead glyph, a label and one trailing item. Contract: `~/.claude/design.md` → **settings group** (inset groups under `overline` headers; each row a lead `icons.size.title` glyph, a label, the current value in `fg-muted` and a chevron to a detail sheet; a setting changed often stays inline). Normalized from the hand-built `.settings-overline` + `.card.settings-group` copies in app-launcher, task-os and home-automation.

## Files

| File | Role |
| --- | --- |
| `settings-group.css` | Visual contract: the header, the inset card, the row and its four trailing variants, the footnote. References design tokens only. |
| `settings-group.html` | Markup skeleton to copy and adapt. |

## How to vendor

1. Copy this `settings-group/` folder **verbatim** into your app's static dir (`app/webapp/static/_vendored/settings-group/`). Do **not** edit `settings-group.css` per-app.
2. Vendor `card/`, `switch/` and `icons/` too; the group is a `.card`, the inline boolean is the vendored switch, the glyphs come from the sprite.
3. Link the CSS after `card.css` and `switch.css`:
   ```html
   <link rel="stylesheet" href="/static/_vendored/settings-group/settings-group.css">
   ```
4. Paste the skeleton once per group, keep the row variants you need, and wire each control in your own JS (save as it changes, text and numbers on blur, with a `toast`).

## Markup contract

```html
<h3 class="settings-overline" id="groupDisplay">Display</h3>
<section class="card settings-group" aria-labelledby="groupDisplay">
  <button type="button" class="settings-row" aria-haspopup="dialog">…</button>
  <label class="settings-row">…</label>
</section>
<p class="settings-footnote">…</p>
```

- **The header sits outside the card**, never inside it: `.settings-overline` keeps a positive gap above the card at every text size, so it can never overlap the card's border. The first header in its container drops its top margin. `.settings-overline` is the overline role; it is the one place Settings uses capitals.
- **A row** is `.settings-row`: `.settings-row-icon` (the lead glyph, `--icon-title`), `.settings-row-label`, then one trailing item. The label never wraps and keeps its whole width up to 70% of the row; the trailing item takes the rest, so a long value ellipsizes first. A row with one muted hint line wraps the two in `.settings-row-text` (`.settings-row-label` + `.settings-row-hint`).
- **Trailing variants** (one per row):

  | Variant | Element | Trailing markup |
  | --- | --- | --- |
  | Value + chevron (opens a sheet) | `<button class="settings-row" aria-haspopup="dialog">` | `.settings-row-value` + `.settings-row-chevron` (`#i-chevron-right`) |
  | Select (a picker in place) | `<label class="settings-row">` | `<select class="settings-row-select">` + `.settings-row-chevron` (`#i-chevron-down`) |
  | Switch | `<label class="settings-row">` | the vendored `.toggle` switch; the label names it and a tap anywhere on the row toggles it |
  | Number field | `<label class="settings-row">` | `.settings-row-number` holding `<input class="settings-row-input" type="number" inputmode="numeric">` + `.settings-row-unit` |

- **A value that can be cut off carries a `title` with its full text** (a path, a long name), so the ellipsis never hides it for good.
- **A value is never boxed.** The value is muted text, and a select inside a row is `.settings-row-select` (borderless, muted, right-aligned), never a bordered `.select-native` pill. Keep `.select-native` for forms and modal rows.
- **A number field is 80px wide in every app**, at the body size (16px, so iOS never zooms on focus), figures right-aligned in `tabular-nums`, with its unit after it in `.settings-row-unit`, and no prefix sign.
- **Tone:** `data-tone="attention"` or `data-tone="danger"` on `.settings-row-value` for an exception only (a failing check, a setting that needs you). A health check that passes reads as a plain value.
- **Wide layout:** `aria-current="true"` on the row whose sheet is open beside the list tints it `accent-soft` and moves its value and glyphs to `accent-text`.
- A row that acts on tap (Delete all…) carries no chevron. A control changed often (Text size, a segmented mode) is placed in a row without a value or chevron.
- Usage tracking: each interactive control carries a constant `data-track`, as everywhere in the fleet.

## Required design tokens

| Token | Light value | Used for |
| --- | --- | --- |
| `--font-caption` | `0.75rem` | the overline |
| `--font-body` | `1rem` | row label, number field |
| `--font-body-sm` | `0.875rem` | value, select, hint, unit, footnote |
| `--muted` | `#656d76` | overline, glyphs, value, hint, unit |
| `--ink` | `#1f2328` | label, number field text |
| `--line-muted` | `#d8dee4` | the hairline between rows |
| `--card-2` | `#f6f8fa` | the pressed row |
| `--accent` | `#0969da` | focus ring |
| `--accent-soft`, `--accent-text` | mixed / `#0550ae` | the selected row on a wide screen |
| `--attention-text`, `--danger-text` | `#7d4e00` / `#a40e26` | a toned value |
| `--control-h`, `--row-sm`, `--row-md` | `36px` / `44px` / `52px` | number field height, select height, row height |
| `--control-border`, `--input-bg` | `#818b98` / `var(--card-off)` | number field border and fill |
| `--radius-md` | `12px` | number field corners |
| `--icon-title`, `--icon-inline` | `18px` / `16px` | lead glyph, chevron |
| `--space-sm`, `--space-md`, `--gap` | `8px` / `16px` / `12px` | header and group spacing |

## Don't diverge

`settings-group.css` is vendored verbatim: to change the contract, change it **here in `project-scaffolding`** and re-vendor downstream. An app's own Settings stylesheet keeps only what is its own (a sheet's contents, a one-off row); it never re-declares `.settings-overline`, `.settings-group` or `.settings-row`. Streamlit POC spikes are exempt.
