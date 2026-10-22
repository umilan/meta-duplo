---
name: design-system
description: The meta-duplo design system as implemented, covering every token in tokens.css (light, dark, compact), base element styles, the component inventory in components.css, theme.js preferences and the settings sheet, inline-SVG icons, and the rules for adding UI. Use it before writing or reviewing any template or CSS so new UI matches the current look and the ADR-017 scale.
---

# Design system (ADR-017)

Sources: `meta_duplo/web/static/css/tokens.css`, `base.css`, `layout.css`, `components.css`,
`static/js/theme.js`, and the settings sheet in `templates/base.html`. Modelled on the Atlassian Design System:
one look per kind of control, shared by every screen. Stylesheets load in this order: tokens → base → layout →
components. There is no build step and no CSS framework.

## 1. The scale

Every length is on **2 · 4 · 8 · 16 · 24 · 32 · 40 · 48 · 64**; larger sizes are multiples of 8 (e.g. 176, 208,
240, 304, 336, 560, 720, 1040, 1504). **1px hairlines are the only exception** (2px edges are on the scale).
Column widths snap to 8 (`workbench.snap`: 40–904; also while dragging). Breakpoints in use are multiples of 8:
208, 760, 1024, 1040, 1088, 1240.

## 2. Tokens (`:root`)

### 2.1 Type, spacing, shape, sizes

| Token | Value | Compact (`:root[data-density="compact"]`) | Use |
|---|---|---|---|
| `--font-ui` | `system-ui, -apple-system, "Helvetica Neue", sans-serif` | | body |
| `--font-mono` | `ui-monospace, "SF Mono", Menlo, monospace` | | code, paths, patterns |
| `--space-025` | 2px | | |
| `--space-050` | 4px | | |
| `--space-100` | 8px | | |
| `--space-200` | 16px | | |
| `--space-300` | 24px | | |
| `--space-400` | 32px | | |
| `--space-500` | 40px | | |
| `--space-600` | 48px | | |
| `--space-800` | 64px | | |
| `--text-sm` / `--line-sm` | 12px / 16px | unchanged | labels, table cells, chips |
| `--text-md` / `--line-md` | 14px / 20px | 12px / 16px | body, controls |
| `--text-lg` / `--line-lg` | 20px / 24px | 16px / 24px | h2, dialog titles |
| `--text-xl` / `--line-xl` | 24px / 32px | 20px / 24px | h1, big numbers |
| `--radius-sm` | 4px | | controls, chips, menu items |
| `--radius` | 8px | | panels, cards, sheets, dialogs |
| `--radius-full` | 999px | | pills |
| `--control-h` | 32px | 24px | buttons, inputs, selects, menu items |
| `--control-h-sm` | 24px | 24px | small buttons, chips, filter pills |
| `--control-h-xs` | 24px | 16px | in-cell ⋯ buttons, srclink |
| `--check` | 16px | | checkbox, radio |
| `--icon` | 16px | | SVG icons |
| `--cell-py` | 4px | 2px | table cell vertical padding |
| `--row-h` | 32px | 24px | Workbench row height (border included) |
| `--thumb-h` | 24px | 16px | table thumbnails (4:3) |
| `--gap` | 16px | 8px | layout gaps |
| `--pad` | 24px | 16px | page padding |
| `--shadow` | `0 2px 4px rgb(0 0 0 / 6%)` | | cards, panels |
| `--shadow-pop` | `0 8px 24px rgb(0 0 0 / 16%)` | | menus, sheets, toast, job |
| `--shadow-modal` | `0 16px 48px rgb(0 0 0 / 24%)` | | dialogs, lightbox |

Compact changes component tokens only, never the scale.

### 2.2 Colour (light default → dark)

