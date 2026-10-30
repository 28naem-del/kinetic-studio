# Scene reference

31 scene types. Any scene works in any mode; the mode only sets theme, music, camera feel,
default transition and audit thresholds. The same information is live from the `list_scenes` MCP tool
and `kstudio scenes -v`; the full JSON Schema comes from `get_schema`. Regenerate this page with
`python docs/tools/generate.py --scenes`.

## Times and lengths

Every time or length takes seconds (`1.5`, `"1.5s"`, `"250ms"`) or beats at the music tempo:
`"4b"`, `"1/2b"`, `"1bar"`, `"bar3"` (start of bar 3), or a marker or scene id with an offset
(`"drop-2b"`, `"intro+1.25s"`, `"end-1b"`). Write scene lengths in whole beats.

## Keys every scene accepts

| key | meaning |
|---|---|
| `type` | the scene type (below) |
| `id` | a name other times can refer to (`"drop"`, `"drop-2b"`) |
| `dur` | length, ideally whole beats (`"4b"`) |
| `at` | start time; default: right after the previous scene |
| `transition` | transition into this scene (see below); default: the mode's |
| `transition_dur` | transition window length (`"1/2b"`) |
| `transition_sfx` | `false` silences the transition sound (pick another with `"sfx"` in a transition dict) |
| `ground` | backdrop colour: a theme colour name (default `bg`) or a hex colour |
| `ground_style` | `solid`, `gradient`, `grid`, `dots`, `blobs`, `paper`, `mesh` |
| `camera` | overrides of the mode camera: `push`, `carry`, `drift`, `drift_hz`, `energy`, `roll`, `truck`, `yaw`, `pitch`, `ground_depth`, `bump`, `shake`, `dist` |

## Transitions

Set on the incoming scene: a name, a shorthand (`"whip-left"`, `"wipe-circle"`) or a dict such as
`{"kind": "wipe", "shape": "circle", "dur": "1b", "dir": "up"}`. Directions: `left`, `right`, `up`, `down`. Wipe shapes: `rect`, `circle`, `diagonal`, `type`.

| kind | window (beats) | sound at the cut |
|---|---|---|
| `cut` | 0 | - |
| `flash` | 0 | hit |
| `cross` | 1 | - |
| `fade` (alias of `cross`) | 1 | - |
| `dissolve` | 1 | - |
| `whip` | 0.5 | whoosh |
| `push` | 0.75 | swish |
| `zoom` | 0.75 | whoosh |
| `squash` | 0.5 | swish |
| `wipe` | 1 | swish |
| `slice` | 1 | swish |
| `split` | 0.75 | swish |
| `match` | 0 | - |

## Kinetic type

### `burst`

The drop: a word zooms from deep space with speed lines, rings and a flash.

| key | default |
|---|---|
| `text` | `"NOW"` |
| `weight` | `900` |
| `width` | `125` |
| `lines` | `70` |
| `color` | `"accent"` |
| `land` | `"1/3b"` |
| `rows` | `3` |
| `row_stagger` | `"1/8b"` |

### `countdown`

Big numbers counting down on the beat (3, 2, 1) with a ring sweep.

| key | default |
|---|---|
| `from` | `3` |
| `per` | `"1b"` |
| `label` | `""` |

### `marquee`

Tilted rows of repeating type scrolling in opposite directions, centre row hero.

| key | default |
|---|---|
| `text` | `"MOTION"` |
| `rows` | `7` |
| `tilt` | `-8` |
| `speed` | `420` |
| `hero` | `true` |
| `weight` | `900` |
| `speed_beats` | `null` |

### `scramble`

Glyph scramble that resolves letter by letter (code / reveal feel).

| key | default |
|---|---|
| `text` | `"DECRYPTED"` |
| `label` | `""` |
| `stagger` | `"1/8b"` |
| `first` | `"1b"` |
| `role` | `"mono"` |
| `weight` | `800` |
| `caret` | `true` |
| `lines` | `[]` |
| `rows` | `5` |

### `slam`

One word per beat, punched in at full width; grounds can flip per word.

| key | default |
|---|---|
| `per` | `"1b"` |
| `flip` | `true` |
| `grounds` | `["bg", "accent", "paper"]` |
| `style` | `"fill"` |
| `accent_words` | `[]` |
| `weight` | `900` |
| `width` | `112` |
| `echo` | `true` |
| `sfx` | `"hit"` |
| `first_sfx` | `"impact"` |
| `land` | `"1/3b"` |
| `rows` | `4` |
| `rows_above` | `2` |
| `row_stagger` | `"1/8b"` |

