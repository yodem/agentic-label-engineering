# ALE run board: design contract

A live monitor for a run of AI coding tasks. Aesthetic: the flight-strip bay of an air traffic control room. Every task is a strip: a thick coloured state edge on the left, then state icon and word, task id, title, and one reason sentence. Dense, calm, typographic. No cards-in-cards, no shadows on rows, no gradients, no avatars, no charts, no sidebar navigation.

## Colors (light)
- bg: #F1F3F5
- surface: #FFFFFF
- text: #17202B
- text-2: #44505E
- text-3: #5E6977
- border: #DCE1E6
- needs (amber): #B45309, tint #FEF3C7
- fail (red): #B91C1C, tint #FEE2E2
- run (blue): #1D4ED8, tint #DBEAFE
- verify (violet): #6D28D9, tint #EDE9FE
- ready (teal): #0F766E, tint #CCFBF1
- wait (grey): #44505E, tint #E9EDF1
- done (green): #15803D, tint #DCFCE7

## Colors (dark)
- bg: #11161C
- surface: #19202A
- text: #EDF1F5
- text-2: #C3CCD6
- text-3: #93A0AE
- border: #2B3542
- needs #FBBF24 tint #3A2A0A; fail #F87171 tint #3F1414; run #60A5FA tint #152542; done #4ADE80 tint #0F2E1C; wait #C3CCD6 tint #222B36

## Typography
- font: system UI sans (San Francisco / Segoe UI), ids and numbers in system monospace with tabular figures
- verdict: 24px, 600, 1.25
- run-id: 13px mono, 400
- section: 15px, 600
- row: 14px, 400; state word 13px 600
- meta: 12px, 400
- weights 400 and 600 only; sentence case everywhere, never all caps

## Spacing
- 4, 8, 12, 16, 24, 32, 48 px
- row min height 44px, padding 10px 16px

## Border radius
- strip: 0 on the left edge, 6px elsewhere
- chip: 999px
- sheet: 12px

## Rules
- Every status is icon plus word plus colour, never colour alone.
- The state edge is 4px wide in the state colour.
- Long text truncates with an ellipsis; nothing overlaps.