| Token | Light | Dark | Meaning |
|---|---|---|---|
| `--bg` | `#fbfbfa` | `#161617` | page |
| `--surface` | `#ffffff` | `#1f1f21` | panels, controls, table |
| `--surface-2` | `#f3f3f1` | `#29292c` | headers, hovers, chips |
| `--text` | `#1d1d1f` | `#f2f2f2` | |
| `--muted` | `#6e6e73` | `#a1a1a6` | secondary text, icons |
| `--line` | `#e5e5e7` | `#333336` | hairlines |
| `--line-strong` | `#c7c7cc` | `#48484c` | control borders, dividers |
| `--accent` | `#2f6fde` | `#6a9cf0` | links, primary, focus |
| `--accent-soft` | `#e6eefc` | `#1e2a40` | menu hover, active nav, radio on |
| `--on-accent` | `#ffffff` | `#0d1220` | text on accent |
| `--band-verified` | `#1f8a4c` | (same) | confidence bands |
| `--band-high` | `#3aa463` | (same) | |
| `--band-medium` | `#b7840f` | `#d9a63a` | |
| `--band-low` | `#d2691f` | (same) | |
| `--band-unknown` | `#8e8e93` | (same) | |
| `--conflict` | `#c62f2f` | (same) | errors, danger, key badge |
| `--diff` | `#fff3d6` | `#3a3220` | diff background (step screens) |
| `--keeper` | `#1f8a4c` | `#4cc27e` | kept copy |
| `--edit-bg` | `#f6ecfd` | `#33213f` | purple edit columns |
| `--edit-text` | `#8a2fb8` | `#d59cf5` | |
| `--same` | `#1f8a4c` | `#4cc27e` | Workbench green |
| `--differ` | `#c62f2f` | `#ff6b6b` | Workbench red |
| `--tint` | `rgb(47 111 222 / 6%)` | `rgb(140 170 255 / 8%)` | row/column hover overlay |
| `--tint-cell` | `rgb(47 111 222 / 12%)` | `rgb(140 170 255 / 16%)` | cell hover overlay |
| `--zebra` | `#f6f6f4` | `#262629` | shaded odd rows |
| `--group-bg` | `#f5f5dc` | `#2c2a20` | group rows, Grouping-by picker (beige) |
| `--sel` | `#ececee` | `#2c2c30` | ticked row |
| `--sel-active` | `#c9dbfa` | `#27406b` | previewed row |
| `--filter-c` | `#e07a10` | `#f0a050` | filtered column / chips (orange) |
| `--sort-c` | `#0a9fbd` | `#3cc8e0` | sorted column (cyan) |
| `--backdrop` | `rgb(0 0 0 / 32%)` | (same) | modal backdrop |
| `--shadow` | (2.1) | `none` | |
| `--shadow-pop` | (2.1) | `0 8px 24px rgb(0 0 0 / 48%)` | |
| `--job-bg` | `#14233f` | `#dbe6fb` | job stripe (inverted) |
| `--job-text` | `#f2f2f2` | `#1d1d1f` | |
| `--job-muted` | `#a9b6cf` | `#4a5670` | stage, count, Cancel border |
| `--job-bar` | `#6a9cf0` | `#2f6fde` | progress value |
| `--job-track` | `rgb(255 255 255 / 16%)` | `rgb(0 0 0 / 12%)` | progress track, Cancel hover |

### 2.3 Theme blocks

```css
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* dark set */ }   /* fallback if theme.js cannot run */
}
:root[data-theme="dark"] { /* the same dark set */ }
:root[data-theme="light"] { color-scheme: light; }
:root[data-theme="dark"]  { color-scheme: dark; }
```
**The two dark blocks must stay byte-for-byte identical in their declarations** (verified identical today).
`theme.js` always sets `data-theme` (light by default), so the media block only matters when the script fails.
`html { color-scheme: light dark }` in base.css.

## 3. Base styles (`base.css`)

