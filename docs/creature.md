# The creature: a mascot with no pixels in it

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

| Limb | Span | What it is made of | What drives it |
|---|---|---|---|
| head (crown, eyes, mouth) | `lanes` | the same primitive a commit graph row is made of: `d` a commit disc, `m` a hollow merge, `h` a rule, `l` and `r` lanes curving away | the mood, and the blink from the tick |
| arms | `spark` | three cells each side, pinned to lo 0 hi 1 | `cpu` is the swing, `rate` the speed, `latency` the droop |
| belly | `meter` | nine cells | `cpu` |
| glow | `heat` | four cells each side of the crown, only when happy | the mood, pulsing with the tick |
| specks | `scatter` | the same four cells, when working or sleepy | the tick |

`dist` and `flow` are the two types the creature does not use. A histogram
and a pipeline strip have no body part that wants them, and inventing one
would be decoration rather than anatomy.

## The head is a lanes figure

A `lanes` row says where a lane is and where it goes, and a receiver draws
each cell so its ends meet the cell edges. That is what makes consecutive
rows of a commit graph read as continuous lanes, and it is exactly what
makes the creature's head read as one closed outline:

- `l` at column *i* curves from the top of column *i+1* to the bottom of
  column *i-1*, and `r` mirrors it. Two of them two columns apart meet at
  the top of the column between them, which is a crown: ` ╱ ╲ `. Swap
  them and they meet at the bottom, which is a smile: ` ╲ ╱ `.
- `d` draws its lane the full height of the row with a disc on it. That
  vertical stroke is the side of the head, and the disc is the eye.
- `h` is a rule along the bottom edge between its neighbours' centres, so
  a run of them is one line: a straight mouth, or a shut eye.

Because the crown's curves land where the eye lanes start, and the smile's
curves rise to meet them again, the three head rows of the largest size
draw one continuous figure with a bead on each side. Nothing coordinates
that across rows: each row is an independent span, and the geometry meets
because SPEC.md says where each cell's ends are.

The five head cells, at every size:

```
col     0      1      2      3      4
crown   .      l      .      r      .        ╱ ╲     awake
        .      r      .      l      .        ╲ ╱     sad, asleep
eyes    d      .      .      .      d       ●   ●
mouth   .      <----mouth---->      .
```

At size 1 and 2 the mouth's three cells sit between the eyes on the same
row, which is why the face reads as a kaomoji there and opens out into a
head at size 4.

## The five moods

Their fallback text, at `cpu` 0.62 and tick 3 (the blink is out of phase):

| Mood | size 1 | size 4 | Colour |
|---|---|---|---|
| idle | `▂▁▂ ● ─ ● ▂▁▂` | crown up, small mouth | teal |
| working | `▂▁▂ ●───● ▂▁▂` | crown up, mouth set flat, amber specks | amber |
| happy | `▂▁▂ ●╲ ╱● ▂▁▂` | crown up, smile, a violet glow both sides | teal |
| sad | `▂▁▂ ○╱ ╲○ ▂▁▂` | crown drooping, frown, hollow eyes | coral |
| sleepy | `▃▄▄ ─ ─ ─ ▄▄▃` | crown drooping, eyes shut, slow arms, dim specks | dim |

```
size 4, working                    size 4, happy
⠄⡀⠈⠐ ╱ ╲ ⠂⠁⢀⠠                     ▀▀▀▀ ╱ ╲ ▀▀▀▀
▂▁▂ ●   ● ▂▁▂                      ▂▁▂ ●   ● ▂▁▂
     ───                                ╲ ╱
  █████▌▁▁▁                          █████▌▁▁▁
```

A `ci` of `fail` turns the body coral whatever the mood is, and a dirty
tree turns the crown amber. Both are colour only, so the fallback keeps
its shape and only the roles move, which is what colour roles are for.

## The three sizes

Every size is thirteen cells wide, so a caller can change size without
relaying out the line around the creature. `SP` is one ordinary space,
`PAD` is ordinary spaces.

```
size 1   ARM(3) SP HEAD(5) SP ARM(3)          ▂▁▂ ● ─ ● ▂▁▂
size 2   the same row                         ▂▁▂ ● ─ ● ▂▁▂
         PAD(2) BELLY(9) PAD(2)                 █████▌▁▁▁

size 4   AURA(4) CROWN(5) AURA(4)             ▀▀▀▀ ╱ ╲ ▀▀▀▀
         ARM(3) SP EYES(5) SP ARM(3)          ▂▁▂ ●   ● ▂▁▂
         PAD(4) MOUTH(5) PAD(4)                    ╲ ╱
         PAD(2) BELLY(9) PAD(2)                 █████▌▁▁▁
```

Size 1 is the line beside a prompt or on a live status line, size 2 adds
the reading that made the mood, size 4 is the one you look at. Thirteen
cells plus the two-cell recipe margin and a label still fits a forty
column terminal, which is the width the creature has to survive.

`frame(tick)` is always that one row, at any size, so a caller with one
line to spend never has to know which size it asked for.

## Signals

All optional, all with a default that means "nothing was measured", so
`Creature().frame(0)` draws.

| Signal | Range | Reads as |
|---|---|---|
| `cpu` | 0..1 | the belly's fill, and how far the arms swing |
| `rate` | >= 0 | how fast the arms swing |
| `latency` | >= 0 | how far the arms hang |
| `ci` | `"pass"`, `"fail"`, `None` | a failure turns the body coral |
| `dirty` | bool | an uncommitted tree turns the crown amber |

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
3. **One row per span.** A limb never emits a multi-line span, so the
   multi-row bbox rules never come into it and the caller owns the layout.
4. **Sparks are pinned to lo 0 and hi 1.** A spark left to scale itself
   rescales a calm arm into a wild one, because lo and hi come from the
   data. A still creature has to look still.
5. **Frames are pure.** `(mood, signals, size, tick)` decides every byte:
   no clock, no randomness, no environment. A caller that wants motion
   counts ticks itself, and a test can assert a frame.
6. **Every frame of a size is the same width.** `Creature.width()` is
   thirteen at every size and for every mood, tick and signal, so a caller
   redraws in place without clearing the line.
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
