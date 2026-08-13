"""gracefall.render: the reference renderer.

Plays the role of a terminal. `parse` consumes a byte stream and returns the
character grid plus every gracefall span with the cells it covered. `render`
produces an SVG of either view:

    enhanced=False   what every existing terminal shows (envelope swallowed)
    enhanced=True    what a terminal implementing OSC 4700 shows

This exists so emitter changes can be verified in CI and so terminal authors
have executable semantics to implement against. It is not a terminal.
"""

import html
import re

CW, CHH, PAD, HDR = 12, 24, 20, 34
PAL = {"fg": "#e6ebf4", "dim": "#6e788a", "teal": "#5fe3c0",
       "blue": "#6ca2f5", "amber": "#eebe6a", "coral": "#f08a6c",
       "violet": "#a894f4"}
BGC = "#10131a"

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_ENV = re.compile(r"\x1b\]4700;([^\x07\x1b]*)(\x07|\x1b\\)")


def parse(stream):
    """Return (grid, spans, nrows). grid maps (row, col) to (ch, fg, bg).
    Each span is {"attrs": str, "cells": [(row, col, ch), ...]}."""
    grid, spans, open_span = {}, [], None
    row = col = 0
    fg, bg = PAL["fg"], None
    i, n = 0, len(stream)
    while i < n:
        c = stream[i]
        if c == "\x1b":
            m = _SGR.match(stream, i)
            if m:
                p = [int(x) for x in m.group(1).split(";") if x] or [0]
                j = 0
                while j < len(p):
                    if p[j] == 0:
                        fg, bg = PAL["fg"], None
                    elif p[j] == 38 and j + 4 < len(p) and p[j + 1] == 2:
                        fg = "#%02x%02x%02x" % tuple(p[j + 2:j + 5])
                        j += 4
                    elif p[j] == 48 and j + 4 < len(p) and p[j + 1] == 2:
                        bg = "#%02x%02x%02x" % tuple(p[j + 2:j + 5])
                        j += 4
                    elif p[j] == 39:
                        fg = PAL["fg"]
                    elif p[j] == 49:
                        bg = None
                    j += 1
                i = m.end()
                continue
            m = _ENV.match(stream, i)
            if m:
                attrs = m.group(1)
                if attrs:
                    open_span = {"attrs": attrs, "cells": []}
                elif open_span is not None:
                    spans.append(open_span)
                    open_span = None
                i = m.end()
                continue
            i += 1
            continue
        if c == "\n":
            row += 1
            col = 0
            i += 1
            continue
        grid[(row, col)] = (c, fg, bg)
        if open_span is not None:
            open_span["cells"].append((row, col, c))
        col += 1
        i += 1
    return grid, spans, row + 1


def attrs_dict(a):
    return dict(kv.split("=", 1) for kv in a.split(";") if "=" in kv)


def _smooth(pts):
    if len(pts) < 3:
        return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(len(pts) - 1):
        p0 = pts[max(0, i - 1)]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[min(len(pts) - 1, i + 2)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += (f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} "
              f"{p2[0]:.1f},{p2[1]:.1f}")
    return d