- `*, ::before, ::after { box-sizing: border-box }`; `body`: `--font-ui`, md type, `--bg`, `--text`, margin 0 (**always an explicit background**).
- h1 xl / h2 lg (margin 24 0 8) / h3 md; `p` margin-bottom 16; links `--accent`, underline on hover only.
- `code, kbd, .mono` mono 12px. Tables collapse, cells padding 8, 1px `--line` bottom; `th` 600, muted, 12/16, nowrap.
- Form controls: one look: text/search/number/date inputs, `select`, `textarea`, `.selectbtn` → `min-height: --control-h; padding: 0 8px; --surface; 1px --line-strong; radius 4; appearance: none` (no native Safari/macOS look; search decorations removed). Hover border `--muted`; focus border `--accent` + `box-shadow: 0 0 0 1px --accent`, no outline. Placeholder `--muted`.
- Select arrow: two 4×4 `linear-gradient`s in `--muted`, positioned 16px from the right; `padding-right: 32px`.
- Checkbox/radio 16×16, `accent-color: --accent`. `label:has(> input[type=checkbox|radio])` = inline-flex gap 8.
- `:focus-visible { outline: 2px solid --accent; outline-offset: 2px }` globally.
- `@media (prefers-reduced-motion: reduce)` removes transitions and animations; `[x-cloak] { display:none !important }`.

## 4. Layout (`layout.css`)

`.app` grid `232px 1fr` (min-height 100vh); `.nav` sticky full-height `--surface` with right hairline;
`.nav__link` 32px rows, radius 4, hover `--surface-2`, `--active` `--accent-soft` + 600, `.nav__step` 24px circle
(active: accent/on-accent), `.nav__count` muted right, `.nav__sep` hairline. `.main` padding `--pad 32px`,
max-width 1504. `.page-head`, `.toolbar`, `.grid-cards`, `.split` (2 columns). `≤1024px`: one column, static nav.
`.app--nonav` hides the nav (Workbench default). `.main--flush` (Workbench): no padding, no max-width.

## 5. Component inventory (`components.css`)