### `stack`

Lines that each fill the width, arriving one per `per` (default 1 beat).

| key | default |
|---|---|
| `lines` | `["MAKE", "IT", "MOVE"]` |
| `per` | `"1b"` |
| `accent_last` | `true` |
| `weight` | `900` |
| `outline` | `[]` |
| `align` | `"center"` |
| `sfx` | `"whoosh"` |
| `gap` | `0.06` |
| `land` | `"1/2b"` |
| `ghost` | `true` |
| `pump` | `true` |
| `width` | `118` |

### `type3d`

Extruded 3D type: swings in flat, then extrudes on the `impact` beat.

| key | default |
|---|---|
| `text` | `"3D"` |
| `depth` | `18` |
| `weight` | `900` |
| `width` | `110` |
| `swing` | `75` |
| `sub` | `""` |
| `color` | `"ink"` |
| `side` | `"accent"` |
| `side_luma` | `0.55` |
| `extrude` | `0.5` |
| `impact` | `"1b"` |
| `rows` | `4` |
| `row_stagger` | `"1/8b"` |
| `sfx` | `"whoosh_big"` |
| `impact_sfx` | `"impact"` |
| `y` | `0.42` |


## Explainer

### `bullets`

Numbered list with large display numerals (headline 168): the heading (title 126) on the anchor and the list's rules and ghosted numerals on beat 0, then one item per `stagger` (default 1b) from `first` (default 1b) with a tick: its numeral and accent rule come in, its text rises, and the previous item steps back to muted. Rows share the anchor -> bottom safe line. Items are strings or {text, detail, icon}; numbered false sets check marks.

| key | default |
|---|---|
| `index` | `""` |
| `kicker` | `""` |
| `heading` | `""` |
| `items` | `[]` |
| `stagger` | `"1b"` |
| `first` | `"1b"` |
| `numbered` | `true` |
| `page` | `""` |

### `cover`

Explainer cover: running head, a 168 px headline on the anchor (visible from frame 0: lines rise together over half a beat) and the standfirst rule on beat 0, one word of the headline on the accent highlighter on beat 1, the standfirst on the bottom safe line on beat 2.

| key | default |
|---|---|
| `index` | `"01"` |
| `kicker` | `""` |
| `title` | `"Title"` |
| `accent` | `""` |
| `subtitle` | `""` |
| `sfx` | `"tick"` |
| `page` | `""` |

### `definition`

Dictionary entry on the anchor. Beat 0: the term in display 224 with the page rules. Beat 1: the accent highlighter behind the term (published as "term" for a match cut), pronunciation / part of speech in mono, the definition in body 53 and the optional `note`. Beats 2, 3, ...: one figure of `stats` per beat on the bottom safe line; numeric figures after the first count up from the first one (a slow miss reads slow).

| key | default |
|---|---|
| `index` | `""` |
| `kicker` | `""` |
| `term` | `"Term"` |
| `phonetic` | `""` |
| `pos` | `"noun"` |
| `definition` | `""` |
| `note` | `""` |
| `stats` | `[]` |
| `highlight` | `true` |
| `page` | `""` |

### `diagram`

Labelled diagram that builds on the beat: nodes every `stagger` (default 1/2b), each edge drawn on the off-beat 16th before the node it lands on, callouts on the following whole beats; a tick on every entry. The heading sits in the running-head band and the figure fills the anchor -> bottom safe line. The first node takes over a published "term" (match cut). Layouts: ladder (bars, default for portrait explainer specs), chain, radial, manual.

| key | default |
|---|---|
| `index` | `""` |
| `kicker` | `""` |
| `heading` | `""` |
| `nodes` | `[]` |
| `edges` | `null` |
| `layout` | `"chain"` |
| `callouts` | `[]` |
| `stagger` | `"1/2b"` |
| `first` | `0.0` |
| `node_r` | `0` |
| `caption` | `""` |
| `page` | `""` |

### `keyfact`

One big figure or short phrase, flush left on the anchor (display role, scaled up to fill the content width), a title-size label on beat 1 and a body detail on beat 2. The value takes over a published "term" when it follows a match cut.

| key | default |
|---|---|
| `index` | `""` |
| `kicker` | `""` |
| `value` | `"4"` |
| `label` | `""` |
| `detail` | `""` |
| `color` | `"accent"` |
| `page` | `""` |


## Promo