def _overlay(sp, defs):
    a = attrs_dict(sp["attrs"])
    cells = [(r, c) for r, c, ch in sp["cells"] if ch != " "]
    if not cells or "t" not in a:
        return []
    rows = [r for r, _ in cells]
    cols = [c for _, c in cells]
    x = PAD + min(cols) * CW
    y = PAD + HDR + min(rows) * CHH
    w = (max(cols) - min(cols) + 1) * CW
    h = (max(rows) - min(rows) + 1) * CHH
    col = PAL.get(a.get("c", "blue"), PAL["blue"])
    out = []
    t = a["t"]
    if t == "spark":
        d = [float(v) for v in a["d"].split(",")]
        lo, hi = float(a["lo"]), float(a["hi"])
        rng = (hi - lo) or 1
        ins = 3
        pts = [(x + i / (max(1, len(d) - 1)) * w,
                y + h - ins - (v - lo) / rng * (h - 2 * ins))
               for i, v in enumerate(d)]
        pd = _smooth(pts)
        if a.get("style") == "area":
            gid = f"g{len(defs)}"
            defs.append(
                f'<linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{col}" stop-opacity=".35"/>'
                f'<stop offset="1" stop-color="{col}" stop-opacity="0"/>'
                f'</linearGradient>')
            out.append(f'<path d="{pd}L{x + w:.1f},{y + h}L{x:.1f},{y + h}Z" '
                       f'fill="url(#{gid})"/>')
        out.append(f'<path d="{pd}" fill="none" stroke="{col}" '
                   f'stroke-width="2.2" stroke-linecap="round"/>')
        out.append(f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" '
                   f'r="3.4" fill="{BGC}" stroke="{col}" stroke-width="2"/>')
    elif t == "meter":
        v = float(a["v"])
        gid = f"g{len(defs)}"
        defs.append(f'<linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0">'
                    f'<stop offset="0" stop-color="{col}" stop-opacity=".55"/>'
                    f'<stop offset="1" stop-color="{col}"/></linearGradient>')
        out.append(f'<rect x="{x}" y="{y + 7}" width="{w}" height="{h - 14}" '
                   f'rx="{(h - 14) / 2}" fill="#232a36"/>')
        if v > 0.01:
            out.append(f'<rect x="{x}" y="{y + 7}" '
                       f'width="{max(h - 14, v * w):.1f}" height="{h - 14}" '
                       f'rx="{(h - 14) / 2}" fill="url(#{gid})"/>')
    elif t == "flow":
        items = [it.split(":") for it in a["n"].split(",")]
        scol = {"done": PAL["teal"], "active": PAL["amber"],
                "pending": PAL["dim"], "failed": PAL["coral"]}
        n = len(items)
        pw = (w - (n - 1) * 26) / n
        cy = y + h / 2
        px = x
        for i, (name, st) in enumerate(items):
            c = scol.get(st, PAL["dim"])
            if i:
                out.append(f'<line x1="{px - 26:.1f}" y1="{cy}" '
                           f'x2="{px:.1f}" y2="{cy}" '
                           f'stroke="{PAL["dim"]}" stroke-width="1.4"/>')
                out.append(f'<path d="M{px - 8:.1f} {cy - 3.5}'
                           f'L{px - 3:.1f} {cy}L{px - 8:.1f} {cy + 3.5}" '
                           f'fill="none" stroke="{PAL["dim"]}" '
                           f'stroke-width="1.4"/>')
            fill = c if st in ("done", "active") else "none"
            tcol = BGC if st in ("done", "active") else c
            out.append(f'<rect x="{px:.1f}" y="{y + 3}" width="{pw:.1f}" '
                       f'height="{h - 6}" rx="{(h - 6) / 2}" fill="{fill}" '
                       f'stroke="{c}" stroke-width="1.4"/>')
            out.append(f'<text x="{px + pw / 2:.1f}" y="{cy + 4:.1f}" '
                       f'text-anchor="middle" font-family="monospace" '
                       f'font-size="13" fill="{tcol}">{html.escape(name)}'
                       f'</text>')
            px += pw + 26
    elif t == "dist":
        counts = [int(v) for v in a["b"].split(",")]
        mx = max(counts) or 1
        bw = w / len(counts)
        base = y + h - 2
        for i, cnt in enumerate(counts):
            bh = max(2, cnt / mx * (h - 6))
            out.append(f'<rect x="{x + i * bw + 1:.1f}" '
                       f'y="{base - bh:.1f}" width="{bw - 2:.1f}" '
                       f'height="{bh:.1f}" rx="2" fill="{col}" '
                       f'fill-opacity="{0.35 + 0.65 * cnt / mx:.2f}"/>')
        out.append(f'<line x1="{x}" y1="{base}" x2="{x + w}" y2="{base}" '
                   f'stroke="{PAL["dim"]}" stroke-width="1" '
                   f'stroke-opacity=".5"/>')
    elif t == "scatter":
        pts = [tuple(float(v) for v in p.split(":"))
               for p in a["d"].split(",")]
        xlo, xhi = float(a["xlo"]), float(a["xhi"])
        ylo, yhi = float(a["ylo"]), float(a["yhi"])
        xr = (xhi - xlo) or 1
        yr = (yhi - ylo) or 1
        ins = 5

        def mx_(vx):
            return x + ins + (vx - xlo) / xr * (w - 2 * ins)

        def my_(vy):
            return y + h - ins - (vy - ylo) / yr * (h - 2 * ins)

        if "m" in a and "tb" in a:
            m, b = float(a["m"]), float(a["tb"])
            out.append(f'<line x1="{mx_(xlo):.1f}" y1="{my_(m * xlo + b):.1f}" '
                       f'x2="{mx_(xhi):.1f}" y2="{my_(m * xhi + b):.1f}" '
                       f'stroke="{PAL["fg"]}" stroke-width="1.4" '
                       f'stroke-opacity=".45" stroke-dasharray="5 5"/>')
        for vx, vy in pts:
            out.append(f'<circle cx="{mx_(vx):.1f}" cy="{my_(vy):.1f}" '
                       f'r="2.8" fill="{col}" fill-opacity=".85"/>')
    elif t == "heat":
        rows_v = [[float(v) for v in r.split(",")]
                  for r in a["d"].split(":")]
        lo, hi = float(a["lo"]), float(a["hi"])
        rng = (hi - lo) or 1
        nr, nc = len(rows_v), len(rows_v[0])
        chw, chh = w / nc, h / nr
        for ri, rvals in enumerate(rows_v):
            for ci, v in enumerate(rvals):
                u = (v - lo) / rng
                out.append(f'<rect x="{x + ci * chw + 1:.1f}" '
                           f'y="{y + ri * chh + 1:.1f}" '
                           f'width="{chw - 2:.1f}" height="{chh - 2:.1f}" '
                           f'rx="2.5" fill="{col}" '
                           f'fill-opacity="{0.08 + 0.92 * u:.2f}"/>')
    return out


