# ALE run board, visual design v2

Scope: how the board and the Mod look. What they say (sections, state table, reason order, wording, health rules,
motion triggers) stays as written in `.ale/briefs/BOARD-DESIGN.md` (called "the brief" below, cited as `brief §N`).
This file replaces the brief only where the table "Deviations" (section 12) says so.

Mockups in this folder (all show the real run: 16 tasks, 14 done, T20 needs your answer, T21 waiting on it, 1.2M tokens):

| File | Source | Viewport |
|---|---|---|
| `board-v2-desktop-light.png` | self-rendered from `board-v2-mock.html` (headless Chromium) | 1440, light |
| `board-v2-desktop-dark.png` | self-rendered | 1440, dark |
| `board-v2-desktop-light-drawer.png` | self-rendered, T20 selected, drawer docked | 1440, light |
| `board-v2-tablet-light-drawer.png` | self-rendered, drawer as overlay sheet | 1024, light |
| `board-v2-mobile-light.png` | self-rendered | 390 @2x, light |
| `board-v2-mobile-dark.png` | self-rendered | 390 @2x, dark |
| `board-v2-mobile-dark-sheet.png` | self-rendered, bottom sheet open | 390 @2x, dark |
| `stitch-desktop-light.png` | Google Stitch export (project thumbnail, 512x410) | desktop, light |

Stitch: project "ALE run board", id `13771253832386113832`, design system asset `8257d2267c0c4fbf93dab2db158fc716`
(created from `stitch-DESIGN.md`, the token contract uploaded to Stitch). Two desktop generations and one mobile dark
generation ran. The first and third timed out. The second said it finished. `list_screens` and `get_project` never listed
a generated screen, only the uploaded DESIGN.md screen `11798766729527050600`, so `edit_screens` and `generate_variants`
had no screen id to act on. The one Stitch image I could export is the project thumbnail of the desktop light screen.
Following the book's rule that Stitch output is a visual spec, not token truth (CandleKeep `cmn65gqw504bllc0znf83oq6k` ch 5.3,
5.5, 15.1), the self-rendered PNGs are the reference. They use the exact tokens below, system fonts and the real SVG sprite.
Stitch changed the fonts: it switched the system stack to Inter, a web font we cannot ship. Its layout matches ours:
a verdict sentence first, a cell ribbon, a full-width attention strip, and rows with a coloured edge.

## 1. Aesthetic direction

A flight-strip bay. In an air traffic control room each aircraft is a paper strip in a rack: a coloured edge says what kind
of attention it needs, and the controller reads the strips top to bottom by urgency. A run of agent tasks has the same
shape: many parallel items, a few that need a human, and one question ("is anything stuck on me?") that the design must
answer before any detail. So every task is a strip: a 4 px state edge, then icon and state word in a fixed column, then id,
then title with the reason sentence under it, then metrics on the right. The one bold element is the header: the verdict
sentence set at 24 px as the page headline, with a task ribbon (one cell per task) under it. Everything else is quiet:
one surface, hairline dividers, no shadows on rows, colour only on edges, icons and state words. Waiting and Canceled edges
are dashed, so "not moving" reads as a different texture from "done" even in greyscale.

## 2. Colour tokens

Semantic hues are the brief's §5 values, unchanged. Neutrals move off Tailwind slate to a slightly cooler graphite,
which also fixes one brief pair that fails AA: light `--text-3 #64748B` on `--needs-tint #FEF3C7` is 4.3:1. With the
neutrals below, every pair passes. Ratios were computed with the WCAG 2 formula by `contrast.py` in this folder (prints `bad 0`).

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#F1F3F5` | `#11161C` | page |
| `--surface` | `#FFFFFF` | `#19202A` | strip rack, drawer, chips |
| `--raised` | `#F7F8FA` | `#1E2632` | row hover, count badge, code block |
| `--text` | `#17202B` (14.77 bg / 16.43 surface) | `#EDF1F5` (16.01 / 14.44) | titles, reason in cards |
| `--text-2` | `#44505E` (7.39 / 8.22) | `#C3CCD6` (11.19 / 10.09) | reasons, meta, labels |
| `--text-3` | `#5E6977` (5.02 / 5.58) | `#93A0AE` (6.82 / 6.15) | placeholders, empty lines only |
| `--border` | `#DCE1E6` | `#2B3542` | decorative dividers |
| `--border-strong` | `#5E6977` | `#93A0AE` | focus ring, outlined chips, inputs (3:1+) |
| `--selected` | `#E6EEFB` (text 14.08) | `#1D2F4F` (text 11.77) | selected strip |
| `--focus` | `#FDE68A` (with `#17202B`, 13.2) | `#FDE68A` (with `#11161C`, 14.6) | focus fill on buttons |
| `--scrim` | `rgba(23,32,43,.32)` | `rgba(0,0,0,.55)` | behind overlay drawer |