### `brand`

Brand end card: an app icon (gradient tile + `mark` icon name or image path) lands, then the wordmark (`text`, display role 224 px, `weight` 700) is revealed by an accent mask bar that travels across it (out_quint from the scene start) and lands on the beat `hit_at` (logo hit + music stop), a sheen sweeps the wordmark, `tagline` and `url` rise in, and `badges` (["App Store", ...]) sit on the bottom safe line from beat 2.

| key | default |
|---|---|
| `text` | `"brand"` |
| `tagline` | `""` |
| `url` | `""` |
| `mark` | `"spark"` |
| `weight` | `700` |
| `size` | `null` |
| `badges` | `[]` |
| `badge_kicker` | `"Download on"` |
| `hit_at` | `"1b"` |
| `stop_music` | `true` |

### `cta`

Call to action on the grid: tracked `kicker`, the `headline` (168 px, 700) flush-left on the shared anchor, a full-width pill button that springs in on beat 1 and pulses on every beat (spring landing one frame after the kick, with a ring wave), a touch that presses it on the downbeat `tap_at` (click SFX + ripple; the label morphs to `done` if set), a `note` under the button and the `sub` line (platforms) on the bottom safe line. `lockup` (product name) puts a small app icon (`mark`) + name on the top safe line so the block spans the frame.

| key | default |
|---|---|
| `kicker` | `""` |
| `headline` | `"Try it free"` |
| `button` | `"Get the app"` |
| `sub` | `""` |
| `note` | `""` |
| `done` | `""` |
| `tap_at` | `null` |
| `enter` | `"1b"` |
| `pulse` | `0.045` |
| `lockup` | `""` |
| `mark` | `"spark"` |

### `device`

Hero device shot. A phone at `height` of the frame (0.70) springs up from below and turns in yaw from yaw[0] to yaw[1] (in_out_quint over `tilt`, default 4b), then eases back half way over the rest of the shot; the bezel highlight and glass sheen follow the tilt; a soft contact + ambient shadow sits under it. Above it: an optional tracked `kicker`, the `headline` at the headline role (168 px, 700) flush-left on the grid, and `sub` set as a right-aligned standfirst. `callouts` ({text, icon, side, y, x, dy, color}) pop in on the beat (`first_callout`, `callout_gap`): a pill (spring) inside the safe area with a leader line to the point (x, y) on the screen (fractions of the screen), tracked through the tilt. `screen` = screenshot path (9:19.5) or the procedural `ui`; `scroll` = list scroll (px@1080). Legacy `swing` (degrees) sets a symmetric yaw when `yaw` is not given.

| key | default |
|---|---|
| `frame` | `"phone"` |
| `screen` | `""` |
| `ui` | `{}` |
| `kicker` | `""` |
| `headline` | `""` |
| `sub` | `""` |
| `callouts` | `[]` |
| `scroll` | `0` |
| `swing` | `null` |
| `yaw` | `null` |
| `pitch` | `[7, 2]` |
| `tilt` | `"4b"` |
| `height` | `0.7` |
| `first_callout` | `"3b"` |
| `callout_gap` | `"1b"` |
| `land` | `"1b"` |

### `features`

Features, one at a time: every `per` beats (default 2b) a full-bleed colour panel wipes in from the right with a large icon, and the label (title role, 126 px) + body line rise in under it on the grid; the outgoing label leaves upward (in_quart). `heading` becomes the panel kicker with a 01/04 counter; progress segments sit on the bottom safe line. Items: {icon, title, text, color}. `layout: "grid"` (or `columns` > 0) keeps the v0.1 cards.

| key | default |
|---|---|
| `heading` | `""` |
| `items` | `[]` |
| `per` | `"2b"` |
| `stagger` | `"1/2b"` |
| `first` | `0.0` |
| `columns` | `0` |
| `layout` | `"solo"` |


## Data

### `bars`

Horizontal bars over the full content width: tracks and labels on the cut, bars grow (out_expo over `grow`) on a `stagger` from `first` with their figures counting on 16ths; the previous-period marks and deltas land after the last bar, then the callout inside the highlighted bar.

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `""` |
| `heading` | `""` |
| `items` | `[]` |
| `prefix` | `""` |
| `suffix` | `""` |
| `decimals` | `0` |
| `highlight` | `""` |
| `horizontal` | `null` |
| `stagger` | `"1/2b"` |
| `first` | `"1b"` |
| `grow` | `"1b"` |
| `callout` | `""` |