def render(stream, enhanced=True, title=""):
    """Return an SVG string of the stream in either view."""
    grid, spans, nrows = parse(stream)
    ncols = max((c for _, c in grid), default=79) + 1
    Wpx = ncols * CW + 2 * PAD
    Hpx = nrows * CHH + 2 * PAD + HDR
    hide = set()
    if enhanced:
        for sp in spans:
            hide |= {(r, c) for r, c, _ in sp["cells"]}
    defs, body = [], []
    body.append(f'<rect width="{Wpx}" height="{Hpx}" fill="{BGC}"/>')
    if title:
        body.append(f'<text x="{PAD}" y="24" font-family="monospace" '
                    f'font-size="15" fill="{PAL["dim"]}">'
                    f'{html.escape(title)}</text>')
    for (r, c), (ch, fg, bg) in sorted(grid.items()):
        if (r, c) in hide:
            continue
        if bg:
            body.append(f'<rect x="{PAD + c * CW}" y="{PAD + HDR + r * CHH}" '
                        f'width="{CW}" height="{CHH}" fill="{bg}"/>')
        if ch != " ":
            body.append(f'<text x="{PAD + c * CW}" '
                        f'y="{PAD + HDR + r * CHH + 17}" '
                        f'font-family="monospace" font-size="20" '
                        f'fill="{fg}">{html.escape(ch)}</text>')
    if enhanced:
        for sp in spans:
            body.extend(_overlay(sp, defs))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{Wpx}" '
            f'height="{Hpx}">'
            f'<defs>{"".join(defs)}</defs>{"".join(body)}</svg>')