| State token / tint | Light fg on bg / surface / own tint | Dark fg | Dark tint | Dark ratios bg / surface / tint |
|---|---|---|---|---|
| needs `#B45309` / `#FEF3C7` | 4.51 / 5.02 / 4.51 | `#FBBF24` | `#3A2A0A` | 10.89 / 9.82 / 8.30 |
| fail `#B91C1C` / `#FEE2E2` | 5.82 / 6.47 / 5.30 | `#F87171` | `#3F1414` | 6.57 / 5.92 / 5.74 |
| run `#1D4ED8` / `#DBEAFE` | 6.02 / 6.70 / 5.49 | `#60A5FA` | `#152542` | 7.15 / 6.45 / 6.01 |
| verify `#6D28D9` / `#EDE9FE` | 6.39 / 7.10 / 5.98 | `#A78BFA` | `#2A1C4F` | 6.68 / 6.02 / 5.60 |
| ready `#0F766E` / `#CCFBF1` | 4.92 / 5.47 / 4.86 | `#2DD4BF` | `#0B2E2B` | 9.76 / 8.80 / 7.83 |
| wait `#44505E` / `#E9EDF1` | 7.39 / 8.22 / 6.99 | `#C3CCD6` | `#222B36` | 11.19 / 10.09 / 8.81 |
| done `#15803D` / `#DCFCE7` | 4.51 / 5.02 / 4.57 | `#4ADE80` | `#0F2E1C` | 10.43 / 9.41 / 8.44 |
| cancel `#5E6977` / `#E9EDF1` | 5.02 / 5.58 / 4.74 | `#93A0AE` | `#222B36` | 6.82 / 6.15 / 5.37 |

