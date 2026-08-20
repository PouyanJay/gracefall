# The creature: a cat with no pixels in it

gracefall needs a face, and there is only one honest way for this project
to draw one: out of charts. Every limb of the creature is a v1 span, so
the same bytes are box characters and blocks in a plain terminal and
smooth curves, beads and gradients in a terminal that implements OSC 4700.
No new type was added for it, and nothing about it is a picture. It is
charts all the way down.

That is not a stunt. It is the strongest possible statement of what the
protocol is for: if a mascot can be assembled out of seven declarative
data types, an application's real chart certainly can.

## The parts

| Part | Span | What it is | Where |
|---|---|---|---|
| the cat | `scatter` | one braille canvas, two dots per cell across and four down, the whole drawing in a single span | sizes 4, 6, 8 |
| the face | `lanes` | the same primitive a commit graph row is made of: `l` and `r` meeting as an ear, `d` a commit disc as an eye, `h` a rule as a mouth, and a tail that wags | sizes 1 and 2, and `frame()` at every size |
| the load | `meter` | the reading under the drawing | every size above 1 |
| the shaded cat | `heat` | one span a row, ten levels of ink a cell, tone rather than outline | sizes 16 and up |

`spark`, `dist`, `flow` and `heat` are the types the creature does not
use. It used to have `spark` arms and a `heat` glow, and they went when
the body did: a curve per limb is what you reach for when a row is all
you have, and a canvas is better when you have four.

## Three techniques, and where each one wins

| Technique | Sizes | A cell is | Good at | Bad at |
|---|---|---|---|---|
| `lanes` | 1-2 | one glyph | reading at a glance, one row | any detail |
| `scatter` | 4-12 | 8 braille dots | outlines, ears, whiskers | tone, fills |
| `heat` `ramp` | 16+ | 10 levels of ink, and up to 3x3 sub-cells for a receiver that draws it | volume, shading, faces | thin lines, small frames |

The thresholds are not taste. Four dot rows cannot hold ears above eyes
above a mouth, so below four rows braille loses to a glyph a cell. Tone
is made of gradients, and a gradient across twenty cells is four
characters wide, so below `shade.COLS_MIN` columns it turns to mush and
the outline wins. A figure keeps its proportions, so its width follows
its height, and sixteen rows is simply the first size wide enough to
shade. `test_each_size_uses_the_technique_that_wins_at_that_size` and
`test_a_shaded_size_is_always_wide_enough_to_shade` hold that.

Each of the three also fails differently, which is worth knowing before
reaching for one. `lanes` and `scatter` are on or off, so their risk is a
feature that lands between cells and vanishes: the creature's whiskers
are drawn in braille and deliberately absent from the shaded model, where
a whisker thin enough to read as a whisker falls between two rows and
flickers, and thick enough to sample is a bar across the frame. Tone
fails the other way: a gradient that runs the whole range takes the
silhouette apart, because the lit half of the head lands on a space. That
is what the ink floor in `shade.py` is for, and
`test_the_body_never_falls_to_nothing` is the rule.

Tone can do one thing neither outline can, though, and it is why the face
survives at small sizes: a soft band returns a partial value, and a
partial value is a lighter character rather than no character. The ramp
antialiases it. A hard test would give a mouth that appears at one size,
breaks into dashes at another and vanishes at a third.

## Why braille, and why not everywhere

A `lanes` cell gets a whole glyph. A `scatter` cell gets eight dots, two
across and four down, so thirteen cells by four rows is a 26 x 16 canvas
and twenty six by eight is 52 x 32. That is the difference between a face
suggested by punctuation and a face drawn.

The trade is real in both directions, and the sizes are split on it:

- **Four dot rows cannot hold a face.** One terminal row is four dots of
  height, and everything a cat needs (ears above eyes above a mouth) has
  to fit in it. Three separate attempts at a one-row braille cat all read
  as a smudge. At one and two rows the creature is `lanes`, where each of
  thirteen cells gets a glyph of its own.
- **`scatter` draws as discs.** In the enhanced view every point becomes a
  circle, so the drawn cat is a stipple rather than an outline. That is a
  consistent reading of the same dots rather than a different picture, but
  it is why the cat is drawn as outlines and never as fills: a filled
  shape would be a solid mass of overlapping circles.
