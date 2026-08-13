# Implementing OSC 4700 in Ghostty: source study

Findings from reading Ghostty 1.3.1 (commit `332b2ae`). This is the Phase 3
study task: where the code that matters lives, what the MVP has to touch,
and which of the plan's assumptions survived contact with the source.

No Ghostty code has been written and nothing has been posted publicly.

## The short version

The plan guessed that span membership should mirror OSC 8 hyperlinks:
"cells carry a small id, a side table maps id to data". That is almost
exactly right, and better than expected. Hyperlink membership is a **single
bit on the cell plus an offset-keyed map**, and both `Cell` and `Row` are
`packed struct(u64)` with explicit spare padding. Adding gracefall spans
costs one bit of each and does not change the size or layout of either.

Reflow really is free, and not by luck: the map is keyed by cell offset and
`PageList` already re-applies hyperlink ids at every reflow site, so
mirroring the mechanism inherits that behaviour rather than reimplementing
it.

## Where things live

| Path | What it is |
| --- | --- |
| `src/terminal/osc.zig` | OSC parser. The `Command` union, its `Key` enum, the digit state machine, and the terminator dispatch |
| `src/terminal/osc/parsers/` | One module per OSC family, e.g. `hyperlink.zig` |
| `src/terminal/page.zig` | `Cell` and `Row` packed structs, `hyperlink_map`, `hyperlink_set`, `setHyperlink` |
| `src/terminal/hyperlink.zig` | `Map` and the ref-counted `Set` |
| `src/terminal/Screen.zig` | `startHyperlink` / `endHyperlink`, cursor state, `cursorSetHyperlink` |
| `src/terminal/Terminal.zig` | The cell write path that applies the cursor's hyperlink, around line 827 |
| `src/terminal/PageList.zig` | Reflow, which re-applies hyperlink ids |
| `src/terminal/kitty/graphics_storage.zig` | `ImageStorage`, `addPlacement`, `Placement` |

## Parsing OSC 4700

The parser is a hand-written, character-at-a-time state machine, not a
number parse. Each digit moves to a state named for the digits seen so far:

    '3' => .@"3"  ->  '0' => .@"30"  ->  '0' => .@"300"  ->  '8' => .@"3008"

At the terminator, a `switch` maps the final state to a parser:

    .@"8" => parsers.hyperlink.parse(self, terminator_ch),

So OSC 4700 needs:

1. States `.@"47"`, `.@"470"`, `.@"4700"`. Note `.@"4"` already exists,
   because OSC 4 is the colour operation, so this is a new branch off an
   existing state rather than a new digit root.
2. A `parsers/gracefall.zig` that parses the attribute string.
3. Two variants on the `Command` union, `gracefall_start` and
   `gracefall_end`, mirroring `hyperlink_start` / `hyperlink_end`.
4. Two entries in `Command.Key`. **Order matters** there: the enum is built
   through `LibEnum` for a stable C ABI, so new names go at the end.

Cheap safety property: an unrecognized OSC state falls to `.invalid` and
the sequence is consumed silently, which is the behaviour SPEC.md already
depends on. Nothing has to be added for the degradation path to work; it is
what already happens today.

The 2048 byte cap is enforced by discarding oversized envelopes at parse
time, which matches SPEC.md's emitter requirement from the receiving side.

## Storing span membership

This is the part worth getting right, and the existing mechanism is a good
fit.

`Cell` is a `packed struct(u64)`:

    content_tag: u2
    content:     u24 union (codepoint u21 | palette u8 | rgb 24)
    style_id:    u16
    wide:        u2
    protected:   bool
    hyperlink:   bool
    semantic_content: u2
    _padding:    u16          <- 16 spare bits

`Row` is also `packed struct(u64)`, with flags `wrap`, `wrap_continuation`,
`grapheme`, `styled`, `hyperlink`, `kitty_virtual_placeholder`, `dirty`,
and `_padding: u23`.

So the MVP adds:

- `Cell.gracefall: bool`, one of the 16 spare cell bits.
- `Row.gracefall: bool`, one of the 23 spare row bits. This is purely an
  optimization, and it is the established pattern: `hyperlink` and
  `kitty_virtual_placeholder` both exist at row level so the renderer can
  skip a row without walking its cells.
- `Page.gracefall_map` and `Page.gracefall_set`, mirroring
  `hyperlink_map: AutoOffsetHashMap(Offset(Cell), Id)` and the ref-counted
  `hyperlink_set`.
- `Screen.cursor.gracefall_id`, mirroring `cursor.hyperlink_id`.

Neither struct grows, so page layout, capacity maths, and memory accounting
are untouched. That is the single most important finding here: it turns
"add a parallel concept to the cell grid" from an invasive change into an
additive one.

### The write path

`Terminal.zig` around line 827, immediately after the kitty placeholder
check:

```zig
if (self.screens.active.cursor.hyperlink_id > 0) {
    self.screens.active.cursorSetHyperlink() catch |err| { ... };
} else if (had_hyperlink) {
    // clear it from the cell
}
```