On every tint, `--text` and `--text-2` stay at 6.7:1 or higher in both themes. Light needs and done on `--bg` sit at
4.51:1, just over the line, so state words sit on `--surface` (5.02) and the pill sits on its own tint.
Tokens live on `:root`, the dark set is repeated under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`
and `:root[data-theme="dark"]`, and each theme sets `color-scheme` so native selects and scrollbars follow it.

## 3. Type scale

System stacks only: sans `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`;
mono `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace` with `font-variant-numeric: tabular-nums`.
`html { font-size: 100% }`, sizes in rem. Weights 400 and 600 only. Sentence case everywhere; no all-caps labels.

| Role | Size / line height | Weight | Face |
|---|---|---|---|
| Verdict headline | 1.5rem / 1.25 (1.25rem under 768 px), `letter-spacing: -.01em`, `text-wrap: balance` | 600 | sans |
| Attention reason, drawer reason | 1rem / 1.45 | 600 card, 400 drawer | sans |
| Section header | .9375rem / 1.3 | 600 | sans |
| Row title, body | .875rem / 1.45 | 400 | sans |
| State word, reason line, chips | .8125rem / 1.35 | 600 state, 400 reason | sans |
| Run id, task id | .8125rem | 400 run id, 600 task id | mono |
| Meta, counts, totals, timeline times | .75rem | 400 (600 in count badge) | mono |

## 4. Spacing, radius, elevation

Spacing scale 4 / 8 / 12 / 16 / 20 / 24 / 32 / 48 px. Page gutter 32 px (16 px under 900 px). Section gap 28 px, header to
content 8 px. Strip: min height 48 px, padding 8 px 16px 8 px 20 px (4 px edge plus 16 px), column gap 16 px (10 px under
768). Radius: rack and cards 6 px, chips and pills 999 px, bottom sheet 12 px top corners, ribbon cells 2 px. Elevation:
no shadows on rows or cards; the only shadows are the overlay drawer and the bottom sheet (`0 -8px 32px rgba(0,0,0,.25)`).

## 5. Layout grid and breakpoints

Content column `max-width: 1120px`, centred in the list area. Measured widest state cell at 13 px / 600 in the
reference render (icon 16 + gap 6 + word): "Needs your answer" 142.9 px, "Out of attempts" 121.9, "Failed to start" 109.3,
"Waiting on fix" 107.9, "No heartbeat" 104.9, "Superseded" 98.2. The state column is `10.5rem` (168 px): 25 px spare,
Measured under forced fallbacks: Arial 141.2, Helvetica Neue 141.4, Tahoma 147.1, Verdana 162.6 (the widest common
system face), all under 168. The old chip "Superseded (parent accepted)" measures 215.8 px and
must not return to the chip (section 12).

| Width | Strip grid | Attention | Drawer |
|---|---|---|---|
| 1280 and up | `grid-template-columns: 10.5rem 6.5rem minmax(0,1fr) auto` areas `state id main meta` | cards `repeat(auto-fit, minmax(min(100%,30rem),1fr))`, action column on the right | docked 440 px column, only while a task is selected; list reflows; no empty panel ever |
| 900 to 1279 | same as above | same | overlay sheet from the right, `width: min(440px, 92vw)`, scrim behind |
| 768 to 899 | two rows: `state id meta` / `main main main`; meta goes inline | card collapses to one column, "Blocks N" and button share the last row | overlay sheet |
| 390 to 767 | same two rows, state column `auto`, gap 10 px | one column | bottom sheet, `top: 48px`, drag handle, "Back to tasks" replaces the close button |

Rules that stop overlap (the defects in `board2-light.png`): every grid track that holds text is `minmax(0, ...)`, every
text cell has `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap` and a `title` with the full
text; the state word never shares a track with the id; `auto` tracks hold only `white-space: nowrap` meta. Titles in the
card and the drawer wrap (`overflow-wrap: anywhere`) instead of truncating (brief §8). Header rows are `flex-wrap: wrap`
with `min-width: 0` on the run id, which truncates at the end with an ellipsis and a `title`.

## 6. Components

**Header (sticky).** Row 1: run id (mono, `--text-2`, ellipsis) and at the far right the connection state: 8 px dot plus
"Live" plus `HH:MM:SS` in mono; disconnected shows `i-pause` plus "Reconnecting" in `--cancel`. Row 2: health pill (28 px,
own tint, 1 px inset ring in the state colour, icon plus text, table in brief §3) then the headline sentence. Row 3: the
task ribbon, the legend and the totals. Ribbon: one cell per task in section order Done, Running, Needs you, Waiting,
`flex: 1 1 0`, 2 px gap, 14 px tall, max width 520 px; Waiting and Ready cells are outlined, not filled. Above 64 tasks the
cells merge into four proportional segments (brief §9 progress transition). `role="img"` with the aria label
"14 of 16 done, 1 needs you, 1 waiting". The legend repeats the counts as icon plus words, so the ribbon never carries
meaning by colour alone. Totals (mono, 12 px): "1.2M tokens, run total" (exact value in `title`), "cost not reported".

**Attention card ("Needs you").** Section hidden when empty. Tint background, 4 px edge in the state colour, 6 px radius,
padding 14 16 14 20. Row 1: state chip (surface fill, coloured ring) plus id (mono 600). Row 2: title. Row 3: the brief §6
reason sentence at 16 px / 600. Row 4, optional: when the reason contains acceptance results (`/A\d+ FAIL exit=\d+/`), render
each as a mono outlined chip ("A1 FAIL exit=1"). Right column (bottom row under 900 px): "Blocks N tasks" and a primary
button "Open task" (`--text` fill, `--surface` text). At most 4 cards, then a quiet button "+N more" that expands inline (brief §2 wording).

**Section header.** `h2` at 15 px / 600 plus a count badge (mono 12 px, `--raised` fill, 1 px `--border` ring, pill).
Waiting groups: 13 px `--text-2` line with `i-link`, text "Blocked by T20 (Needs your answer) · 1 task" (brief §6 wording),
ellipsis at narrow widths. Groups with a disclosure use `i-chev` rotated -90deg when collapsed.

**Task strip.** Grid in section 5. Edge: `::before`, 4 px, solid in the state colour; dashed (3 px on, 3 px off) for wait
and cancel tokens. State cell: 16 px icon plus word in the state colour, 600. Id: mono 600 `--text`. Main: title
(`--text`) over the reason (13 px `--text-2`); Done rows show no reason line (brief §4: outcome goes to meta). Meta: mono
12 px `--text-2`, right-aligned, two stacked lines ("939k tok", "Done 6h 30m ago"); inline under 900 px. Hover: `--raised`.
Selected: `--selected` plus `aria-current="true"`. The whole strip is one `button` (or `role="button"`, `tabindex=0`).
Strips sit in one rack per section: `--surface`, 1 px `--border` ring, dividers between strips, 6 px radius.

**Show all control.** Under the Done rack: quiet button, `--text-2`, "Show all 14 done" / "Show fewer done", 32 px tall,
aligned to the strip text (left padding 12 px).

**Filters.** One wrapping row: three native `<select>` (ids `role-filter`, `state-filter`, `agent-filter`) styled as
32 px fields "Role All" with `i-chev`, a search input (`flex: 1 1 14rem`, `i-search`, placeholder
"Search task id or title"), the visible count "16 of 16" (mono), and "Clear filters" shown only while a filter is set.
Under 768 px the three selects share one row (`flex: 1 1 calc(33% - 8px)`), the search takes the next row.
Hidden on an empty run.

**Drawer.** Never a permanent panel. Order and content from brief §8. Top line: state chip, id, close button (`i-close`,
`aria-label="Close detail"`); on the bottom sheet a "Back to tasks" button with `i-back` replaces it. The reason sits in a
tinted block at 16 px (the primary element). Section labels are 13 px / 600 `--text-2` ("Acceptance", "Dependencies",
"Facts", "Labels", "Timeline"). Facts: two-column `dl`, labels `7.5rem`. Acceptance: mono block on `--raised`, grid
`auto auto minmax(0,1fr)` so ids and FAIL never wrap and commands wrap on their own column. Labels: outlined neutral chips.
Timeline: two columns, time in mono. Focus moves into the drawer on open and back to the strip on close; Esc closes.
Overlay and sheet trap focus and set `aria-modal="true"`; the docked column does not.

**Empty states.** Running with none: dashed-border box, `--text-3`, "Nothing is running." Needs you empty: section not
rendered. Filters hide everything: in the list area, "No tasks match these filters." plus a "Clear filters" button. Run
with zero tasks: header verdict only, then "This run has no tasks yet. They appear here as soon as the plan is baked."
Disconnected: header rule 1 of brief §3; lists stay visible, dimmed to `opacity: .7`, never blanked.

## 7. Icon set

One inline sprite, `viewBox="0 0 16 16"`, rendered at 16 px, `fill: none; stroke: currentColor; stroke-width: 1.5;
stroke-linecap: round; stroke-linejoin: round`. Paths below are the exact `d` values in `board-v2-mock.html`.
Most state icons share a 6.25 radius ring; the mark inside tells them apart, so shape carries state in greyscale.

| State (brief §4 label) | id | Drawing |
|---|---|---|
| Needs your answer | `i-question` | ring r 6.25 plus question mark `M6.1 6.2a1.95 1.95 0 1 1 2.7 1.8c-.5.25-.8.6-.8 1.15v.35` and dot `M8 11.4v.05` |
| Failed to start, Rejected, Failed | `i-x` | ring plus cross `m5.9 5.9 4.2 4.2m0-4.2-4.2 4.2` |
| Out of attempts | `i-stop` | rounded square 10.5 (rx 1.5) plus filled 4x4 inner square |
| No heartbeat | `i-pause` | pulse that dies into dashes: `M1.75 8h2.5l1.25-2.75L7.25 10.5 8.25 8` and `M10.5 8h.75m2 0h1` |
| Running | `i-dot` | ring at 35% opacity plus filled dot r 3 (the breathing element) |
| Verifying | `i-check-pending` | dashed ring (`stroke-dasharray: 2.2 2.2`) plus check `m5.4 8.1 1.8 1.8 3.4-3.7` |
| Waiting on fix | `i-wrench` | wrench `M10.6 2.4a3.1 3.1 0 0 0-3.7 4L2.6 10.7a1.35 1.35 0 0 0 1.9 1.9l4.3-4.3a3.1 3.1 0 0 0 4-3.7l-1.8 1.8-1.6-.3-.3-1.6z` |
| Ready | `i-circle` | smaller ring r 5, nothing inside |
| Retrying | `i-retry` | open arc `M13.25 8a5.25 5.25 0 1 1-1.54-3.71` plus corner arrow `M13.25 2.5v2.75H10.5` |
| Waiting | `i-clock` | ring plus hands `M8 4.75V8l2.25 1.5` |
| Done | `i-check` | ring plus check `m5.25 8.2 1.9 1.9 3.6-4` |
| Superseded (new, section 12) | `i-superseded` | ring plus arrow `M5 8h5.5M8.5 5.75 10.75 8 8.5 10.25` |
| Canceled | `i-slash` | ring plus slash `m3.6 12.4 8.8-8.8` |
| Unknown | `i-diamond` | diamond `M8 1.75 14.25 8 8 14.25 1.75 8z` |

UI icons: `i-link` (group header), `i-search`, `i-close`, `i-back`, `i-chev`. Implementation gotcha: the brief's `<svg hidden>`
sprite still takes 300x150 px in Chromium (the `hidden` attribute does not hide SVG elements); add
`svg[hidden]{display:none}`. `<use>` still resolves symbols inside it. Rows clone a `<template>` and call
`use.setAttribute("href", "#i-...")` (brief §4).

## 8. Motion

Triggers, durations and reduced-motion fallbacks are brief §9, unchanged. Additions for the new drawer forms, same easing
(`--ease-out: cubic-bezier(.25,1,.5,1)`, `--ease-in: cubic-bezier(.5,0,.75,0)`), `transform` and `opacity` only:

| Element | Enter | Exit | Reduced motion |
|---|---|---|---|
| Docked drawer (1280 and up) | list column change is instant; drawer content `opacity 0 to 1` 160 ms | instant | instant |
| Overlay sheet (768 to 1279) | `translateX(24px to 0)` plus opacity, 220 ms ease-out; scrim opacity 200 ms | `translateX(0 to 8px)` 140 ms ease-in | instant |
| Bottom sheet (under 768) | `translateY(24px to 0)` plus opacity, 220 ms ease-out | `translateY(0 to 8px)` 140 ms ease-in | instant |
| Ribbon cell changes state | background-color 250 ms | | instant |

The one orchestrated moment is the health pill change (brief §9 ring pulse). Nothing animates on first load.

## 9. Terminal variant (Mod)

Header is 3 rows (brief §10): run id plus health, headline, ribbon plus totals. The board URL moves to the last line.
Fixed columns, one line per task, so glyph, id and state word never move: `{glyph} {id padded 8} {state padded 17} `
(29 cells), then title and reason. At 80 columns the title is cut to 16 cells and the reason gets the rest; at 120 the
title gets 32, the reason the rest minus a right-aligned meta column (tokens). Truncation uses `…` and never touches the
first 29 cells. Needs you rows are never cut: line 1 is glyph, id, state and full title; the reason wraps onto up to two
lines indented 11 cells; a last indented line holds "Blocks N tasks". The header ribbon is one glyph per task in section
order (`✓` done, `●` running, `?` or the Needs-you glyph, `·` waiting); above 40 tasks it becomes counts only. Colour
by named ANSI colour from brief §10, applied to glyph plus state word only; bold on Needs you ids. Both mockups were
generated by a script and checked: the longest line is 80 and 120 cells, counting the 1-cell glyphs `✓ ? · ■ …`.

80 columns:

```
ALE 2026-09-22-work-entry-point-orchestration  ■ Stalled: needs you
Nothing is running. 1 needs you; 1 is waiting behind them.
✓✓✓✓✓✓✓✓✓✓✓✓✓✓?·  14 of 16 done   1.2M tok, run total   cost not reported
Needs you 1
? T20      Needs your answer Cleanup (w-sonnet, human-gated deletions)
           Needs your answer: "Acceptance failed: A1 FAIL exit=1 A2 FAIL exit=1"
           Blocks 1 task