- **No trend line.** `scatter` carries an optional least-squares fit in
  `m` and `tb`, and a receiver draws it when both are there. Through a
  drawing it is a meaningless number and a line across the face, so the
  creature passes `trend=False`. SPEC.md requires a derived value
  *shipped* in the envelope to be honest, not that one is shipped.
- **Explicit bounds.** `scatter`'s canvas defaults to the extent of its
  own points, so a drawing that breathes would rescale on every frame as
  its outermost dot moved. The creature fixes `xlo`, `xhi`, `ylo` and
  `yhi` to the canvas.

## The five moods

Their one-row fallback, at `cpu` 0.62 and tick 3 (the blink is out of
phase), and what the drawn cat does with the same mood:

| Mood | `frame()` | the cat | Colour |
|---|---|---|---|
| idle | `╱╲ ● ── ● ╱╲─` | ears up, eyes open, a level mouth | teal |
| working | `╱╲ ● ││ ● ╱╲─` | mouth open on something | amber |
| happy | `╱╲ ● ╲╱ ● ╱╲─` | a smile | teal |
| sad | `╱╲ ● ╱╲ ● ╱╲─` | a frown | coral |
| sleepy | `╱╲ ─ ── ─ ╱╲╲` | eyes shut, breathing at half speed | dim |

The mouth is the mood, and it is the mood *in cells*, not in colour. A
mono terminal, a pipe and a screen reader all take the colour away, and a
mood a reader cannot tell from another mood once it is gone is not a mood.
`test_every_mood_has_its_own_face` asserts all five differ with the colour
stripped off.

A `ci` of `fail` turns the cat coral whatever the mood is, and a dirty
tree turns the meter amber. Both are colour only, so the fallback keeps
its shape and only the roles move, which is what colour roles are for.

## The sizes

`SIZES` is 1, 2, 4, 6 and 8 terminal rows, and `WIDTHS` is the cells each
one is wide: 13, 13, 13, 20 and 26.

Braille dots are square. A cell is two dots across and four down, and a
cell is about twice as tall as it is wide, so the dots come out square and
a taller creature needs proportionally more columns or the cat is stranded
in the middle of a letterbox. That is where the widths come from; they are
not a style choice.

```
size 1   the lanes face, and nothing else       ╱╲ ● ── ● ╱╲│
size 2   the face, and the reading under it     ╱╲ ● ── ● ╱╲│
                                                  ████▌▁▁▁
size 4   the cat on three rows, then the meter   26 x 12 dots
size 6   the cat on five rows                    40 x 20 dots
size 8   the cat on seven rows                   52 x 28 dots
size 12  the cat on eleven rows                  68 x 44 dots
size 16  the shaded cat, 30 cells x 15 rows      tone, not dots
```

The cat is authored once, in a 0..1 box at a fixed ratio, and fitted into
whatever canvas the size gives it. Fitted and centred, never stretched: a
canvas three times wider than it is tall would otherwise hold a cat three
times wider than it is tall, which is a different animal.

Size 1 is the line beside a prompt or on a live status line, and `gfl pet`
defaults to 8 because it owns the screen it is on. `frame(tick)` is always
the one-row lanes face at every size, and always `WIDTH` cells, so a
caller with one line to spend never has to know which size it asked for
and never has to pad around it.

## Signals

All optional, all with a default that means "nothing was measured", so
`Creature().frame(0)` draws.

| Signal | Range | Reads as |
|---|---|---|
| `cpu` | 0..1 | the meter, and how wide the eyes open |
| `rate` | >= 0 | how fast it breathes and how fast the tail wags |
| `latency` | >= 0 | how far the whiskers droop |
| `ci` | `"pass"`, `"fail"`, `None` | a failure turns the cat coral |
| `dirty` | bool | an uncommitted tree turns the meter amber |

`rate` and `latency` arrive in whatever units the caller measures in, so
they go through `v / (1 + v)`, which maps zero to infinity onto zero to
one smoothly and needs no invented constant. `cpu` is the one signal with
a natural range, and it is the one the meter shows directly.

`mood_for(signals)` is the suggested reading when a caller has only
measurements: a failing build is sad, real load is working, a green build
on a clean tree is happy, a clean quiet tree is sleepy, everything else is
idle. Nothing inside the module calls it, because a caller that knows what
it is doing should say the mood outright.

