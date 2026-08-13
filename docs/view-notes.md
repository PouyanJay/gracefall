# `gfl view` implementation notes

Findings from building the kitty-graphics shim (Phase 1). What is settled,
what is still open, and what only a human in front of a real terminal can
answer.

## Verification without a terminal

Screen capture is not available in the environment this was built in, so
placement was verified with [scripts/kitty_sim.py](../scripts/kitty_sim.py),
which plays the role of a kitty-graphics terminal: it consumes exactly the
bytes `gfl view` writes, applies the cursor movements, reassembles the
chunked base64, and composites the result to a PNG.

This is the same trick `render.py` already uses for the OSC 4700 side, and
it is a real oracle rather than a mock: it decodes the actual output, and it
raises on any escape sequence it was not expecting rather than skipping it,
because silently ignoring a sequence would hide the exact class of bug it
exists to catch.

    make view-sim                       # over placement, 10x20 cells
    make view-sim ARGS="--cell 12x24"   # match the SVG renderer's grid

Checked mechanically:

- All seven demo spans land on the rows and columns the reference renderer
  computes for them (2, 3, 5, 6, 9, 14, 17, all at column 9).
- Chunk boundaries reassemble into valid PNGs, including the 26 KB heat grid
  that needs nine chunks.
- The cursor returns to where it started after every image.

Two bugs came out of looking at the composite, neither of which the unit
tests caught:

1. **Every gradient was invisible.** `Image.getchannel("A")` returns a copy,
   so writing alpha through it changed nothing. Both meters rendered as
   empty grooves and the spark lost its area fill. The test that should have
   caught it counted opaque pixels, which the meter's track supplies on its
   own. Now covered by `test_gradient_paints_are_actually_opaque` and
   `test_meter_fill_scales_with_its_value`, both confirmed to fail when the
   bug is reintroduced.

2. **The reference SVG renderer was violating SPEC.md.** The spark's end dot
   is centered on the box's right edge and spills 3.4px past it. SPEC.md
   says the enhanced rendering "is confined to those cells", and the shim,
   which paints an image sized exactly to the span's cells, necessarily
   clips it. The renderer did not. It now clips each span to its own box,
   which moved 101 pixels of the reference output and made the two backends
   agree.

That second one is the argument for the shared geometry core landing before
the second backend: the disagreement was visible only because both were
drawing from the same shape list.

## Spike: placement variant A (over) versus B (under)

Both are implemented, `--placement over|under`.

**A, over (the default, and the one to keep).** Span cells are printed as
spaces and the image is placed on top. Clean, and the only artifact is that
the region is genuinely blank if the image fails to arrive.

**B, under (`z=-1`).** The fallback text is printed intact and the image is
placed beneath it. The result is a collision: the block glyphs are opaque
and sit on top of the graphics, so the flow labels double up, the heat grid
is muddied by its own half-block characters, and the meter bars show the
eighth-block fallback over the smooth fill. See `build/sim-under.png` after
`make view-sim ARGS="--placement under"`.

B would only make sense if the fallback glyphs were the intended foreground
and the graphics a subtle wash behind them, which is not what this protocol
is for. Keeping A.

**Still needs a human.** The simulator composites the same way a terminal
does (image, then text on top for `z=-1`), so the conclusion should hold,
but the z-index semantics of `z=-1` differ subtly between kitty and Ghostty
and only a real terminal settles it.

## Spike: Unicode placeholder mode

Not implemented, deliberately, per the plan. Notes for when it is:

Placeholder mode (`U=1` plus cells of U+10EEEE carrying the image id in
their colour attributes) makes the image a property of the text grid rather
than of the screen, which is what would let it survive tmux, scrollback, and
reflow. That is the mode a shipping version wants.

The reason to defer: it requires the image id to be encoded in the
placeholder cells' foreground colour, which fights with the fallback text
occupying those same cells, and it needs a per-terminal check of how many
diacritic rows are supported. It is a bigger piece of work than the rest of
the shim combined, and it is not needed for the screenshot this phase is
for.

## Open questions for real-terminal verification

The acceptance check is visual and cannot be automated. In Ghostty and in
kitty:

1. `gfl demo --force-osc | gfl view` at two different font sizes. The images
   are sized from the terminal's reported cell metrics, so a font size change
   is the direct test of whether those metrics are being read correctly.
2. Confirm the images are crisp rather than resampled. Blurry output means
   the cell metrics are wrong, and `--stats` reports where the numbers came
   from (`ioctl`, `CSI 16 t`, or `default`).
3. Scroll the output off screen and back. Snapshot mode places images at
   absolute screen positions, so scrollback behaviour is expected to be
   imperfect here; this is what placeholder mode would fix.
4. Inside tmux without `allow-passthrough on`, confirm the fallback text
   appears rather than nothing. tmux swallows APC sequences, and the shim
   currently has no way to detect that it is being swallowed.

## Known limitations

- **tmux.** APC passthrough is off by default. The shim cannot tell that its
  images are being eaten, so it prints blanked cells with no graphics over
  them. `allow-passthrough on` fixes it. Detecting `$TMUX` and warning is
  the obvious next step.
- **Scrollback.** Images are placed at screen positions, not anchored to
  text. Phase 2's watch mode deletes and repaints, which sidesteps it for
  live output but not for scrollback.
- **Background colour.** Queried with OSC 11 so the spark's end dot and the
  meter's groove match the theme. Terminals that do not answer get a dark
  default, which is wrong on a light theme. `track` is mixed from the
  background rather than hardcoded, so it follows automatically once the
  query succeeds.
