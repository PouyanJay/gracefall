# gracefall: fallback-first terminal graphics (OSC 4700, draft 1)

One sentence: progressive enhancement for the terminal byte stream. An
application wraps generated fallback text in an OSC envelope that carries the
underlying data; terminals that don't implement the protocol silently consume
the envelope and display the fallback, while terminals that do implement it
re-render the same cells as theme-aware vector graphics. The fallback is
simultaneously the degradation path, the size contract, the copy/grep
surface, and the accessibility layer.

The specification text in this file is released under CC0. Copy it into any
implementation without attribution.

## The four laws (derived from 40 years of prior failures)

1. Live inside the existing byte stream. ReGIS, NeWS, TermKit, and Arcan all
   replaced it, and all died regardless of technical merit.
2. Zero coordination. Emitting must be safe blind: over SSH, into a pipe,
   into a recording, with no capability query and no round trip.
3. Degrade at the receiver, not the sender. Sender-side degradation (probe
   the terminal, then choose an output) fails for recorded, piped, replayed,
   or multiplexed output. Unknown OSC sequences are silently consumed by
   every xterm descendant, so the fallback text is what a non-implementing
   terminal displays. Nothing ever breaks.
4. Preserve text semantics. Selection copies the fallback. Grep matches it.
   Screen readers speak it. Scrollback and tmux replay it, because it is
   ordinary cells.

## Wire format

Framing is identical to OSC 8 hyperlinks, the only graphics-adjacent
protocol that ever reached near-universal adoption:

    ESC ] 4700 ; key=value ; key=value ST   fallback text   ESC ] 4700 ; ST

ST is ESC \ (BEL accepted). An envelope with parameters opens a span; an
empty envelope closes it. The span's cells are wherever the fallback text
lands, which means:

- the application does layout in cells, exactly as today
- the enhanced rendering is confined to those cells, so there is no size
  negotiation and no query round trip
- resize and reflow behave exactly like text, because it is text

Fallback text may contain newlines. Receivers compute the drawing rectangle
from the span's non-space cells, so indentation whitespace inside a
multi-line span never distorts the box.

Receivers MUST ignore keys they do not recognize, and MUST fall back to
displaying the span's text unchanged for any type they do not implement.
Emitters MUST keep each envelope under 2048 bytes.

## v1 types (deliberately small, all declarative)

    t=spark    d=1,4,2,8 ; lo=0 ; hi=10 ; style=line|area ; c=<role>
    t=meter    v=0.62 ; w=24 ; c=<role>
    t=dist     b=<bin counts> ; lo ; hi ; c=<role>
    t=flow     n=build:done,test:done,canary:active,prod:pending
    t=scatter  d=x:y,x:y,... ; xlo ; xhi ; ylo ; yhi ; m=<slope> ;
               tb=<intercept> ; c=<role>
    t=heat     d=row:row:... (rows are comma-separated) ; lo ; hi ; c=<role>

Payloads are data, never pixels and never drawing commands. The receiving
terminal owns rendering, so output adapts to its theme, font size, and DPI.

Color is by role, not value: fg, dim, teal, blue, amber, coral, violet.
Roles resolve against the terminal's theme, which is what makes one byte
stream correct on both dark and light backgrounds. Raw #rrggbb is permitted
but discouraged.

An arbitrary-path type is deliberately excluded. The moment payloads become
drawing commands instead of data, the fallback can no longer be mechanically
derived from the same source and law 4 dies. That line is the integrity of
the protocol.

## Emitter requirements

- The fallback MUST be generated from the same data by the library, never
  hand-written. A fallback that can drift from the data is a lying UI.
- The fallback MUST be a genuine visualization (unicode blocks, braille,
  eighth blocks), not a placeholder like [chart]. The degraded experience is
  a first-class experience.
- Emitters MUST suppress envelopes when stdout is not a tty, the same
  isatty() convention that governs color today, unless explicitly forced.
- Derived values shipped in the envelope (for example a trend line) MUST be
  computed from the same data that produced the fallback.

## Interactivity (v2 sketch, out of scope)

Spans may carry id=. A terminal MAY report a click on a span as a CSI
reply mirroring SGR mouse reporting. Nothing in v1 depends on this.