| Component | Classes | Rules that define the look |
|---|---|---|
| Button | `.btn` (+ `.setbtn`, `.navtoggle`) | inline-flex, h `--control-h`, padding 0 16, 1px `--line-strong`, radius 4, `--surface`, 14 md, weight 500, nowrap; hover `--surface-2` |
| | `.btn--primary` | accent bg/border, `--on-accent`, 600; hover `brightness(1.08)` |
| | `.btn--danger` | text `--conflict` |
| | `.btn--small` | h 24, padding 0 8, 12/16 |
| | `.btn--icon` | square (width = height), padding 0, `--muted`; `.btn--small.btn--icon` 24 |
| | `.btn[disabled]` | opacity .5, not-allowed; `.htmx-request` primary opacity .64 |
| Link button | `.linkbtn` | no chrome, accent, 12/16, underline on hover |
| Settings / nav toggle | `.setbtn`, `.navtoggle` | 32px icon buttons, SVG 16px; hover or `aria-expanded=true` → accent text + border |
| Small icon button | `.iconbtn`, `.cellbtn`, `.wbh__act` | inline-grid, min-w/h `--control-h-xs`, padding 0 4, 1px `--line-strong`, radius 4, `--surface`, muted, 12px; hover/focus accent |
| Badges | `.badge` (pill outline in currentColor, `--verified/high/medium/low/unknown/conflict`), `.status--*`, `.keep-pill` | height 16, 12px, 600 |
| Chip | `.chip` | h 24, padding 0 8, `--surface-2`, pill, 12/16, muted; `--selected` accent-soft/accent; `--btn` mono + hover accent; `--exp` edit-bg/edit-text 600 ("beta") |
| Meta checks | `.check.check--ok/--no/--na` in `.checks` | outlined 16px badges, same/differ/muted-dashed; container query ≤208px shows counts only |
| Key badge | `.keybadge` | 16px red circle, white "!" bold 12 |
| Filter chip | `.fchip`, `.fchip__edit` | h 32, padding 0 4 0 8, **2px `--filter-c` border**, `--surface-2`, radius 4, 600 muted; × buttons 24px |
| Select button | `.selectbtn` | looks like a select (base.css), inline-flex, min-width 176 (128 under 1240px in `.wbbar`); `.is-active` accent border/text 600; `.is-sorting` 2px `--sort-c` border; `.is-grouping` `--group-bg` |
| Pick sheet | `.pick` (relative inline-flex gap 8) + `.pick__sheet` (absolute, top 100%+4, z 40, min-w 208, max-h 64vh, scroll); `.pick--right` aligns right; `--wide` 400px; `.pick__hint` 12/16 muted |
| Menu / sheet | `.wbsheet`, `.setsheet` (also legacy `.wbmenu`, `.colpick__menu`) | grid gap 2, padding 4, `--surface`, 1px `--line`, radius 8, `--shadow-pop`. `.wbsheet` fixed z 40 w 304; `.setsheet` absolute right z 45 w 256. Items: flex, min-h 32, padding 0 8, radius 4, no border, md, left-aligned; hover/focus `--accent-soft`; disabled muted. Items with a hint stack vertically. `[role=menuitemradio]` gets a 16px circle `::before` at left 8 (padding-left 32); checked → 4px accent border + 600 |
| Sheet labels | `.wbsheet__section`, `.setsheet__section`, `.colcat__title` | 12/16, 600, uppercase, `.04em`, padding 8 8 4; section colour `color-mix(--muted 64%, --surface)` (lighter than items) |
| Sheet title | `.wbsheet__title(--row)` | 600, bottom hairline, ellipsis; `.wbsheet__remove` muted linkbtn → red on hover |
| Edit actions | `.wbsheet__acts`, `.wbsheet__act(--primary/--danger)` | four equal 24px buttons, 1px `--line-strong`, primary accent |
| Segmented control | `.themeswitch` (`role=radiogroup`) | inline-flex gap 2, padding 2, 1px `--line-strong`, pill, `--surface`; buttons 24px pill, 12px muted; `[aria-checked=true]` accent-soft/accent 600; SVG 16 |
| Tabs | `.tabs`, `.tabs__tab(--active)` | 32px, 2px accent underline when active |
| Panels | `.panel`, `.card(__head/__body/__foot)`, `.stat(s)`, `.member` | `--surface`, hairline, radius 8, padding 16, `--shadow` |
| Search field | `.searchfield`, `__btn`, `__clear` | input with 32px padding both sides; magnifier absolute left (becomes a bordered button in tight header), × 24px right |
| Filters row | `.filters`, `.filters__label`, `.histbtns` | wrap, gap 8; labels muted 600 |
| Table | `.wbt` | fixed layout, separate borders, 12/16; cells `--cell-py 8`, hairline bottom, nowrap ellipsis; sticky thead `--surface-2`; `td` height `--row-h`; see the Workbench skill for state colours |
| Header cell | `.wbh` | flex, gap 4, h 40, `.wbh__label` 600 2-line clamp, `.wbh__sort`, `.wbh__filter` (orange funnel), `.wbh__pin`, `.wbh__abbr`, `.wbh__resize` (8px, col-resize) |
| Source link | `.srclink` (+ `--head`, `--origin`), `.srclink__icon` | inline-flex, h `--control-h-xs`, min-w 32, padding 0 4, no border, radius 4, bg `color-mix(--edit-text 16%, --edit-bg)` (28% on hover), text `--edit-text`, 12/16 600; icon 16 |
| Tooltip | `.wbtip` | fixed z 70, padding 4 8, radius 4, inverted (`--text` bg, `--bg` text), 12/16, nowrap, no pointer events |
| Drop line | `.dropline` | fixed z 45, 4px accent bar, 16px round ends (`::before/::after`) with 2px `--bg` ring, glow `0 0 8px --accent` |
| Modal | `.modal`, `__backdrop`, `__box(--wide)`, `__actions`, `__close` | fixed z 50; box 440px (min 92vw) / wide 1040 (94vw), max-h 88vh, padding 24, gap 16, radius 8, `--shadow-modal`; h3 lg; labels stacked 12/16 (radios/checkboxes inline md); actions right-aligned gap 8 |
| Column picker | `.colpick-head(__tools)`, `.colcat`, `.colcat__group`, `.colcat__item` | auto-fill 224px columns, items 32px rows, hover `--surface-2` |
| Value list | `.valuelist`, `.valuelist__item` | max-h 56vh, items 32px, hover accent-soft |
| Lightbox | `.lightbox`, `__backdrop`, `__box`, `__head`, `__one`, `__grid--1..4`, `__foot`, `__modes`, `__zoom`, `__pct`, `.zoomview` | fixed z 55; box `100vw/100vh − 32px`, padding 16, radius 8; media area `--surface-2`; current figure 2px accent; zoomview grab/grabbing |
| Preview | `.results-panel`, `.preview`, `.preview__zoom`, `__zoomhint`, `.pv-head`, `.pv-section(--edit)`, `.pv-title`, `.facts(--mono)`, `.pv-list` | panel `--surface`, hairline, radius 8 (0 when flush), padding 16; zoom hint pill `color-mix(--text 80%, transparent)` on hover |
| Naming dialog | `.namer`, `__body` (240px sidebar + main), `__side`, `__preset(.is-active)`, `__edit`, `__text`, `__tok`, `__list`, `__opt`, `__warn` | chips purple underlined (`--edit-text`, offset 4), selected chip 2px `--edit-text` outline, active preset accent-soft + 2px inset accent; stacks under 760px |
| Data input | `.datain`, `__field`, `__sheet` (`max(100%, 560px)`), `__src`, `__addbtn`, `__removeall`, `__writable`, `__demo` | uppercase bold add (accent) / remove-all (conflict) |
| Job stripe | `.job`, `__stage`, `__count`, `__cancel(.is-busy)`, `.job--failed` | fixed top z 60, h 40, padding 0 16, gap 16, `--job-*` tokens, progress 8px pill `flex:1`; body gets `--job-h: 40px` padding via `body:has(#job > .job:not(.job--failed))`; failed = static bordered `--conflict` banner |
| Toast | `.toast` | fixed bottom/right 24, z 60, inverted (`--text`/`--bg`), padding 8 16, radius 8, `--shadow-pop` |
| Empty | `.empty` | padding 32, centred muted, 1px dashed `--line-strong`, radius 8 |
| Misc | `.muted`, `.small`, `.nowrap`, `.path`, `.chev(--open)` (rotate 90°, .12s) | |