## The rules it has to keep

The first three come straight from SPEC.md, and the other three are what
make an animation honest and testable.

1. **Every limb is a v1 type.** If the creature ever seems to need a new
   one, that is the signal it is drifting toward pixels, which the spec
   excludes on purpose. `test_every_span_is_a_v1_type` is that rule.
2. **The fallback is generated from the same data.** Every limb goes
   through the same `spark`, `meter`, `lanes`, `heat` and `scatter`
   functions every other chart uses. There is no hand-written art here at
   all, which is why the two renderings cannot disagree.
3. **The drawing is one span, not one per row.** The cat is a single
   figure and its rows are not independent: split into a span per row,
   each row's canvas would be derived from whatever happened to be drawn
   in that row, and the head would change width depending on how much of
   it was ears. This is the one rule that changed when the creature became
   a cat, and it is why `lines()` must be joined with newlines before it
   is parsed: a multi-row span opens on its first row and closes on its
   last, so a row on its own is not a stream.
4. **The canvas is fixed, and the trend line is off.** Both are `scatter`
   defaults that suit a plot of measurements and not a drawing. See "Why
   braille" above.
5. **Frames are pure.** `(mood, signals, size, tick)` decides every byte:
   no clock, no randomness, no environment. A caller that wants motion
   counts ticks itself, and a test can assert a frame.
6. **Every frame of a size is the same width.** `Creature.width()` is
   constant for every mood, tick and signal at that size, so a caller
   redraws in place without clearing the line. `frame()` is `WIDTH` at
   every size, so the compact row never changes width either.
7. **The tick is a beat, and it is continuous.** Not a frame number: a
   caller passes fractional ticks, and every function that draws a limb is
   continuous in it. This is what separates the creature's speed from the
   caller's frame rate. Sampling twice as often gives twice as many
   distinct frames rather than each one twice, and raising a frame rate
   never speeds the creature up. `test_sampling_faster_gives_more_frames_
   not_faster_motion` is that rule.
8. **The belly is a reading and may not perform.** Everything else on the
   creature is decoration driven by a signal and may move however it
   likes. The belly is a `meter` whose value *is* `cpu`, so making it
   breathe would put a number on screen that is not the number. When a
   still creature needs to look alive, the motion comes from the air, the
   arms and the blink, never from the gauge.

### What "continuous" cannot buy you

Only a third of this is reachable in block characters, and it is worth
being plain about which third. The creature is thirteen cells wide with
eight vertical steps to a cell, and that is the entire resolution the
fallback has. An arm moving a hundredth of a cell renders as an arm not
moving, so past a certain frame rate the extra frames are identical ones.
Raising the rate stops helping and starts costing.

So the fallback's animation is real but coarse, and the fix for it was to
stop wasting the resolution that is there: no two consecutive frames that
are the same, no row that structurally cannot move, no blink too short to
see. `gfl pet --graphics` is the other half, where the same spans are
drawn instead of quantized and the motion is as smooth as the numbers are.

## Using it

```python
import time

from gracefall.creature import Creature, mood_for

c = Creature("working", {"cpu": 0.62, "rate": 1.4}, size=2)
start = time.monotonic()
while True:
    tick = (time.monotonic() - start) * 2.0   # beats, and fractional
    print("\n".join(c.lines(tick)))           # `size` lines, no newlines
    c.update(cpu=read_load())                 # merge, the rest stays
    c.mood = mood_for(c.signals)              # or say it yourself
    time.sleep(0.05)

one_line = c.frame(7.25)                      # the same row, at any size
```

Take the tick from the clock, not from a frame counter, and the two
decisions stay separate: the creature moves at two beats a second because
of the `2.0`, and you draw it as often as you like. `gfl pet` uses twenty
frames a second. The blink is one beat in twelve, which at two beats a
second is roughly how often a person blinks, and it is a window rather
than an instant so it survives being sampled at any rate.

## Where it appears

The creature lives around a tool, never on top of one. Claude Code, vim
and lazygit own every cell of the alternate screen and repaint them, so
nothing may walk about inside one, and a relay that drew over a running
full-screen program would corrupt its layout. The shell, the prompt, the
live line under a wrapped command and a launch splash are its home.
