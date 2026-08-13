# Draft: issue for Textualize/rich

Not posted. This is a draft for review, per the adoption path in SPEC.md.
Post it yourself, or say so and it can be posted for you.

Target: <https://github.com/Textualize/rich/issues/new>

Title:

    Proposal: optional OSC 4700 envelopes so Rich's charts can render as
    vector graphics in capable terminals

---

Body:

Rich already draws bar charts, progress bars, and sparkline-ish output as
unicode blocks. I would like to propose an optional addition: wrap that
generated text in an OSC 4700 envelope carrying the data behind it, so a
terminal that understands the protocol can re-render the same cells as
smooth vector graphics, while every other terminal shows exactly what Rich
shows today.

The wire format is one line:

    ESC ] 4700 ; t=spark ; d=1,4,2,8 ; c=blue  ST   ▁▄▂█   ESC ] 4700 ; ST

An open envelope carrying data, the visible fallback text, then an empty
envelope to close. Terminals that do not know OSC 4700 silently consume
both envelopes, which every xterm descendant already does for unknown OSC,
and print `▁▄▂█`.

**Why this might interest Rich specifically**

- **Nothing regresses.** The fallback is the text Rich would have printed
  anyway. Selection, grep, scrollback, tmux replay, and screen readers keep
  working because they are ordinary cells.
- **No capability detection.** No query, no round trip, no probing. Emitting
  is safe blind: over SSH, into a pipe, into a recording, inside a
  multiplexer. There is no branch on terminal type to maintain, and no
  `isatty` check beyond the one Rich already does for colour.
- **The payload is data, not pixels.** `t=meter;v=0.62;c=teal`, not drawing
  commands. The terminal owns rendering, so output adapts to its theme,
  font size, and DPI. Colours are roles (`fg dim teal blue amber coral
  violet`) resolved against the user's theme, which is what makes one byte
  stream correct on both light and dark backgrounds.
- **It is small.** Six declarative types, envelopes capped at 2048 bytes.

**Current status, stated honestly**

No terminal implements OSC 4700 natively yet. What exists today:

- A reference implementation and spec: <https://github.com/PouyanJay/gracefall>
  (spec text is CC0, code MIT)
- `gfl view`, a shim that renders the graphics *today* in Ghostty, kitty,
  and WezTerm by translating spans into the kitty graphics protocol. This
  is the immediate consumer: anything emitting these envelopes is already
  visibly better in those terminals, without waiting for anyone to
  implement the protocol.

So this is not "adopt a protocol and wait". It has a working renderer now,
and native support later is a strict improvement on top.

**What adoption in Rich could look like**

Smallest possible version: an opt-in flag or a `Console` option, off by
default, that wraps existing chart-ish renderables in envelopes. No new
public API, no change to default output, and the fallback generated from
the same data by the same code so the two cannot disagree.

I am happy to do the implementation work and to adjust the spec if the type
set does not fit Rich's renderables. Mostly I want to know whether the idea
is interesting to you before writing a PR, and what would need to be true
for it to be mergeable.

Spec, including the four design laws and the prior-art delta:
<https://github.com/PouyanJay/gracefall/blob/main/SPEC.md>
