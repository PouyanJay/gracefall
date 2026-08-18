"""gracefall: fallback-first graphics for terminals (OSC 4700).

Wrap generated fallback text in an OSC span that carries the underlying data.
Terminals that don't implement OSC 4700 silently consume the envelope and
display the fallback. Terminals that do implement it re-render the span's
cells as theme-aware vector graphics driven by the data in the envelope.

The fallback is simultaneously the degradation path, the size contract,
the copy/grep surface, and the accessibility layer.

Library API:

    spark(data)          inline trend line
    meter(value)         horizontal gauge
    dist(values)         histogram
    flow(stages, states) pipeline strip
    scatter(points)      x/y point cloud with trend
    heat(rows)           value grid
    span(attrs, text)    raw envelope, for new types
    strip_spans(s)       remove all gracefall envelopes, keep fallback
"""

import re

__version__ = "0.5.0"
__all__ = ["OSC_NUM", "span", "spark", "meter", "flow", "dist", "scatter",
           "heat", "strip_spans", "ROLE_RGB", "SGR"]

OSC_NUM = 4700
OSC = f"\x1b]{OSC_NUM};"
ST = "\x1b\\"
R = "\x1b[0m"
MAX_ATTRS = 2048

ROLE_RGB = {"fg": (222, 227, 236), "dim": (110, 120, 138),
            "teal": (95, 227, 192), "blue": (108, 162, 245),
            "amber": (238, 190, 106), "coral": (240, 138, 108),
            "violet": (168, 148, 244)}
SGR = {k: f"\x1b[38;2;{r};{g};{b}m" for k, (r, g, b) in ROLE_RGB.items()}

_SPAN_RE = re.compile(r"\x1b\]4700;[^\x07\x1b]*(?:\x07|\x1b\\)")


def span(attrs: str, fallback: str) -> str:
    """Wrap fallback text in a gracefall envelope carrying `attrs`."""
    if len(attrs) > MAX_ATTRS:
        raise ValueError(
            f"envelope is {len(attrs)} bytes, cap is {MAX_ATTRS}. "
            "Downsample the data; this is a display protocol, not a transport.")
    return f"{OSC}{attrs}{ST}{fallback}{OSC}{ST}"


def strip_spans(s: str) -> str:
    """Remove every gracefall envelope, leaving exactly what an
    unaware terminal displays."""
    return _SPAN_RE.sub("", s)


def _bucket(data, width):
    """Downsample data to `width` points by bucket means."""
    if width is None or width >= len(data):
        return list(data)
    out = []
    n = len(data)
    for i in range(width):
        lo = i * n // width
        hi = max(lo + 1, (i + 1) * n // width)
        seg = data[lo:hi]
        out.append(sum(seg) / len(seg))
    return out


def spark(data, color="blue", style="line", lo=None, hi=None, width=None):
    data = _bucket([float(v) for v in data], width)
    lo = min(data) if lo is None else lo
    hi = max(data) if hi is None else hi
    rng = (hi - lo) or 1.0
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    fb = "".join(blocks[min(7, int((v - lo) / rng * 7.999))] for v in data)
    d = ",".join(f"{v:g}" for v in data)
    return span(f"t=spark;d={d};lo={lo:g};hi={hi:g};style={style};c={color}",
                f"{SGR[color]}{fb}{R}")


def meter(v, width=24, color="teal"):
    EI = " \u258f\u258e\u258d\u258c\u258b\u258a\u2589"
    v = max(0.0, min(1.0, float(v)))
    t = v * width * 8
    full, rem = int(t // 8), int(t % 8)
    fb = "\u2588" * full + (EI[rem] if rem else "")
    fb = fb + "\u2581" * (width - len(fb))
    return span(f"t=meter;v={v:g};w={width};c={color}", f"{SGR[color]}{fb}{R}")


def flow(stages, statuses):
    scol = {"done": "teal", "active": "amber", "pending": "dim",
            "failed": "coral"}
    for s in statuses:
        if s not in scol:
            raise ValueError(f"unknown status {s!r}, use one of {list(scol)}")
    parts = [f"{n}:{s}" for n, s in zip(stages, statuses)]
    # One space each side of every name. The padding is part of the wire
    # format, not styling: a receiver that draws stage markers finds each
    # name's cells from this layout, and the extra cells are what give its
    # drawing room around the text it cannot resize.
    fb = f"{SGR['dim']}\u2500\u2500{R}".join(
        f"{SGR[scol[s]]} {n} {R}" for n, s in zip(stages, statuses))
    return span("t=flow;n=" + ",".join(parts), fb)


def dist(values, bins=26, color="blue", lo=None, hi=None):
    values = [float(v) for v in values]
    lo = min(values) if lo is None else lo
    hi = max(values) if hi is None else hi
    rng = (hi - lo) or 1.0
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, max(0, int((v - lo) / rng * bins)))] += 1
    mx = max(counts) or 1
    blocks = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
    fb = "".join(blocks[min(7, int(c / mx * 7.999))] for c in counts)
    b = ",".join(str(c) for c in counts)
    return span(f"t=dist;b={b};lo={lo:g};hi={hi:g};c={color}",
                f"{SGR[color]}{fb}{R}")