Step screens (results, harmonize grid, compare, recheck) reuse the same tokens: `.results`, `.rgroup__head`,
`.rrow(--selected)`, `.selbar`, `.grid`, `.fv`, `.field--accepted/edited/rejected` (4px inset bars).

### 5.1 Z-index ladder

table internals 1–3 · `.wbbar` 35 · `.wbsheet`, `.pick__sheet` 40 · `.setsheet`, `.dropline` 45 · `.modal` 50 ·
`.lightbox` 55 · `.job`, `.toast` 60 · `.wbtip` 70.

### 5.2 Appearance preference rules (end of components.css)

```css
:where(:root[data-zebra="on"]) :where(tbody tr:nth-child(odd):not(.wbg__head, .wbr)) > td,
:where(:root[data-zebra="on"]) :where(.wbt tbody tr:nth-child(odd of .wbr)) > td { background-color: var(--zebra); }
:root[data-borders="on"] :is(.wbt, .grid, .results) tbody td + td { border-left: 1px solid var(--line); }
:root[data-borders="off"] :is(td, th) { border-bottom-color: transparent; }   /* + .grid right, .wbg__head top */
```
`:where()` keeps zebra at zero specificity so selection, hover and diff backgrounds win.

## 6. Preferences: `theme.js` and the settings sheet

`theme.js` is loaded **synchronously in `<head>` before the stylesheets**, so saved choices apply before first paint.

