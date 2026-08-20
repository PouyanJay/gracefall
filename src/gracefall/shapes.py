"""gracefall.shapes: the geometry core, shared by every backend.

`shapes_for(attrs, box)` turns one span's attributes into a list of abstract
drawing primitives placed inside `box`. It contains no SVG, no Pillow, and
no literal colors, only the role names from SPEC.md. Both the reference SVG
renderer and the terminal viewer draw from this one source of truth, so the
two backends cannot drift apart.

Coordinates are abstract pixels. The caller decides what a pixel is: the SVG
renderer uses a fixed 12 x 24 cell, the viewer uses the terminal's real cell
metrics. Geometry is expressed as fractions of the box wherever possible so
both look right.

A Box is `(x, y, w, h)`.

Paints
------
A paint says what color something is without saying which color that is:

    ("solid", role, alpha)
    ("lgrad", role, alpha0, alpha1, vertical)

`lgrad` is a linear gradient in one role across the shape's own bounding
box, left to right unless `vertical`. Backends resolve `role` against the
palette. Beyond the seven SPEC.md roles, two pseudo-roles name things only
a backend knows: `bg` is the surface behind the span, `track` is the unfilled
groove of a meter.

Shapes
------
    ("line", x1, y1, x2, y2, paint, width, dash)
    ("polyline", points, paint, width)
    ("curve", points, paint, width)
    ("area", points, paint, y_base)
    ("rrect", x, y, w, h, rx, fill, stroke, stroke_w)
    ("circle", cx, cy, r, fill, stroke, stroke_w)
    ("text", s, cx, cy, size, paint)

`polyline` is an open run of straight segments joined at the corners, used
for the flow arrowheads: two separate lines would blunt the tip.

`dash`, `fill`, and `stroke` may be None. `points` in `curve` and `area` are
the raw data points: pass them through `catmull_rom` to get the smoothed
cubic segments, so every backend smooths identically. `text` gives the
center of the string; backends apply their own baseline correction.
"""

ROLES = ("fg", "dim", "teal", "blue", "amber", "coral", "violet")

#: Pseudo-roles that are not in SPEC.md because only a backend can know them.
PSEUDO_ROLES = ("bg", "track")


def solid(role, alpha=1.0):
    return ("solid", role, _r(alpha))


def lgrad(role, alpha0, alpha1, vertical=False):
    return ("lgrad", role, _r(alpha0), _r(alpha1), vertical)


def _r(v):
    """Round to keep golden snapshots stable and readable."""
    return round(float(v), 4)


def _role(attrs, default="blue"):
    """The span's color role. Unknown names fall back rather than fail, so
    a stream from a newer emitter still draws."""
    c = attrs.get("c", default)
    return c if c in ROLES else default


#: Types whose drawing rectangle covers every cell, blanks included.
#: A lanes row's blank cells are where a leaving or joining lane's curve
#: lands, so they are part of the drawing, not indentation.
ALL_CELL_TYPES = ("lanes",)


def cell_bbox(cells, attrs=None):
    """Return (row0, col0, nrows, ncols) over the span's non-space cells, or
    None if the span drew nothing.

    SPEC.md computes a span's drawing rectangle from its non-space cells, so
    the indentation whitespace inside a multi-line span never distorts the
    box. This is that rule, and it is the only place it is implemented. The
    one exception is also SPEC.md's: a type in ALL_CELL_TYPES keeps its
    blank cells, which is why `attrs` may be passed.
    """
    if attrs is not None and attrs.get("t") in ALL_CELL_TYPES:
        live = [(r, c) for r, c, _ in cells]
    else:
        live = [(r, c) for r, c, ch in cells if ch != " "]
    if not live:
        return None
    rows = [r for r, _ in live]
    cols = [c for _, c in live]
    return (min(rows), min(cols),
            max(rows) - min(rows) + 1, max(cols) - min(cols) + 1)


def box_from_cells(cells, x0, y0, cellw, cellh, attrs=None):
    """Map a span's cells onto a pixel Box given the origin and cell size."""
    bb = cell_bbox(cells, attrs)
    if bb is None:
        return None
    r0, c0, nr, nc = bb
    return (x0 + c0 * cellw, y0 + r0 * cellh, nc * cellw, nr * cellh)