The gracefall version is the same shape: if a span is open, tag the cell as
it is written; if one just closed, clear the flag. `setHyperlink` already
handles the awkward parts (reallocating when the page's map runs out of
capacity, and the fast path when overwriting a cell with the same id), so
the mirror should copy its structure rather than invent one.

### Reflow, verified rather than assumed

`PageList.zig` calls `setHyperlink` at several points during reflow and
page splitting. Because membership is keyed by cell offset and re-applied
when cells move, a mirrored `setGracefall` inherits the same behaviour.
SPEC.md's rule that the drawing rectangle is the span's non-space cells
then holds after resize with no extra work, because the rectangle is
recomputed at draw time from cells that carried their membership along.

## Drawing, without new GPU work

The MVP plan was to reuse the kitty graphics pipeline rather than write a
shader, and the API supports it directly:

```zig
pub fn addPlacement(
    self: *ImageStorage,
    alloc: Allocator,
    image_id: u32,
    placement_id: u32,
    p: Placement,
) !void
```

`Placement` carries `columns` and `rows` and already knows how to compute
its pixel size from the terminal's cell metrics, including the case where
both are specified. That is exactly the shape `gfl view` is already using
over the wire (`a=T,c=<cols>,r=<rows>`), so the internal path and the shim
path agree by construction.

Per frame, for each visible span: compute the cell bbox from tagged
non-space cells, rasterize into an RGBA buffer, hand it to `ImageStorage`
at that cell rect, and suppress the span's glyphs.

The honest caveat: rasterizing on every frame is wasteful, and a real
implementation wants a cache keyed by (attrs, cell box, theme). The MVP
does not need it and should not grow it before the approach is agreed.

## Feature gating

Ghostty already gates kitty graphics at compile time:

```zig
if (comptime build_options.kitty_graphics) { ... }
```

The fork should use the same mechanism plus the runtime config flag the
plan calls for, defaulting off until a name is settled upstream. Worth
copying the comment style at `Row.kitty_virtual_placeholder`, which
explains that the bit is kept even when the feature is compiled out, so the
struct layout does not depend on build options.

## Porting the geometry

The MVP is `meter` and `spark` only. In shapes.py those are `_meter` and
`_spark`, about 30 lines together, and both are pure arithmetic over floats
with no dependencies. The Zig port is mechanical.

Two things must come with them or the port is wrong:

- The abstract 12 x 24 cell coordinate space. The geometry constants are
  absolute (`ins = 3`, `y + 7`, `h - 14`), so the Zig side must scale from
  abstract units to device pixels exactly as `raster.py` does, rather than
  computing shapes directly in device pixels.
- The end-dot clamp. The spark's marker is held inside the box by its own
  outer radius, because a span may not paint outside its cells. A port that
  drops the clamp will draw a half-dot that the terminal then clips.

`tests/golden/inference_shapes.txt` is the cross-check: a Zig port that
produces the same numbers for the same attrs is correct, and that is a
better acceptance test than looking at it.

## Theme roles

SPEC.md's roles are `fg dim teal blue amber coral violet`, plus the two
pseudo-roles `raster.py` needs (`bg`, and `track` mixed from the
background). Ghostty exposes its palette and default foreground and
background; the mapping should resolve roles against the active theme with
the SPEC.md RGB values as the fallback, which is what makes one stream
correct on both light and dark backgrounds.

`track` should stay derived rather than hardcoded, for the same reason it
is derived in `raster.py`: a fixed slate is wrong on a light theme.

## What the MVP touches

1. `src/terminal/osc.zig`: three states, two `Command` variants, two `Key`
   entries, one dispatch arm.
2. `src/terminal/osc/parsers/gracefall.zig`: new.
3. `src/terminal/page.zig`: one cell bit, one row bit, map and set, and a
   `setGracefall` mirroring `setHyperlink`.
4. `src/terminal/Screen.zig`: cursor id, start and end.
5. `src/terminal/Terminal.zig`: the write path arm.
6. Renderer: rasterize tagged spans and place them through `ImageStorage`.
7. Tests in Ghostty's terminal unit test harness: open, close, cap
   enforcement, unknown type passthrough, and reflow retention.

## Open questions for upstream

Worth asking before writing renderer code, because they change the shape of
the patch:

- Is a second cell bit acceptable, or would maintainers prefer spans reuse
  the hyperlink machinery with a tagged id space? The bit is free today,
  but it is their budget to spend.
- Should the payload be parsed at OSC time into a typed struct, or stored
  raw and parsed at draw time? Raw is simpler and keeps the parser cheap;
  typed catches malformed envelopes earlier.
- Is reusing the kitty image pipeline acceptable for an MVP, given it means
  gracefall spans appear in image storage and interact with image deletion?

## Status

Study complete. Nothing has been posted to the Ghostty repo and no fork has
been created; both need explicit sign-off because they happen in the
maintainer's own name.