| Pref | Attribute on `<html>` | Values (default first) | localStorage key |
|---|---|---|---|
| theme | `data-theme` | `light` \| `dark` (any other stored value, e.g. old `auto`, reads as light) | `meta-duplo-theme` |
| borders | `data-borders` | `on` \| `off` | `meta-duplo-borders` |
| zebra | `data-zebra` | `off` \| `on` | `meta-duplo-zebra` |
| density | `data-density` | `normal` \| `compact` | `meta-duplo-density` |

API: `window.mdPrefs.get(name)`, `mdPrefs.set(name, value)` (sets the attribute; stores the value, or removes the key
when it equals the default). All storage access is in try/catch (blocked storage → defaults).

Settings sheet (`base.html`, block `topbar`): `.topbar` with
`x-data="{ open, p: {theme, borders, zebra, density from mdPrefs}, set(k, v) }"`, closes on Esc and click outside.
`button.setbtn` (gear SVG, `aria-haspopup`, `:aria-expanded`, `aria-label="Settings"`) toggles `.setsheet`
(`x-show x-cloak x-transition.opacity`). Order inside the sheet:
1. `{% block settings_menu %}` — **the page's own section first** (Workbench: "Filomena" → Preview panel, Columns…, Reset view);
2. `.setsheet__section` "Appearance";
3. `.themeswitch[role=radiogroup][aria-label="Colour theme"]`: Light (sun SVG) / Dark (moon SVG) radios;
4. `{% block appearance_menu %}` (Workbench: "Text colours (same / different)");
5. checkboxes `label.setsheet__item.setsheet__check`: Cell borders, Shade odd rows, Compact view;
6. `a.setsheet__item` "All settings…" → `/settings` (top hairline).

Pages either render it at the top right (`topbar_place` → `.topbar` flex-end) or put `{{ self.topbar() }}` in their
own header row (the Workbench). The block is declared inside `{% if false %}` so it is defined once and rendered by
reference.

## 7. Icons

Inline SVG only: `viewBox="0 0 16 16"` (or 20), `fill="none" stroke="currentColor" stroke-width="1.5"`,
round caps/joins, `aria-hidden="true"`, sized by CSS to `--icon` (16px) — never by inline style. Colour comes from
`currentColor`, so hover/active states recolour icons. Shared SVGs live in partials (e.g.
`partials/link_icon.svg`, class `srclink__icon`) and are `{% include %}`d. Text glyphs are used where they read
well: ⋯ › └ ★ ✓ × + − ↑ ↓ ✎ ⤢ ⚙. No icon fonts, no image sprites.

## 8. Rules for adding UI

1. **Tokens only.** Colours, lengths, radii, type and shadows come from `tokens.css`; a new value becomes a token (add light and dark values, and mirror it in **both** dark blocks) or is a step of the scale / a multiple of 8. `color-mix()` of tokens is fine.
2. **No inline styles** except user data and JS-computed layout: column/table widths, menu `left/top`, pinned `left`, `--panel-w`, tooltip/drop-line position, `.wb-scroll` max-height, lightbox transform, naming text width, compare swipe clip.
3. **No new JS frameworks**, no build step: Jinja2 templates, htmx 2 attributes, Alpine 3 (`Alpine.data`, `Alpine.store`), plain CSS. Register Alpine components on `alpine:init` in a deferred script loaded before Alpine.
4. **Reuse components**: `.btn` variants, `.selectbtn` + `.pick__sheet`, `.wbsheet` items, `.themeswitch`, `.chip`, `.modal`. One look per kind of control.
5. **Compact density** must work: use `--control-h*`, `--row-h`, `--cell-py`, `--gap`, `--pad` rather than fixed heights.
6. **Both themes**: check light and dark; tints and backgrounds as overlays (`background-image: linear-gradient(var(--tint), var(--tint))`) so they combine with row colours.
7. **Safari**: `-webkit-appearance: none` on fields, `-webkit-line-clamp` with `display:-webkit-box`, `::-webkit-progress-*` and `::-moz-progress-bar`, plain objects for reactive sets, try/catch around storage, relative asset URLs (`/static/...`), no APIs Safari lacks.
8. **Accessibility**: real `<button type="button">` for actions; `aria-label` + `title` on icon-only buttons; menus `role=menu` with `menuitem`/`menuitemradio` + `aria-checked`; pickers `aria-haspopup`, `aria-expanded`, `aria-labelledby`; segmented controls `radiogroup`/`radio`; dialogs closable with Esc; `role=status` for progress/toasts, `role=alert` for failures; visually-hidden labels via `clip-path: inset(50%)` (not `display:none`); never remove `:focus-visible` outlines except where a container shows focus (`.namer__edit:focus-within`).
9. **Motion**: short opacity/transform transitions only (≤ .15s); reduced motion disables them.

