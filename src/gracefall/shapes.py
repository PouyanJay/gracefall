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


def cell_bbox(cells):
    """Return (row0, col0, nrows, ncols) over the span's non-space cells, or
    None if the span drew nothing.

    SPEC.md computes a span's drawing rectangle from its non-space cells, so
    the indentation whitespace inside a multi-line span never distorts the
    box. This is that rule, and it is the only place it is implemented.
    """
    live = [(r, c) for r, c, ch in cells if ch != " "]
    if not live:
        return None
    rows = [r for r, _ in live]
    cols = [c for _, c in live]
    return (min(rows), min(cols),
            max(rows) - min(rows) + 1, max(cols) - min(cols) + 1)


def box_from_cells(cells, x0, y0, cellw, cellh):
    """Map a span's cells onto a pixel Box given the origin and cell size."""
    bb = cell_bbox(cells)
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
    out.append(("circle", pts[-1][0], pts[-1][1], 3.4,
                solid("bg"), solid(role), 2))
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
    out = []
    for ri, vals in enumerate(rows):
        for ci, v in enumerate(vals):
            u = (v - lo) / rng
            out.append(("rrect", _r(x + ci * cw + 1), _r(y + ri * ch + 1),
                        _r(cw - 2), _r(ch - 2), 2.5,
                        solid(role, 0.08 + 0.92 * u), None, 0))
    return out


_TYPES = {"spark": _spark, "meter": _meter, "flow": _flow,
          "dist": _dist, "scatter": _scatter, "heat": _heat}


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
