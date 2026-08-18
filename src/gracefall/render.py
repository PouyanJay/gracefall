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

from .shapes import box_from_cells, catmull_rom, shapes_for

CW, CHH, PAD, HDR = 12, 24, 20, 34
PAL = {"fg": "#e6ebf4", "dim": "#6e788a", "teal": "#5fe3c0",
       "blue": "#6ca2f5", "amber": "#eebe6a", "coral": "#f08a6c",
       "violet": "#a894f4"}
BGC = "#10131a"
TRACK = "#232a36"

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


def _color(role):
    """Resolve a shapes.py role name to this backend's palette."""
    if role == "bg":
        return BGC
    if role == "track":
        return TRACK
    return PAL.get(role, PAL["blue"])


def _paint(paint, defs):
    """Return (svg_paint_value, opacity_or_None) for a shapes.py paint."""
    if paint is None:
        return "none", None
    kind = paint[0]
    if kind == "solid":
        _, role, alpha = paint
        return _color(role), None if alpha >= 1 else f"{alpha:.2f}"
    _, role, a0, a1, vertical = paint
    gid = f"g{len(defs)}"
    x2, y2 = ("0", "1") if vertical else ("1", "0")
    col = _color(role)
    defs.append(
        f'<linearGradient id="{gid}" x1="0" y1="0" x2="{x2}" y2="{y2}">'
        f'<stop offset="0" stop-color="{col}" stop-opacity="{a0:.2f}"/>'
        f'<stop offset="1" stop-color="{col}" stop-opacity="{a1:.2f}"/>'
        f'</linearGradient>')
    return f"url(#{gid})", None


def _fill_attrs(paint, defs):
    val, op = _paint(paint, defs)
    return f'fill="{val}"' + (f' fill-opacity="{op}"' if op else "")


def _stroke_attrs(paint, defs, width):
    val, op = _paint(paint, defs)
    if val == "none":
        return 'stroke="none"'
    return (f'stroke="{val}" stroke-width="{width:g}"'
            + (f' stroke-opacity="{op}"' if op else ""))


def _path_d(pts):
    """SVG path data for the Catmull-Rom smoothing in shapes.py."""
    (sx, sy), segs = catmull_rom(pts)
    d = f"M{sx:.1f},{sy:.1f}"
    for c1x, c1y, c2x, c2y, ex, ey in segs:
        d += (f"C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} "
              f"{ex:.1f},{ey:.1f}")
    return d


def _to_svg(shape, defs):
    """One shapes.py primitive as one or more SVG elements."""
    kind = shape[0]
    if kind == "line":
        _, x1, y1, x2, y2, paint, width, dash = shape
        dash_a = f' stroke-dasharray="{dash[0]} {dash[1]}"' if dash else ""
        return [f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" '
                f'y2="{y2:.1f}" {_stroke_attrs(paint, defs, width)}'
                f'{dash_a}/>']
    if kind == "polyline":
        _, pts, paint, width = shape
        d = "M" + "L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
        return [f'<path d="{d}" fill="none" '
                f'{_stroke_attrs(paint, defs, width)}/>']
    if kind == "curve":
        _, pts, paint, width = shape
        return [f'<path d="{_path_d(pts)}" fill="none" '
                f'{_stroke_attrs(paint, defs, width)} '
                f'stroke-linecap="round"/>']
    if kind == "area":
        _, pts, paint, y_base = shape
        d = (f"{_path_d(pts)}L{pts[-1][0]:.1f},{y_base:.1f}"
             f"L{pts[0][0]:.1f},{y_base:.1f}Z")
        return [f'<path d="{d}" {_fill_attrs(paint, defs)}/>']
    if kind == "rrect":
        _, x, y, w, h, rx, fill, stroke, sw = shape
        el = (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" '
              f'height="{h:.1f}" rx="{rx:g}" {_fill_attrs(fill, defs)}')
        if stroke is not None:
            el += " " + _stroke_attrs(stroke, defs, sw)
        return [el + "/>"]
    if kind == "circle":
        _, cx, cy, r, fill, stroke, sw = shape
        el = (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:g}" '
              f'{_fill_attrs(fill, defs)}')
        if stroke is not None:
            el += " " + _stroke_attrs(stroke, defs, sw)
        return [el + "/>"]
    if kind == "text":
        _, s, cx, cy, size, paint = shape
        val, op = _paint(paint, defs)
        opa = f' fill-opacity="{op}"' if op else ""
        # shapes.py gives the visual center; nudge to a baseline.
        return [f'<text x="{cx:.1f}" y="{cy + size * 0.31:.1f}" '
                f'text-anchor="middle" font-family="monospace" '
                f'font-size="{size:g}" fill="{val}"{opa}>'
                f'{html.escape(s)}</text>']
    return []


def _overlay(sp, defs):
    """SVG elements for one span, via the shared geometry core.

    Clipped to the span's own box because SPEC.md confines the enhanced
    rendering to the span's cells. Without the clip this renderer shows
    things no conforming terminal can draw: the spark's end dot is centered
    on the right edge and spills 3.4px past it, which a receiver painting
    into the span's cell rect necessarily cuts in half.
    """
    a = attrs_dict(sp["attrs"])
    box = box_from_cells(sp["cells"], PAD, PAD + HDR, CW, CHH, a)
    shapes = shapes_for(a, box)
    if not shapes:
        return []
    x, y, w, h = box
    cid = f"c{len(defs)}"
    defs.append(f'<clipPath id="{cid}"><rect x="{x:.1f}" y="{y:.1f}" '
                f'width="{w:.1f}" height="{h:.1f}"/></clipPath>')
    out = [f'<g clip-path="url(#{cid})">']
    for shape in shapes:
        out.extend(_to_svg(shape, defs))
    out.append("</g>")
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