### `candles`

Candlestick chart on the plot band. History sweeps in over the first beat; the last `live` candles then form one per beat with real OHLC motion (open, a path through the low/high to the close, wick tracking the extremes so far). The last full candle closes above `level` on its beat (impact, music hit, the level flips to the accent); the next candle keeps forming.

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `""` |
| `heading` | `""` |
| `seed` | `21` |
| `n` | `30` |
| `start` | `100.0` |
| `end` | `null` |
| `vol` | `0.025` |
| `drift` | `0.003` |
| `ohlc` | `null` |
| `level` | `null` |
| `first` | `"0b"` |
| `live` | `null` |
| `ma` | `7` |
| `prefix` | `"$"` |
| `decimals` | `2` |
| `callout` | `""` |
| `reveal` | `null` |

### `counter`

Hero figure on the anchor counting up on 16ths over `count` (odometer roll, weight 300 -> 800) and landing on the downbeat after it; label, a delta pill on the landing and a full-width sparkline (published as "spark" for a match cut into a line chart).

| key | default |
|---|---|
| `value` | `null` |
| `from` | `0` |
| `decimals` | `0` |
| `prefix` | `""` |
| `suffix` | `""` |
| `label` | `""` |
| `kicker` | `""` |
| `heading` | `""` |
| `delta` | `null` |
| `delta_note` | `""` |
| `count` | `"2b"` |
| `first` | `"0b"` |
| `spark` | `true` |
| `seed` | `3` |
| `color` | `"ink"` |
| `delay` | `null` |

### `line`

Line chart on the full plot band. With a "spark" hand-off the counter's sparkline grows into the plot; otherwise the line draws in over the first beat. A read cursor then travels the series (in_out_quint over `draw`), lighting the area behind it; event callouts land on the beat after the cursor passes them; the change figure lands when the read ends.

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `""` |
| `heading` | `""` |
| `series` | `{"seed": 11, "n": 60}` |
| `prefix` | `"$"` |
| `suffix` | `""` |
| `decimals` | `0` |
| `draw` | `"3b"` |
| `first` | `"1b"` |
| `events` | `[]` |
| `x_labels` | `[]` |
| `change` | `true` |
| `dek` | `""` |

### `stat_grid`

2 x 2 (or 1 x N) cards filling the plot band: cards cascade in on 16ths from `first`, figures count on 16ths and land together on the downbeat after `count`; each card carries a signed delta and a mini spark or gauge. The first card is published as "card0".

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `""` |
| `heading` | `""` |
| `items` | `[]` |
| `count` | `"2b"` |
| `stagger` | `"1/4b"` |
| `first` | `"0b"` |

### `ticker`

Watchlist rows on the grid (symbol, name, price in mono, change pill), cascading in on 32nds; prices re-quote on every beat with an odometer roll and a row flash in the sign colour. The first row can be born from the stat grid's "card0". A breadth bar (advancing vs declining) closes the band; the market tape runs underneath.

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `""` |
| `heading` | `""` |
| `items` | `[]` |
| `tape` | `null` |
| `speed` | `160` |
| `jitter` | `0.003` |
| `breadth` | `true` |


## Social

### `follow`

Call-to-follow end card. The pill of the previous karaoke scene is handed over on the cut (match on "pill") and springs out into a full-width follow button; the headline rises in line by line on the 8ths; the button is pressed on `tap_at` (default beat 2) with a click + chime, its label morphs to `done`, and it kicks on every beat after (spring). `stats` ([{"value": "312", "label": "clips"}, ...]) sit in a row above the chrome, rising on the 8ths after the press; numeric values count up (out_quint over 1 beat).

| key | default |
|---|---|
| `ground_style` | `"paper"` |
| `backdrop` | `true` |
| `kicker` | `"Follow for more"` |
| `text` | `"One idea a day, set to the beat."` |
| `button` | `"Follow"` |
| `done` | `"Following"` |
| `tap_at` | `"2b"` |
| `note` | `""` |
| `label` | `"follow"` |
| `weight` | `650` |
| `stats` | `[]` |

### `hook`

Hook opener: a stacked headline that lands one line per beat.

| key | default |
|---|---|
| `ground_style` | `"paper"` |
| `backdrop` | `true` |
| `lines` | `["Two", "seconds.", "That is all", "you get."]` |
| `kicker` | `""` |
| `roles` | `[]` |
| `emphasis` | `[]` |
| `stagger` | `"1b"` |
| `first` | `"0b"` |
| `weight` | `760` |
| `timer` | `true` |
| `label` | `"hook"` |
| `mark` | `2.0` |

