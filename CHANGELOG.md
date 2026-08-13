# Changelog

## 0.2.3

### Fixed

- `gfl view --watch` now exports `COLUMNS` and `LINES` to the watched
  command. The child's stdout is a pipe, so it cannot measure the terminal
  itself, and a dashboard laid out for a default 80 columns wrapped every
  line on a narrower window.
- `examples/sysmon.sh` sizes its bars to the terminal and keeps each value
  on the same line as its chart. Every gracefall command ends with a
  newline, so the value had been landing on the line below.

## 0.2.2

Both found by running it in Terminal.app, which does not swallow APC
sequences the way the graphics-capable terminals do.

### Fixed

- The capability probe printed `Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA` onto the
  screen. Terminal.app displays the contents of an APC sequence instead of
  consuming it, so probing corrupted the display of exactly the terminals
  the probe exists to rule out. It now saves the cursor, probes, then
  restores and erases forward, which cleans up any leak and is a no-op on a
  terminal that behaved.
- `gfl view --watch` did nothing useful without graphics support: it printed
  the message and exited. It now runs the loop in text mode, repainting the
  fallback in place, because a live text dashboard is still a live dashboard
  and `--watch` should not be the one feature that needs a special terminal.
  The text path emits no APC at all, including the image delete.

## 0.2.1

Found by using the tool on real data rather than the demo.

### Fixed

- Empty or non-numeric input raised a traceback. A pipeline that produces
  nothing is normal (a grep that misses, a log with no lines yet), so
  `spark` and `dist` now exit 1 with "no data: stdin was empty" or
  "not a number: 'x'".
- `gfl view --watch` repainted text with no graphics on it. The watched
  command's stdout is a pipe, so its own isatty check stripped the very
  envelopes it was being asked to produce. The watch loop now sets
  `GRACEFALL_FORCE_OSC=1` for the child, and the emitter honours it.
  `--no-osc` still wins.

### Added

- `examples/sysmon.sh`, a real dashboard of disk, memory, battery, load, and
  process CPU, built entirely out of gracefall and readable in any terminal.
- A recipes section in the README.

## 0.2.0

The release that makes the smooth rendering real. `gfl view` paints spans as
graphics in terminals that already speak the kitty graphics protocol, and
the geometry behind it is now shared, so the two renderings cannot drift.

Nothing about the wire format changed. A 0.1 stream renders identically.

### Added

- `gfl view`: paints spans as graphics over their own cells using the kitty
  graphics protocol, verified in Ghostty 1.3.1 and kitty 0.48.2. Falls back
  to printing the stream untouched, with a reason on stderr, in a terminal
  without graphics support. Behind the optional `view` extra:
  `pip install "gracefall[view]"`.
- `gfl view --watch CMD`: re-runs a command on an interval and repaints in
  place, inside synchronized output, deleting the previous frame's images
  each cycle so they cannot accumulate.
- `gracefall render --png`: composes a whole stream, text and spans, to a
  PNG without needing a terminal. `docs/compare.png` is now generated from
  this rather than by hand.
- `src/gracefall/shapes.py`: the shared geometry core. One
  `shapes_for(attrs, box)` feeds the SVG renderer, the terminal viewer, and
  anything added later.
- `src/gracefall/raster.py`: the Pillow backend, including block elements
  drawn as exact cell fractions rather than font glyphs.
- `SPEC.md` appendices on rendering a span and on multiplexer passthrough,
  both non-normative.
- A developer task runner (`make help`), including `make visual-diff`, which
  rasterizes the rendering at two git refs and compares actual pixels.

### Fixed

- `--force-osc` and `--no-osc` are accepted after the subcommand, not only
  before it. The README's own `gracefall demo --force-osc` exited 2 and,
  when redirected, left an empty file. (0.1.1)
- `gracefall --help` crashed with `ValueError: incomplete format`, because
  argparse `%`-expands help text and meter's "0..1 or N%" is an incomplete
  format specifier. (0.1.1)
- Span rendering is now clipped to the span's own cells, as SPEC.md
  requires. The spark's end marker was centered on the box edge, so half of
  it was undrawable by any conforming receiver; it is now held inside by
  its own radius.
- Inside tmux without `allow-passthrough on`, `gfl view` now prints the
  fallback and says why. It previously blanked the span's cells and then
  lost the images tmux had swallowed, leaving empty space where a chart
  should be.
- Placeholder URLs (`CHANGEME`, `<user>`) in the packaging metadata and the
  README. (0.1.1)

### Changed

- CI runs `make test`, the same target used locally, and now covers Python
  3.9, the floor declared in `requires-python`.

## 0.1.1

- `--force-osc` and `--no-osc` accepted on both sides of the subcommand.
- `gracefall --help` no longer raises.
- Placeholder URLs fixed in packaging metadata and the README.

## 0.1.0

First release. Emitter, CLI, and the SVG reference renderer, with six span
types: spark, meter, dist, flow, scatter, heat.