#: The cell vocabulary of a `lanes` row and the box-drawing fallback of
#: each kind: blank, lane bar, commit, merge, a lane leaving to the right
#: (git's `\\`), a lane joining from the right (git's `/`), a crossing rule.
LANE_GLYPH = {".": " ", "b": "\u2502", "d": "\u25cf", "m": "\u25cb",
              "r": "\u2572", "l": "\u2571", "h": "\u2500"}


def lanes(cells):
    """One row of a commit graph: `cells` is a list of (kind, role) with
    kind from LANE_GLYPH and role a colour role or None for the default.
    A `d` or `m` cell's role is its lane's colour; the dot is drawn in it
    and the lane runs through it. Same data, both renderings: the fallback
    is the box characters, coloured per cell."""
    fb = []
    d = []
    for kind, role in cells:
        if kind not in LANE_GLYPH:
            raise ValueError(f"unknown lane cell {kind!r}")
        if kind == ".":
            fb.append(" ")
            d.append(".")
            continue
        role = role or "teal"
        fb.append(f"{SGR[role]}{LANE_GLYPH[kind]}{R}")
        d.append(f"{kind}:{role}")
    return span("t=lanes;d=" + ",".join(d), "".join(fb))


_BR = [[0x01, 0x08], [0x02, 0x10], [0x04, 0x20], [0x40, 0x80]]


def scatter(pts, w=30, h=4, color="coral", indent=0):
    pts = [(float(x), float(y)) for x, y in pts]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    xlo, xhi = min(xs), max(xs)
    ylo, yhi = min(ys), max(ys)
    xr = (xhi - xlo) or 1
    yr = (yhi - ylo) or 1
    gw, gh = w * 2, h * 4
    dots = [[0] * gw for _ in range(gh)]
    for x, y in pts:
        gx = min(gw - 1, int((x - xlo) / xr * (gw - 0.001)))
        gy = min(gh - 1, int((1 - (y - ylo) / yr) * (gh - 0.001)))
        dots[gy][gx] = 1
    lines = []
    for cy in range(h):
        row = []
        for cx in range(w):
            bits = 0
            for dy in range(4):
                for dx in range(2):
                    if dots[cy * 4 + dy][cx * 2 + dx]:
                        bits |= _BR[dy][dx]
            row.append(chr(0x2800 + bits))
        lines.append("".join(row))
    n = len(pts)
    sx, sy = sum(xs), sum(ys)
    m = (n * sum(x * y for x, y in pts) - sx * sy) / \
        ((n * sum(x * x for x in xs) - sx * sx) or 1)
    b = (sy - m * sx) / n
    fb = ("\n" + " " * indent).join(f"{SGR[color]}{l}{R}" for l in lines)
    d = ",".join(f"{x:g}:{y:g}" for x, y in pts)
    return span(f"t=scatter;d={d};xlo={xlo:g};xhi={xhi:g};ylo={ylo:g};"
                f"yhi={yhi:g};m={m:.4g};tb={b:.4g};c={color}", fb)


def heat(rows, color="teal", indent=0, lo=None, hi=None):
    rows = [[float(v) for v in r] for r in rows]
    lo = min(min(r) for r in rows) if lo is None else lo
    hi = max(max(r) for r in rows) if hi is None else hi
    rng = (hi - lo) or 1.0
    tr, tg, tb = ROLE_RGB[color]
    base = (24, 30, 42)

    def ramp(v):
        u = (v - lo) / rng
        return tuple(int(base[i] + ((tr, tg, tb)[i] - base[i]) * u)
                     for i in range(3))

    lines = []
    for i in range(0, len(rows), 2):
        top = rows[i]
        bot = rows[i + 1] if i + 1 < len(rows) else rows[i]
        s = []
        for j in range(len(top)):
            f = ramp(top[j])
            g = ramp(bot[j])
            s.append(f"\x1b[38;2;{f[0]};{f[1]};{f[2]}m"
                     f"\x1b[48;2;{g[0]};{g[1]};{g[2]}m\u2580")
        lines.append("".join(s) + R)
    fb = ("\n" + " " * indent).join(lines)
    d = ":".join(",".join(f"{v:.2f}" for v in r) for r in rows)
    return span(f"t=heat;d={d};lo={lo:g};hi={hi:g};c={color}", fb)
