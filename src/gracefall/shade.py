"""gracefall.shade: a shaded figure as a grid of densities.

The third way this project has to draw. `lanes` gives one glyph a cell,
`scatter` gives eight braille dots a cell, and neither has any notion of
*tone*: a dot is on or off. This module renders an implicit surface to a
grid of 0..1 densities and hands it to `heat`, whose `ramp` fallback picks
a character per cell out of ten levels of ink. That is the oldest trick in
terminal graphics and it is what an ASCII render of a 3D model does.

It needs room. A shaded figure is made of gradients, and a gradient across
twenty cells is four characters wide: below roughly fifty columns the tone
turns to mush and the outline techniques win. `creature.py` picks between
the three on exactly that basis.

Nothing here is a picture in the protocol's sense. The payload is the grid
of numbers, the characters are generated from it by `heat`, and a receiver
that draws the span draws the same numbers as coloured cells. There are no
pixels and no drawing commands on the wire.

The model is implicit, not an asset: an ellipsoid head with Lambert
shading, two triangles for ears, and cut-outs for the face. Pure stdlib
maths and a pure function of `(u, v, tick, mood)`, so a frame is
reproducible and can be golden-tested, which an image pipeline could not
promise.
"""

import math

from . import MAX_ATTRS, heat

__all__ = ["field", "render", "rows", "sub_for", "COLS_MIN", "SUB_MAX"]

#: The most a cell is ever subdivided. Past this the envelope is mostly
#: payload for detail no terminal can show: four sub-cells across a cell
#: is already finer than the glyph grid the fallback prints on, and the
#: drawn view is bounded by the pixels in a cell.
SUB_MAX = 4

#: Bytes a value costs in `d=`, near enough: "0.62," is five.
_PER_VALUE = 5

#: Below this many columns a shaded figure is not worth rendering: the
#: tone has nowhere to run and the outline techniques read better. It is
#: a measured floor, not a guess: at thirty columns the face still has
#: eyes, a nose and a mouth, and at twenty it is a smudge.
COLS_MIN = 28

#: Where the figure's ink actually falls inside the unit box it is drawn
#: in, with a little margin for the sway. Measured rather than guessed:
#: the geometry constants leave the cat occupying about six tenths of its
#: box, so a caller fitting the box to a frame would fit mostly emptiness
#: and the cat would sit small in the middle of it. `field` remaps the
#: caller's 0..1 onto this, so the frame it is given is the frame it fills.
_FIT_U = (0.190, 0.835)
_FIT_V = (0.135, 0.815)

#: The figure's own proportions, width over height, in square units, and
#: it is taller than it is wide. A terminal cell is about twice as tall as
#: it is wide, so a grid of `cols` by `nrows` cells is `cols` by
#: `2 * nrows` square units: sampling the model straight onto the cell
#: grid would stretch it to twice its height.
MODEL_ASPECT = (_FIT_U[1] - _FIT_U[0]) / (_FIT_V[1] - _FIT_V[0])

#: How tall a cell is against its width. Every terminal is close enough to
#: this that a figure fitted with it looks right in all of them, and the
#: alternative is asking, which the protocol forbids.
CELL_RATIO = 2.0

#: The floor under the body's ink. Shading that ran the whole range would
#: take the silhouette apart, because the lit half of the head would fall
#: to a character you cannot see. Tone varies above this, never below.
_INK_FLOOR = 0.40

#: Direction the light comes from, in the same space as the model.
_LIGHT = (-0.55, -0.62, 0.56)