## Rebuild checklist

1. Create `tokens.css` with the exact tokens of 2.1–2.2, the compact block and the two identical dark blocks plus `color-scheme` rules.
2. Create `base.css` (reset, type, unified controls, select arrow gradients, checkbox/radio, focus-visible, reduced motion, x-cloak).
3. Create `layout.css` (app grid, nav, main, `--nonav`, 1024px breakpoint).
4. Create `components.css` section by section in the inventory order, then the preference rules last.
5. Add `theme.js` synchronously in `<head>` before the stylesheets; expose `window.mdPrefs`.
6. Build the settings sheet in `base.html` with the page block first, then Appearance, and wire it to `mdPrefs`.
7. Add the gear, sun, moon and link SVGs inline with `currentColor`.
8. Audit: grep the CSS for raw px values not on the scale or not multiples of 8 (allow 1px), raw hex colours outside `tokens.css`, and `style=` in templates outside the allowed cases.

## Verify

Manual:
- Toggle Dark in ⚙: every surface switches with no light patches; reload keeps Dark with no flash of light.
- Compact view: controls become 24px, rows 24px, body text 12px; turning it off restores 32px.
- Shade odd rows on: alternate Workbench rows get `--zebra`; selected/previewed rows still show their colour.
- Cell borders off: table hairlines disappear.
- Tab through the header: every control shows a 2px accent focus ring.
- Block site storage (private window): the app renders in light, borders on, normal density, no errors.

Playwright:
```js
await page.goto(url);
const attr = (n) => page.evaluate((n) => document.documentElement.dataset[n], n);
expect(await attr('theme')).toBe('light');
expect(await attr('density')).toBe('normal');
await page.locator('button.setbtn').click();
await page.getByRole('radio', { name: 'Dark' }).click();
expect(await attr('theme')).toBe('dark');
expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(22, 22, 23)'); // #161617
await page.reload();
expect(await attr('theme')).toBe('dark');                     // applied before paint by theme.js
await page.locator('button.setbtn').click();
await page.getByLabel('Compact view').check();
expect(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--control-h').trim())).toBe('24px');
expect(await page.evaluate(() => localStorage.getItem('meta-duplo-density'))).toBe('compact');
await page.getByLabel('Compact view').uncheck();
expect(await page.evaluate(() => localStorage.getItem('meta-duplo-density'))).toBeNull();   // default removed
// sheet order: page section before Appearance
const sections = await page.locator('.setsheet .setsheet__section').allTextContents();
expect(sections.map(s => s.trim().toLowerCase())).toEqual(['filomena', 'appearance']);
// dark blocks identical (run in Node on tokens.css)
// const css = fs.readFileSync('meta_duplo/web/static/css/tokens.css', 'utf8');
// const a = css.match(/:root:not\(\[data-theme="light"\]\) \{([^}]*)\}/)[1], b = css.match(/:root\[data-theme="dark"\] \{([^}]*)\}/)[1];
// expect(a.replace(/\s+/g, ' ').trim()).toBe(b.replace(/\s+/g, ' ').trim());
```