## Envelope number and registration

There is no official OSC registry; numbers are first-come, documented in
each terminal's control-sequence list. 4700 was chosen after checking the
documented lists of xterm, iTerm2 (1337), kitty, WezTerm, VS Code (633),
rxvt (777), and mintty (7721, 7770, 7771, and private modes in the 77xx
range, which is why the earlier draft number 7700 was abandoned). Staking
the number means documenting it publicly where terminal authors look:
a vt-extensions style document and a terminal-wg thread.

## Prior art and the precise delta

- kitty Unicode placeholders (U+10EEEE): graphics anchored to real text
  cells that reflow with text. Solves anchoring, but the placeholder is
  meaningless on other terminals; the degraded view is blank or tofu.
- Contour's Good Image Protocol and terminal-wg issue 26: the community's
  formal attempt at a better image protocol. Raster, requires support to
  show anything; its accessibility thread proposed alt text as invisible
  metadata, never as the visible body.
- notcurses and friends: degrade at the sender by probing the terminal,
  which fails in pipes, recordings, and multiplexers.
- Jupyter MIME bundles: multi-representation payloads with a text/plain
  fallback, but in a JSON message protocol, not a terminal byte stream.

No shipped protocol or public proposal we could find uses generated visible
fallback text as the wire format's body with machine-readable data in the
envelope, receiver-side, zero round trip. That inversion is the contribution.

## Adoption path

The OSC 8 playbook: land the emitter in one library thousands of CLIs
already use (Rich, or a ratatui crate), land the renderer in one
fast-moving terminal where a single maintainer can say yes (Ghostty,
WezTerm), ship one daily-visible demo, then write it up for terminal-wg.
Emitting is safe today because degradation is free; that is the entire
point of the design.

## Appendix A: rendering a span (non-normative)

Nothing in this appendix is required. It records what the reference
implementation does, so a terminal author has answers to the questions that
come up first. The normative rules are above.

**The drawing rectangle.** Compute it from the span's non-space cells, not
from every cell the span covers. A multi-line span carries its indentation
as spaces, and including them would widen the box and shear the drawing.

**Confinement.** Draw only inside that rectangle. A receiver painting into
the span's cell rect necessarily clips anything outside it, so geometry
that overflows is geometry no conforming terminal can show. The reference
renderer got this wrong at first: a spark's end-of-line marker centered on
the last data point sat exactly on the right edge, and half of it was
undrawable. The fix belongs in the geometry, holding the marker inside by
its own radius, not in the receiver.

**Coordinates.** The reference geometry is authored in an abstract cell of
12 x 24 units and scaled to real cell metrics at draw time. Insets and bar
heights are absolute in that space, so recomputing geometry directly in
device pixels distorts them at other cell sizes.

**Colour roles.** Resolve `fg dim teal blue amber coral violet` against the
active theme. Two further colours are needed that only a receiver knows:
the surface behind the span, and the unfilled groove of a meter. Derive the
groove from the background rather than hardcoding it, or it will be wrong
on a light theme.

**Unknown types and unknown keys.** Both are already covered normatively:
ignore unknown keys, and display the fallback text unchanged for unknown
types. Implemented as "produce no drawing primitives", this falls out for
free, because a receiver with nothing to draw leaves the text alone.

**Deriving the fallback is not the receiver's job.** The fallback arrives as
ordinary cells. A receiver never generates it and never needs to parse it.

## Appendix B: multiplexers and passthrough (non-normative)

tmux and screen consume APC and OSC sequences they do not recognise. For
gracefall this is harmless in the default case: the envelope is dropped, the
fallback text is ordinary cells, and the user sees the degraded view, which
is the whole design.

It stops being harmless for a *shim* that blanks the span's cells before
painting over them, because the blanking survives and the graphics do not.
Anything doing that should detect the multiplexer and decline, rather than
leave empty cells where a chart was. For tmux, passthrough is off by
default and enabled with:

    tmux set -g allow-passthrough on

A terminal implementing OSC 4700 natively has no such problem: it receives
the envelope directly and never blanks anything it cannot then draw.