def catmull_rom(pts):
    """Smooth `pts` into cubic Bezier segments.

    Returns (start, segments) where segments is a list of
    (c1x, c1y, c2x, c2y, x, y). Fewer than three points stay straight, in
    which case every control point sits on the line and backends that only
    know how to draw cubics still get the right picture.
    """
    pts = [(_r(px), _r(py)) for px, py in pts]
    if len(pts) < 3:
        segs = []
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            segs.append((x1, y1, x2, y2, x2, y2))
        return pts[0], segs
    segs = []
    for i in range(len(pts) - 1):
        p0 = pts[max(0, i - 1)]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[min(len(pts) - 1, i + 2)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segs.append((_r(c1[0]), _r(c1[1]), _r(c2[0]), _r(c2[1]),
                     p2[0], p2[1]))
    return pts[0], segs


def flatten(start, segs, steps=16):
    """Sample Bezier segments into a polyline, for backends that cannot draw
    curves natively. `steps` per segment is plenty at terminal sizes."""
    out = [start]
    x0, y0 = start
    for c1x, c1y, c2x, c2y, x3, y3 in segs:
        for s in range(1, steps + 1):
            t = s / steps
            u = 1 - t
            out.append((
                _r(u * u * u * x0 + 3 * u * u * t * c1x
                   + 3 * u * t * t * c2x + t * t * t * x3),
                _r(u * u * u * y0 + 3 * u * u * t * c1y
                   + 3 * u * t * t * c2y + t * t * t * y3)))
        x0, y0 = x3, y3
    return out


def _spark(a, box):
    x, y, w, h = box
    data = [float(v) for v in a["d"].split(",")]
    lo, hi = float(a["lo"]), float(a["hi"])
    rng = (hi - lo) or 1.0
    role = _role(a)
    ins = 3
    pts = [(_r(x + i / max(1, len(data) - 1) * w),
            _r(y + h - ins - (v - lo) / rng * (h - 2 * ins)))
           for i, v in enumerate(data)]
    out = []
    if a.get("style") == "area":
        out.append(("area", tuple(pts), lgrad(role, 0.35, 0.0, True),
                    _r(y + h)))
    out.append(("curve", tuple(pts), solid(role), 2.2))
    # The current-value dot sits on the last point, which is on the box's
    # right edge, so half of it falls outside the span's cells. SPEC.md
    # confines rendering to those cells, so that half is undrawable and the
    # dot reads as a hook. Hold it inside by its own outer radius instead:
    # the curve is untouched and the marker is whole.
    dot_r, dot_sw = 3.4, 2
    edge = dot_r + dot_sw / 2
    cx = min(max(pts[-1][0], x + edge), x + w - edge)
    cy = min(max(pts[-1][1], y + edge), y + h - edge)
    out.append(("circle", _r(cx), _r(cy), dot_r,
                solid("bg"), solid(role), dot_sw))
    return out


def _meter(a, box):
    x, y, w, h = box
    v = max(0.0, min(1.0, float(a["v"])))
    role = _role(a, "teal")
    top, bar = _r(y + 7), _r(h - 14)
    out = [("rrect", _r(x), top, _r(w), bar, _r(bar / 2),
            solid("track"), None, 0)]
    if v > 0.01:
        out.append(("rrect", _r(x), top, _r(max(bar, v * w)), bar,
                    _r(bar / 2), lgrad(role, 0.55, 1.0), None, 0))
    return out


def _flow(a, box):
    x, y, w, h = box
    items = [it.split(":") for it in a["n"].split(",")]
    status_role = {"done": "teal", "active": "amber",
                   "pending": "dim", "failed": "coral"}
    gap = 26
    n = len(items)
    pw = (w - (n - 1) * gap) / n
    cy = _r(y + h / 2)
    px = x
    out = []
    for i, item in enumerate(items):
        name, st = item[0], item[1] if len(item) > 1 else "pending"
        role = status_role.get(st, "dim")
        if i:
            out.append(("line", _r(px - gap), cy, _r(px), cy,
                        solid("dim"), 1.4, None))
            out.append(("polyline",
                        ((_r(px - 8), _r(cy - 3.5)), (_r(px - 3), cy),
                         (_r(px - 8), _r(cy + 3.5))),
                        solid("dim"), 1.4))
        filled = st in ("done", "active")
        out.append(("rrect", _r(px), _r(y + 3), _r(pw), _r(h - 6),
                    _r((h - 6) / 2), solid(role) if filled else None,
                    solid(role), 1.4))
        out.append(("text", name, _r(px + pw / 2), cy, 13,
                    solid("bg") if filled else solid(role)))
        px += pw + gap
    return out


def _dist(a, box):
    x, y, w, h = box
    counts = [int(v) for v in a["b"].split(",")]
    mx = max(counts) or 1
    role = _role(a)
    bw = w / len(counts)
    base = _r(y + h - 2)
    out = []
    for i, cnt in enumerate(counts):
        bh = max(2, cnt / mx * (h - 6))
        out.append(("rrect", _r(x + i * bw + 1), _r(base - bh),
                    _r(bw - 2), _r(bh), 2,
                    solid(role, 0.35 + 0.65 * cnt / mx), None, 0))
    out.append(("line", _r(x), base, _r(x + w), base,
                solid("dim", 0.5), 1, None))
    return out


def _scatter(a, box):
    x, y, w, h = box
    pts = [tuple(float(v) for v in p.split(":"))
           for p in a["d"].split(",")]
    xlo, xhi = float(a["xlo"]), float(a["xhi"])
    ylo, yhi = float(a["ylo"]), float(a["yhi"])
    xr = (xhi - xlo) or 1.0
    yr = (yhi - ylo) or 1.0
    role = _role(a, "coral")
    ins = 5

    def px_(vx):
        return _r(x + ins + (vx - xlo) / xr * (w - 2 * ins))

    def py_(vy):
        return _r(y + h - ins - (vy - ylo) / yr * (h - 2 * ins))

    out = []
    if "m" in a and "tb" in a:
        m, b = float(a["m"]), float(a["tb"])
        out.append(("line", px_(xlo), py_(m * xlo + b),
                    px_(xhi), py_(m * xhi + b),
                    solid("fg", 0.45), 1.4, (5, 5)))
    for vx, vy in pts:
        out.append(("circle", px_(vx), py_(vy), 2.8,
                    solid(role, 0.85), None, 0))
    return out


def _heat(a, box):
    x, y, w, h = box
    rows = [[float(v) for v in r.split(",")] for r in a["d"].split(":")]
    lo, hi = float(a["lo"]), float(a["hi"])
    rng = (hi - lo) or 1.0
    role = _role(a, "teal")
    nr, nc = len(rows), len(rows[0])
    cw, ch = w / nc, h / nr
    # The gap between cells is what makes a grid of readings read as a grid
    # of readings. On a picture it is the opposite: gaps turn a shaded
    # figure into an LED sign. The emitter has already said which this is,
    # so use it rather than guessing from the cell size: `ramp` is the
    # fallback an emitter picks when the grid is a picture. No style key
    # means `half`, which is every heat span written before this existed,
    # and those render byte for byte as they always did.
    picture = a.get("style") == "ramp"
    ins = 0.0 if picture else 1.0
    rad = 0.0 if picture else 2.5
    out = []
    for ri, vals in enumerate(rows):
        for ci, v in enumerate(vals):
            u = (v - lo) / rng
            out.append(("rrect", _r(x + ci * cw + ins), _r(y + ri * ch + ins),
                        _r(cw - 2 * ins), _r(ch - 2 * ins), rad,
                        solid(role, 0.08 + 0.92 * u), None, 0))
    return out


def _s_curve(x1, x2, y, h, steps=8):
    """Points along a cubic from (x1, y) to (x2, y + h) that leaves and
    arrives vertically, so a lane bar in the row above and below joins it
    without a kink. Sampled, so both backends smooth it the same way they
    smooth a spark."""
    ym = y + h / 2
    pts = []
    for s in range(steps + 1):
        t = s / steps
        u = 1 - t
        px = u * u * u * x1 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x2
        py = u * u * u * y + 3 * u * u * t * ym + 3 * u * t * t * ym + t * t * t * (y + h)
        pts.append((_r(px), _r(py)))
    return tuple(pts)


def _lanes(a, box):
    """One graph row. Bars run the full row height so rows join; a lane
    leaving or joining is an S-curve from the centre of one neighbouring
    cell to the centre of the other; a lane sliding under the row is a
    rule along the bottom edge; a commit is a disc on its lane with the
    lane drawn through it, hollow for a merge."""
    x, y, w, h = box
    cells = a["d"].split(",")
    cw = w / max(1, len(cells))
    lw = 2.2
    r = _r(min(cw, h) * 0.3)
    cy = _r(y + h / 2)
    out = []
    for i, cell in enumerate(cells):
        kind, _, role = cell.partition(":")
        role = role or "teal"
        cx = _r(x + (i + 0.5) * cw)
        if kind == "b":
            out.append(("line", cx, _r(y), cx, _r(y + h), solid(role), lw, None))
        elif kind == "h":
            # A lane sliding under this row's lanes: along the bottom edge,
            # from the centre of one neighbouring cell to the centre of the
            # other, so the run of h cells and the bars they pass under
            # make one line, and a joining lane in the row below picks it
            # up where it ends. Lifted by half a stroke to stay in the box.
            yb = _r(y + h - lw / 2)
            out.append(("line", _r(cx - cw), yb, _r(cx + cw), yb,
                        solid(role), lw, None))
        elif kind in ("r", "l"):
            x1 = cx - cw if kind == "r" else cx + cw
            x2 = cx + cw if kind == "r" else cx - cw
            out.append(("curve", _s_curve(x1, x2, y, h), solid(role), lw))
        elif kind == "d":
            out.append(("line", cx, _r(y), cx, _r(y + h), solid(role), lw, None))
            out.append(("circle", cx, cy, r, solid(role), None, 0))
        elif kind == "m":
            out.append(("line", cx, _r(y), cx, _r(y + h), solid(role), lw, None))
            out.append(("circle", cx, cy, r, solid("bg"), solid(role), lw))
    return out


_TYPES = {"spark": _spark, "meter": _meter, "flow": _flow,
          "dist": _dist, "scatter": _scatter, "heat": _heat, "lanes": _lanes}


def shapes_for(attrs, box):
    """Return the drawing primitives for one span inside `box`.

    An unrecognized type returns no shapes, which is what makes SPEC.md's
    "fall back to displaying the span's text" rule work: a backend that gets
    nothing to draw leaves the fallback text alone.
    """
    fn = _TYPES.get(attrs.get("t"))
    if fn is None or box is None:
        return []
    return fn(attrs, box)