Running 0
  Nothing is running.
Waiting 1
  Blocked by T20 (Needs your answer), 1 task
· T21      Waiting           Version 0.4.0, … Waiting on T20 (blocked by T20, n…
Done 14
✓ T14      Done              Explore brainst… Done 1h 58m ago
✓ T13      Done              Intake acceptan… Done 2h 20m ago
✓ T12      Done              Observe ledger … Done 2h 51m ago
  +11 more done
board: http://127.0.0.1:43127/<token>/  (copy into a browser)
```

120 columns:

```
ALE 2026-09-22-work-entry-point-orchestration  ■ Stalled: needs you
Nothing is running. 1 needs you; 1 is waiting behind them.
✓✓✓✓✓✓✓✓✓✓✓✓✓✓?·  14 of 16 done   1.2M tok, run total   cost not reported
Needs you 1
? T20      Needs your answer Cleanup (w-sonnet, human-gated deletions)
           Needs your answer: "Acceptance failed: A1 FAIL exit=1 A2 FAIL exit=1"
           Blocks 1 task   96k tok
Running 0
  Nothing is running.
Waiting 1
  Blocked by T20 (Needs your answer), 1 task
· T21      Waiting           Version 0.4.0, CHANGELOG entry,… Waiting on T20 (blocked by T20, needs you)
Done 14
✓ T14      Done              Explore brainstorm hook (w-sonn… Done 1h 58m ago                                   8.1k tok
✓ T13      Done              Intake acceptance criteria (w-s… Done 2h 20m ago                                   9.8k tok
✓ T12      Done              Observe ledger rows (w-sonnet)   Done 2h 51m ago                                    11k tok
  +11 more done
board: http://127.0.0.1:43127/<token>/  (copy into a browser)
```

Colour in the mockups: `?`, T20 and "Needs your answer" in `yellow` (id bold); `■ Stalled: needs you` in `red` bold;
`·` and "Waiting" `dimColor`; `✓` and "Done" in `green`. Titles and ids of Done rows: default colour. Task titles and ids for
T12 to T14 and all token and time values here are illustrative; the counts, T20, T21 and their text are the real run.

## 10. Wording decisions that affect layout

- Headline plurals: "1 needs you; 1 is waiting behind them" and "2 need you; 13 are waiting". The live board printed
  "1 need you; 1 are waiting" because the brief §3 template hard-codes the verb. Use the brief §7 two-form map for
  "need/needs" and "is/are".
- The card and the drawer print the brief §6 reason unchanged: `Needs your answer: "{waiting_on}"`. The A1/A2 chips are
  parsed from that text and are extra, never a replacement.

## 11. What changed from the current board (fault to fix)

| Fault in `board2-*.png`, `board-phone.png` (in `agentic-label-engineering/.playwright-mcp/`) | Fix |
|---|---|
| State label and id overlap; "Superseded (parent accepted)" runs over its column | fixed `10.5rem` state track, `minmax(0,...)` tracks, ellipsis plus `title`, chip word "Superseded" |
| Icons look missing | measured in `ale/board.html` (r1): every `use` resolves, but the symbols are drawn at 4 to 8 px inside the 16 px box (for example `i-clock` is r 4, `i-pause` two 8 px strokes), so they read as specks next to 13 px text. Fix: the section 7 paths, which fill the 1.75 to 14.25 box; checklist item 3 checks size, not presence |
| Empty drawer panel beside the list | drawer exists only while a task is selected: docked, overlay or bottom sheet by width |
| Bland layout, weak hierarchy | verdict at 24 px as the headline, ribbon, tinted attention strip, strip edges |
| One attention card in a 4-column grid leaves 3/4 empty | `auto-fit` grid, a single card spans the full width |
| Phone: totals wrap mid-phrase ("1.1M tokens, run / total") | each total is `white-space: nowrap` and wraps as a whole item |

## 12. Deviations from BOARD-DESIGN.md (the brief owner decides)

| Brief | This design | Why |
|---|---|---|
| §2: list plus a permanent 420 px drawer at 1024 px and wider | drawer only while selected; docked 440 px at 1280 px and wider, overlay 768 to 1279, bottom sheet under 768 | the empty panel was a named defect; 1024 leaves the list too narrow for five tracks |
| §4 has no Superseded row; `board.html` shows "Superseded (parent accepted)" | add row: rejected fix whose parent is accepted, label "Superseded", `i-superseded` / `→`, token cancel, section Done, reason "Superseded: parent {id} accepted" | wording gap; the long label caused the overlap |
| §4 "Unknown: {raw}" has no length limit; §10 says the state word is never truncated | "Unknown:" is never cut; only `{raw}` truncates, with the full value in `title` (web) and in the drawer | an unbounded raw state would push every column |
| §3 headline templates hard-code "are" | pluralise with the §7 map | it printed "1 are waiting" |
| §5 neutrals (slate) | cooler graphite neutrals, same semantic hues | fixes `--text-3` on needs tint (4.3:1) and gives the board its own tone |
| §10 header shows the board URL on row 3 | URL on the last line; row 3 is the ribbon plus totals; Needs you reasons wrap to at most 2 extra lines | the Mod keeps "why" readable at 80 columns; Needs you is never folded anyway |
| §2 row padding 8 px 12 px, min height 40 | 8 px 16 px (plus 4 px edge), min height 48 | two-line strip (title plus reason) |

## 13. Implementer checklist

1. `python3 docs/board-design/check_layout.py check <path-or-url-of-board>` exits 0. It loads the page at 390, 480, 600,
   768, 900, 1024, 1280, 1440 and 1600 px, with and without a selected task and with a `#stress` fixture, and fails on
   page horizontal scroll, any element wider than its box without ellipsis, or overlapping siblings in a strip, card,
   header or toolbar. (Set `PW_CHROME` to a Chromium binary if Playwright's own is not installed.) For the real board:
   `SNAPSHOT=tests/fixtures/board/superseded_snapshot.json ROWSEL='.task > *' python3 docs/board-design/check_layout.py check ale/board.html`
   calls the page's `applySnapshot()` and uses the page's own row class. On today's board this exits 1
   ("overflow state cancel 228>159", the Superseded label), which is the defect it must catch. Add a fixture with every
   state in brief §4, a 60-character title and a 30-character id.
2. The longest state word ("Needs your answer") is not truncated at any width: the script lists truncated state words;
   only "Unknown: {raw}" may appear.
3. Every icon id in section 7 exists as a `<symbol>` in the page, and every state icon draws at a readable size:
   each `use` inside a state cell has `getBBox().width >= 10` (the current board gives 4 to 8).
4. Contrast: `python3 docs/board-design/contrast.py` prints `bad 0` (update its token dict if tokens change); all text pairs
   4.5:1 or more, borders and icons 3:1 or more, in both themes.
5. With a greyscale filter (DevTools "achromatopsia"), every state is still named by icon plus word; Waiting and Canceled
   strips show a dashed edge.
6. No web fonts, no external URLs, no `http` substring in the HTML; `grep -c "font-face\|googleapis" ale/board.html` is 0.
7. `prefers-reduced-motion: reduce`: no animation or transition runs (the CSS contains the literal media query).
8. Keyboard: Tab reaches filters, cards, every strip; Enter opens the drawer; Esc closes it and focus returns to the strip;
   focus is always visible (2 px `--border-strong` outline, `--focus` fill on buttons).
9. No drawer element is in the layout when no task is selected (`getComputedStyle(drawer).display === "none"`).
10. Headline reads "Nothing is running. 1 needs you; 1 is waiting behind them." on the real-run fixture.
11. Terminal: render the Mod at 80 and 120 columns; no line is wider than the terminal; glyph, id and state word start at
    the same column on every task row; Needs you reasons are complete.
12. Visual regression: compare against the `board-v2-*.png` files at the same viewports (book ch 11.2: review diffs by eye
    for a redesign; do not trust a pixel threshold).
