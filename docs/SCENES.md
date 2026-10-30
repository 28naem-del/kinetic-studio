# Scene reference

Generated with `bin/kstudio scenes -v`. Every scene also accepts the common keys `id`, `dur`, `at`, `transition`, `transition_dur`, `transition_sfx`, `ground`, `ground_style` and `camera`.

```
bars         Bar chart growing on a stagger with counting value labels and a highlighted bar.
              kicker = ""
              title = ""
              items = []
              prefix = ""
              suffix = ""
              decimals = 0
              highlight = ""
              horizontal = null
              stagger = 0.12
              first = 0.4
              grow = 0.9
bullets      Heading + numbered points revealed one by one; the newest point is emphasised.
              kicker = ""
              heading = ""
              items = []
              stagger = 0.9
              first = 0.6
              numbered = true
burst        The drop: a word zooms from deep space with speed lines, ring and flash.
              text = "NOW"
              weight = 900
              width = 125
              lines = 70
              color = "accent"
candles      Candlestick chart revealing candle by candle with a moving average and last-price line.
              kicker = ""
              title = ""
              seed = 21
              n = 36
              start = 100.0
              vol = 0.025
              drift = 0.003
              ohlc = null
              reveal = 2.4
              first = 0.3
              ma = 7
              prefix = "$"
              decimals = 2
countdown    Big numbers counting down on the beat (3, 2, 1) with a ring sweep.
              from = 3
              per = "1b"
              label = ""
counter      A big number rolling up to its value (mono, tabular), label, delta pill, sparkline.
              value = 1000
              from = 0
              decimals = 0
              prefix = ""
              suffix = ""
              label = ""
              kicker = ""
              delta = ""
              count = 1.4
              delay = 0.2
              spark = true
              seed = 3
              color = "ink"
cta          Call to action: headline, pulsing button, simulated tap, sub-line.
              headline = "Try it free"
              button = "Get the app"
              sub = ""
              tap_at = 1.1
definition   Dictionary-style card: term, pronunciation, part of speech, definition, note.
              term = "Term"
              phonetic = ""
              pos = "noun"
              definition = ""
              note = ""
device       Phone (or browser) mockup with screenshot / procedural UI, 3D swing-in, float, scroll,
              frame = "phone"
              screen = ""
              ui = {}
              headline = ""
              sub = ""
              callouts = []
              scroll = 0
              swing = 28
              first_callout = 0.9
              callout_gap = 0.7
diagram      Labelled node/edge diagram that builds itself: nodes pop, edges draw with a comet,
              kicker = ""
              heading = ""
              nodes = []
              edges = null
              layout = "chain"
              callouts = []
              stagger = 0.45
              first = 0.5
              node_r = 0
              caption = ""
end_card     Calm closing card: icon badge, statement, small print, handle. Use for disclaimers.
              icon = "book"
              title = "For learning only."
              body = "Not medical advice."
              small = ""
              handle = ""
              stop_music = false
              sfx = "chime"
features     Grid of feature cards (icon, title, line) popping in on a stagger.
              heading = ""
              items = []
              stagger = 0.22
              first = 0.3
              columns = 0
image        Full-bleed picture with a Ken Burns push, gradient scrim and optional caption.
              src = ""
              zoom = [1.0, 1.14]
              pan = [[-0.3, 0.0], [0.3, 0.0]]
              caption = ""
              scrim = 0.55
karaoke      Big captions paged a few lines at a time; the active word gets a springy pill.
              text = ""
              words = null
              timing = "even"
              per = 0.5
              lines_per_page = 3
              size = 92
              lead = 0.25
              tail = 0.6
              emphasis = []
              kicker = ""
keyfact      One big figure or short phrase with a label: e.g. '4 amino acids'.
              value = "4"
              label = ""
              detail = ""
              color = "accent"
line         Line/area chart drawing left to right with a glowing comet head, live value badge,
              kicker = ""
              title = ""
              series = {"seed": 11, "n": 60}
              prefix = "$"
              suffix = ""
              decimals = 0
              draw = 2.6
              first = 0.3
              events = []
              x_labels = []
              change = true
logo         Logo sting: a line draws, the wordmark rises through it, tracking tightens, tagline.
              text = "BRAND"
              tagline = ""
              mark = ""
              weight = 800
              role = "display"
              stop_music = true
              line = true
              hit_at = 0.32
              url = ""
marquee      Tilted rows of repeating type scrolling in opposite directions, centre row hero.
              text = "MOTION"
              rows = 7
              tilt = -8
              speed = 420
              hero = true
              weight = 900
quote        Pull-quote: giant quote mark, words fading in on a stagger, author with rule.
              text = ""
              author = ""
              role = ""
              size = 84
              stagger = 0.07
scramble     Glyph scramble that resolves letter by letter (code / reveal feel).
              text = "DECRYPTED"
              label = ""
              stagger = 0.05
              role = "mono"
              weight = 800
              caret = true
slam         One word per beat, punched in at full width; grounds can flip per word.
              per = "1b"
              flip = true
              grounds = ["bg", "accent", "paper"]
              style = "fill"
              accent_words = []
              weight = 900
              width = 112
              echo = true
              sfx = "hit"
              first_sfx = "impact"
stack        Lines that each fill the width, arriving one per `per`, width-axis stretching in.
              lines = ["MAKE", "IT", "MOVE"]
              per = "1b"
              accent_last = true
              weight = 900
              outline = []
              align = "center"
              sfx = "whoosh"
              gap = 0.06
stat_grid    2x2 (or 1xN) cards, each with a counting value, label and delta.
              kicker = ""
              title = ""
              items = []
              count = 1.2
              stagger = 0.15
text         A paragraph that fades in word by word, optional heading and highlighted terms.
              heading = ""
              body = ""
              highlight = []
              size = 54
              align = "left"
              rate = 0.06
ticker       Watchlist with live-flickering prices and change pills, plus a scrolling tape.
              kicker = ""
              title = ""
              items = []
              tape = true
              speed = 160
              jitter = 0.004
title        Kicker + multi-line title (masked rise) + subtitle + growing rule.
              kicker = ""
              title = "Title"
              subtitle = ""
              align = "left"
              size = 128
              title_role = "display"
              weight = 600
              accent_line = -1
              sfx = "swish"
type3d       Extruded 3D type swinging in on a spring, then breathing in perspective.
              text = "3D"
              depth = 18
              weight = 900
              width = 110
              swing = 75
              sub = ""
              color = "ink"
              side = "accent"
              sfx = "whoosh_big"
```