### `karaoke`

Spoken-caption karaoke on the 8th grid, one sentence per page.

| key | default |
|---|---|
| `ground_style` | `"paper"` |
| `backdrop` | `true` |
| `text` | `""` |
| `words` | `null` |
| `timing` | `"beat"` |
| `per` | `0.5` |
| `lines_per_page` | `null` |
| `size` | `"title"` |
| `lead` | `0.0` |
| `tail` | `0.6` |
| `emphasis` | `[]` |
| `kicker` | `""` |
| `page_on` | `"bar"` |
| `max_lines` | `3` |
| `audiogram` | `true` |
| `ticks` | `true` |
| `label` | `"caption"` |
| `weight` | `650` |
| `riser` | `true` |
| `speaker` | `null` |
| `pulse` | `"1/2b"` |
| `gap` | `0.0` |

### `quote`

Pull-quote card revealed word by word on the 8th grid.

| key | default |
|---|---|
| `ground_style` | `"paper"` |
| `backdrop` | `true` |
| `text` | `""` |
| `author` | `""` |
| `role` | `""` |
| `size` | `"title"` |
| `stagger` | `"1/2b"` |
| `first` | `"1/2b"` |
| `kicker` | `""` |
| `emphasis` | `[]` |
| `weight` | `440` |
| `max_lines` | `5` |
| `exit` | `false` |
| `ticks` | `true` |
| `riser` | `true` |
| `label` | `"quote"` |
| `card` | `true` |
| `meta` | `""` |
| `speaker` | `null` |


## Any mode

### `end_card`

Closing card on the grid, flush-left from the shared top anchor: a brand lockup (accent badge with an icon + wordmark on the subhead role), the statement (title role), an accent line (body), a CTA pill and the one handle, with small print standing on the bottom safe line. Elements enter one per half beat on the registry "enter" ease and the badge and CTA arrow pulse on every beat. The card owns the handle: the film's corner handle is hidden while it is on screen, and the handle colour is picked for >= 4.5:1 contrast on the card's ground.

| key | default |
|---|---|
| `icon` | `"book"` |
| `brand` | `""` |
| `title` | `"For learning only."` |
| `body` | `"Not medical advice."` |
| `small` | `""` |
| `handle` | `""` |
| `cta` | `""` |
| `stop_music` | `false` |
| `sfx` | `"chime"` |
| `align` | `"left"` |
| `step` | `"1/2b"` |

### `image`

Full-bleed picture with a Ken Burns push, gradient scrim and an optional caption set flush-left on the bottom safe line.

| key | default |
|---|---|
| `src` | `""` |
| `zoom` | `[1.0, 1.14]` |
| `pan` | `[[-0.3, 0.0], [0.3, 0.0]]` |
| `caption` | `""` |
| `scrim` | `0.55` |
| `caption_style` | `"title"` |

### `logo`

Logo sting: a line draws, the wordmark rises through it, tracking tightens, tagline. Centred on the optical centre of the safe zone (a sting is the one place centring belongs).

| key | default |
|---|---|
| `text` | `"BRAND"` |
| `tagline` | `""` |
| `mark` | `""` |
| `weight` | `800` |
| `role` | `"display"` |
| `stop_music` | `true` |
| `line` | `true` |
| `hit_at` | `0.32` |
| `url` | `""` |
| `style` | `"display"` |

### `text`

A paragraph that fades in word by word, optional heading and highlighted terms. Set on the grid from the shared top anchor; `size` (reference px) overrides the body role size.

| key | default |
|---|---|
| `heading` | `""` |
| `body` | `""` |
| `highlight` | `[]` |
| `size` | `null` |
| `align` | `"left"` |
| `rate` | `0.06` |
| `style` | `"body"` |
| `heading_style` | `"title"` |

### `title`

Editorial title card: tracked kicker on the shared top anchor, a balanced headline under it, and the subtitle set as a standfirst on the bottom safe line under a growing hairline. `size` (reference px) overrides the `style` role size; `title_role` picks the theme face.

| key | default |
|---|---|
| `kicker` | `""` |
| `title` | `"Title"` |
| `subtitle` | `""` |
| `align` | `"left"` |
| `size` | `null` |
| `style` | `"headline"` |
| `title_role` | `"display"` |
| `weight` | `null` |
| `accent_line` | `-1` |
| `sfx` | `"swish"` |