def _sat(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _dome(x, y, rx, ry):
    """Height of a unit ellipsoid above the xy plane, or None outside."""
    q = (x / rx) ** 2 + (y / ry) ** 2
    if q >= 1.0:
        return None
    return math.sqrt(1.0 - q)


def _lit(x, y, rx, ry):
    """Lambert shading of that ellipsoid plus a rim, 0..1, or 0 outside."""
    z = _dome(x, y, rx, ry)
    if z is None:
        return 0.0
    nx, ny, nz = x / (rx * rx), y / (ry * ry), z
    n = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    lx, ly, lz = _LIGHT
    lam = _sat((nx * lx + ny * ly + nz * lz) / n)
    return _sat(0.18 + 0.82 * lam + (1.0 - z) ** 3 * 0.42)


def _band(dist, half):
    """Coverage of a soft band `half` wide, 1 at the centre, 0 past it.

    A hard test is the wrong tool at this resolution. A feature one row
    thick either lands on a sample or does not, so a mouth appears at one
    size, breaks into dashes at another and vanishes at a third. A band
    that falls off returns a partial value instead, and a partial value is
    a lighter character rather than no character: the ramp antialiases it.
    That is the one thing tone can do that an outline cannot.
    """
    if half <= 0.0:
        return 0.0
    return _sat(1.0 - abs(dist) / half)


def _tri(px, py, half, height):
    """Inside a triangle with its base at py=0 and its apex at -height.
    Returns 1 along the spine falling to 0 at the edges, 0 outside."""
    if py > 0.0 or py < -height:
        return 0.0
    w = half * (1.0 + py / height)
    if w <= 0.0 or abs(px) > w:
        return 0.0
    return 1.0 - abs(px) / w


def field(u, v, tick=0.0, mood="idle", signals=None):
    """Ink at normalized `(u, v)`, both 0..1 with v downwards.

    Ink, not light: the ramp runs from a space to `@`, so the body is
    dense and the background is empty.
    """
    sig = signals or {}
    u = _FIT_U[0] + u * (_FIT_U[1] - _FIT_U[0])
    v = _FIT_V[0] + v * (_FIT_V[1] - _FIT_V[0])
    x, y = (u - 0.5) * 2.0, (v - 0.5) * 2.0
    breathe = 1.0 + 0.035 * math.sin(tick * 0.9)
    x -= 0.045 * math.sin(tick * 0.55)
    cy = 0.12
    rx, ry = 0.60, 0.46 * breathe

    d = 0.0
    for sx in (-1.0, 1.0):                              # ears, behind
        g = _tri(x - sx * 0.36, y - (cy - ry * 0.62), 0.20, 0.52)
        if g > 0.0:
            d = max(d, _INK_FLOOR + 0.02 + 0.50 * g)
    lit = _lit(x, y - cy, rx, ry)                       # head, in front
    if lit > 0.0:
        d = _INK_FLOOR + (1.0 - _INK_FLOOR) * lit
    if d <= 0.0:
        # No whiskers. A whisker is a one dot line, and this technique has
        # no lines: thin enough to read as a whisker it falls between two
        # rows and flickers as the figure moves, thick enough to sample
        # reliably it is a bar across the frame. The outline techniques
        # get whiskers; tone does not.
        return 0.0

    if _dome(x, y - cy, rx, ry) is None:
        return d                                        # an ear, not the face

    shut = mood == "sleepy" or (tick + 1) % 12 < 1.0
    for sx in (-1.0, 1.0):                              # eyes
        ex, ey = sx * 0.25, cy - 0.15
        if shut:
            # A shut eye is a *light* line, not a dark one. The open eye
            # is a bright disc with a dark pupil in it, so the lid that
            # closes over it has to stay bright or the face gains two dark
            # slots where its eyes were.
            if abs(x - ex) < 0.14:
                g = _band(y - ey, 0.06)
                if g > 0.0:
                    return d + (0.10 - d) * g
        else:
            # The eyes widen with load, the same reading the braille cat
            # uses, so the two techniques say the same thing about the
            # same machine.
            cpu = _sat(float(sig.get("cpu") or 0.0))
            wide = 1.0 + 0.22 * cpu
            r = math.hypot((x - ex) / (0.125 * wide),
                           (y - ey) / (0.115 * wide))
            if r < 1.0:
                return 1.0 if r < 0.46 else 0.10
    if _tri(x * 1.6, y - (cy + 0.10), 0.070, 0.080) > 0.0:
        return 1.0                                      # nose
    m = 0.11 if mood == "happy" else (-0.11 if mood == "sad" else 0.0)
    if abs(x) < 0.18:                                   # mouth
        my = cy + 0.26 + m * math.cos(min(1.0, abs(x) / 0.18) * 1.57)
        g = _band(y - my, 0.055)
        if g > 0.0:
            return max(d, g)
    return d


def render(cols, nrows, tick=0.0, mood="idle", signals=None):
    """Sample `field` onto a `cols` x `nrows` grid of 0..1 densities.

    Fitted at `MODEL_ASPECT` and centred, never stretched to fill: the
    cell grid has whatever proportions the caller asked for and the cat
    has its own.
    """
    vw, vh = float(cols), nrows * CELL_RATIO
    scale = min(vw / MODEL_ASPECT, vh)
    bw, bh = scale * MODEL_ASPECT, scale
    x0, y0 = (vw - bw) / 2.0, (vh - bh) / 2.0
    grid = []
    for r in range(nrows):
        v = ((r + 0.5) * CELL_RATIO - y0) / bh
        row = []
        for c in range(cols):
            u = ((c + 0.5) - x0) / bw
            row.append(0.0 if not (0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)
                       else field(u, v, tick, mood, signals))
        grid.append(row)
    return grid


def sub_for(cols, cap=MAX_ATTRS):
    """How finely a cell can be subdivided and still fit one envelope.

    The drawn resolution of a heat span is the grid it carries, not the
    cells it covers: a receiver scales the grid to the span's box. So the
    only limit on detail is the 2048 byte cap, and the useful thing to do
    is spend all of it. A row subdivided `s` ways in both axes carries
    `cols * s * s` values, so `s` falls as the row gets wider, which is
    right: a wide row already has the detail a narrow one is buying.
    """
    room = (cap - 64) // _PER_VALUE          # 64 for the keys around `d`
    s = SUB_MAX
    while s > 1 and cols * s * s > room:
        s -= 1
    return s


def rows(cols, nrows, tick=0.0, mood="idle", signals=None, color="teal",
         sub=None):
    """The figure as `nrows` `heat` spans, one per terminal row.

    One span per row rather than one for the grid, because the grid does
    not fit: a sixty by eighteen frame is about 5400 bytes of payload and
    the cap is 2048. A row is a few hundred, and the room left over goes
    on subdividing the cells: the fallback still prints one character per
    cell, and a terminal that draws the span gets `sub` times the
    resolution in each axis for free.
    """
    s = sub or sub_for(cols)
    if s > sub_for(cols):
        # Say which knob, rather than let `span` report a byte count from
        # three frames down the stack.
        raise ValueError(
            f"sub={s} does not fit an envelope at {cols} columns; the most "
            f"that fits is {sub_for(cols)}. Subdivision costs its square, "
            f"so a wider figure can afford less of it.")
    grid = render(cols * s, nrows * s, tick, mood, signals)
    return [heat(grid[r * s:(r + 1) * s], color=color, lo=0.0, hi=1.0,
                 style="ramp", box=(cols, 1))
            for r in range(nrows)]
