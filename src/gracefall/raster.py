"""gracefall.raster: the Pillow backend over shapes.py.

Third consumer of the shared geometry core, after the SVG renderer and the
kitty shim. It draws two things:

    span_image()   one span, transparent, sized to its cells
    frame_png()    a whole stream, text and spans together

`frame_png` is what makes screenshots reproducible: it composes the same
picture a graphics-capable terminal would show, without needing one, so the
images in the README come out of the real pipeline instead of a prototype
script that can drift from it.

Pillow is imported inside functions, never at module scope, so importing
this module stays free for anything that only needs the palette helpers.
Everything here is behind the `view` extra.

Coordinate spaces
-----------------
shapes.py geometry is authored in abstract pixels where a cell is 12 x 24
(the constants in render.py). Real cells are not that size, so this scales
abstract units to device pixels rather than asking shapes.py for a
differently proportioned box: the insets and bar heights in the geometry are
absolute, and re-boxing would distort them.
"""

import io
import os
import re

from .render import CHH, CW, attrs_dict, parse
from .shapes import catmull_rom, cell_bbox, flatten, shapes_for

SUPERSAMPLE = 4

FONTS = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
]


# ---------------------------------------------------------------- palette


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def parse_color(s):
    """Accept #rgb, #rrggbb, and the rgb:RRRR/GGGG/BBBB of an OSC 11 reply."""
    if not s:
        return None
    if isinstance(s, (tuple, list)):
        return tuple(s[:3])
    s = s.strip()
    m = re.match(r"rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)$", s)
    if m:
        return tuple(int(g[:2], 16) if len(g) >= 2 else int(g * 2, 16)
                     for g in m.groups())
    s = s.lstrip("#")
    if len(s) == 3:
        return tuple(int(c * 2, 16) for c in s)
    if len(s) == 6:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    return None


def build_palette(bg=None):
    """Resolve SPEC.md roles plus the two pseudo-roles shapes.py can emit.

    `track` is mixed from the background rather than hardcoded so the meter
    groove sits correctly on a light theme as well as a dark one.
    """
    from . import ROLE_RGB
    pal = dict(ROLE_RGB)
    pal["bg"] = parse_color(bg) or (16, 19, 26)
    pal["track"] = mix(pal["bg"], pal["fg"], 0.14)
    return pal


# ------------------------------------------------------------------ fonts


#: Fonts to fall back to per glyph when the monospace face lacks one. These
#: are not monospace and must never be the primary: they are only ever asked
#: for a character the primary cannot draw.
GLYPH_FONTS = [
    "/System/Library/Fonts/Apple Symbols.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]

#: One glyph from each family the fallback text is built out of: eighth
#: blocks, the meter's left eighths, the heat grid's upper half block, the
#: flow connector, and braille for scatter.
FALLBACK_GLYPHS = "█▁▏▀─⣿"

#: A codepoint no font defines, so its rendering *is* that font's "missing
#: glyph" box. Comparing against it is the only reliable way to tell a real
#: glyph from tofu: a .notdef box has plenty of non-blank pixels, so simply
#: asking whether anything was drawn says yes for a character the font does
#: not have.
_UNDEFINED = ""


class FontSet:
    """A monospace primary plus per-glyph fallbacks, the way a terminal does
    it. macOS SFNSMono and Menlo have the block elements but no Braille
    Patterns, so a single face silently drops every scatter plot."""

    def __init__(self, size):
        from PIL import ImageFont
        self.warning = None
        self._cache = {}
        self.fonts = []
        for path in FONTS + GLYPH_FONTS:
            if not os.path.exists(path):
                continue
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                continue
            self.fonts.append((font, self._sig(font, _UNDEFINED)))
        if not self.fonts:
            self.fonts = [(ImageFont.load_default(), None)]
            self.warning = ("no monospace font found, text will not line up "
                            "with the graphics")
        self.primary = self.fonts[0][0]
        missing = [c for c in FALLBACK_GLYPHS if self.for_char(c) is None]
        if missing and not self.warning:
            self.warning = (f"no installed font can draw {''.join(missing)!r},"
                            " so part of the fallback view will be blank")

    @staticmethod
    def _sig(font, ch):
        try:
            mask = font.getmask(ch)
        except Exception:
            return None
        return (mask.size, bytes(mask))

    def for_char(self, ch):
        """The first font that genuinely has `ch`, or None if none does."""
        if ch in self._cache:
            return self._cache[ch]
        found = None
        for font, notdef in self.fonts:
            sig = self._sig(font, ch)
            if sig is not None and sig != notdef:
                found = font
                break
        self._cache[ch] = found
        return found

    def draw_with(self, ch):
        """The font to actually use: the covering one, else the primary so
        the cell still takes up its space."""
        return self.for_char(ch) or self.primary


def load_font(size, require=None):
    """Return (primary_font, warning). For single-font drawing such as the
    flow labels, which are plain ASCII."""
    fs = FontSet(size)
    return fs.primary, fs.warning


def require_pillow():
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        raise SystemExit(
            'this needs Pillow: pip install "gracefall[view]"')


# --------------------------------------------------------------- painting


def _rgba(palette, role, alpha):
    r, g, b = palette.get(role, palette["blue"])
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))


def _gradient(size, bbox, palette, paint):
    """An RGBA image whose alpha ramps across bbox, for lgrad paints.

    Built as a one pixel ramp and stretched, which is both far faster than a
    per-pixel loop and, unlike getchannel("A"), actually attached to the
    image: getchannel returns a copy, so writing through it yields a fully
    transparent gradient and silently drops the shape.
    """
    from PIL import Image
    _, role, a0, a1, vertical = paint
    r, g, b = palette.get(role, palette["blue"])
    x0, y0, x1, y1 = bbox
    lo, hi = (y0, y1) if vertical else (x0, x1)
    steps = max(1, int(round(hi - lo)))
    ramp = Image.new("L", (1, steps) if vertical else (steps, 1))
    load = ramp.load()
    for i in range(steps):
        v = int(round((a0 + (a1 - a0) * (i / steps)) * 255))
        load[(0, i) if vertical else (i, 0)] = max(0, min(255, v))
    # Hold the end stops outside the ramp, the way an SVG gradient does.
    alpha = Image.new("L", size, int(round(a0 * 255)))
    alpha.paste(ramp.resize((size[0], steps) if vertical
                            else (steps, size[1])),
                (0, int(round(lo))) if vertical else (int(round(lo)), 0))
    tail = int(round(lo)) + steps
    if vertical and tail < size[1]:
        alpha.paste(Image.new("L", (size[0], size[1] - tail),
                              int(round(a1 * 255))), (0, tail))
    elif not vertical and tail < size[0]:
        alpha.paste(Image.new("L", (size[0] - tail, size[1]),
                              int(round(a1 * 255))), (tail, 0))
    img = Image.new("RGBA", size, (r, g, b, 255))
    img.putalpha(alpha)
    return img


def _dash(p1, p2, on, off):
    """Split a segment into dashes. Pillow has no dash support."""
    (x1, y1), (x2, y2) = p1, p2
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    if length <= 0 or on <= 0:
        return [(p1, p2)]
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    out = []
    pos = 0.0
    while pos < length:
        end = min(length, pos + on)
        out.append(((x1 + ux * pos, y1 + uy * pos),
                    (x1 + ux * end, y1 + uy * end)))
        pos = end + off
    return out


class Canvas:
    """Draws shapes.py primitives with Pillow, scaling abstract units to
    device pixels."""

    def __init__(self, size, sx, sy, palette):
        from PIL import Image, ImageDraw
        self.img = Image.new("RGBA", size, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.img, "RGBA")
        self.sx, self.sy, self.pal = sx, sy, palette
        self.warning = None

    def pt(self, x, y):
        return (x * self.sx, y * self.sy)

    def pts(self, seq):
        return [self.pt(x, y) for x, y in seq]

    def width(self, w):
        return max(1, int(round(w * (self.sx + self.sy) / 2)))

    def _fill(self, drawer, paint, bbox):
        """Run drawer(target, color) for solids, or build a mask and
        composite a gradient through it."""
        if paint is None:
            return
        if paint[0] == "solid":
            drawer(self.draw, _rgba(self.pal, paint[1], paint[2]))
            return
        from PIL import Image, ImageChops, ImageDraw
        mask = Image.new("L", self.img.size, 0)
        drawer(ImageDraw.Draw(mask), 255)
        grad = _gradient(self.img.size, bbox, self.pal, paint)
        grad.putalpha(ImageChops.multiply(grad.getchannel("A"), mask))
        self.img.alpha_composite(grad)

    def add(self, shape):
        kind = shape[0]
        whole = (0, 0) + self.img.size
        if kind == "line":
            _, x1, y1, x2, y2, paint, w, dash = shape
            p1, p2 = self.pt(x1, y1), self.pt(x2, y2)
            segs = (_dash(p1, p2, dash[0] * self.sx, dash[1] * self.sx)
                    if dash else [(p1, p2)])
            for a, b in segs:
                self._fill(lambda d, c, a=a, b=b:
                           d.line([a, b], fill=c, width=self.width(w)),
                           paint, whole)
        elif kind in ("polyline", "curve"):
            _, pts, paint, w = shape
            pp = (self.pts(pts) if kind == "polyline"
                  else self.pts(flatten(*catmull_rom(pts))))
            self._fill(lambda d, c: d.line(pp, fill=c, width=self.width(w),
                                           joint="curve"), paint, whole)
        elif kind == "area":
            _, pts, paint, ybase = shape
            poly = self.pts(flatten(*catmull_rom(pts)))
            poly.append(self.pt(pts[-1][0], ybase))
            poly.append(self.pt(pts[0][0], ybase))
            xs = [p[0] for p in poly]
            ys = [p[1] for p in poly]
            self._fill(lambda d, c: d.polygon(poly, fill=c), paint,
                       (min(xs), min(ys), max(xs), max(ys)))
        elif kind == "rrect":
            _, x, y, w, h, rx, fill, stroke, sw = shape
            x0, y0 = self.pt(x, y)
            x1, y1 = self.pt(x + w, y + h)
            box = [x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)]
            r = min(rx * self.sy, (box[3] - box[1]) / 2,
                    (box[2] - box[0]) / 2)
            self._fill(lambda d, c: d.rounded_rectangle(box, radius=r,
                                                        fill=c),
                       fill, tuple(box))
            if stroke is not None:
                self._fill(lambda d, c: d.rounded_rectangle(
                    box, radius=r, outline=c, width=self.width(sw)),
                    stroke, tuple(box))
        elif kind == "circle":
            _, cx, cy, rr, fill, stroke, sw = shape
            px, py = self.pt(cx, cy)
            rx, ry = rr * self.sx, rr * self.sy
            box = [px - rx, py - ry, px + rx, py + ry]
            self._fill(lambda d, c: d.ellipse(box, fill=c), fill,
                       tuple(box))
            if stroke is not None:
                self._fill(lambda d, c: d.ellipse(
                    box, outline=c, width=self.width(sw)), stroke,
                    tuple(box))
        elif kind == "text":
            _, s, cx, cy, size, paint = shape
            font, warn = load_font(max(1, int(round(size * self.sy))))
            self.warning = self.warning or warn
            color = _rgba(self.pal, paint[1], paint[2])
            try:
                self.draw.text(self.pt(cx, cy), s, font=font, fill=color,
                               anchor="mm")
            except (ValueError, AttributeError):
                # The bitmap fallback font has no anchor support.
                px, py = self.pt(cx, cy)
                bb = self.draw.textbbox((0, 0), s, font=font)
                self.draw.text((px - (bb[2] - bb[0]) / 2,
                                py - (bb[3] - bb[1]) / 2), s,
                               font=font, fill=color)


def span_image(attrs, cols, rows, cellw, cellh, palette,
               supersample=SUPERSAMPLE, background=None):
    """One span as an RGBA image sized exactly to its cells.

    Transparent by default. `background` fills it instead, which is what
    `gfl shell` needs: there the fallback text is already on screen
    underneath, and it would otherwise show through the chart.

    Returns (image, warning), or (None, None) when the type is unknown:
    SPEC.md says an unimplemented type falls back to its text, so drawing an
    empty rectangle over it would be worse than drawing nothing.
    """
    require_pillow()
    from PIL import Image
    w, h = cols * cellw, rows * cellh
    if w <= 0 or h <= 0:
        return None, None
    shapes = shapes_for(attrs, (0, 0, cols * CW, rows * CHH))
    if not shapes:
        return None, None
    s = supersample
    canvas = Canvas((w * s, h * s), cellw * s / CW, cellh * s / CHH, palette)
    for shape in shapes:
        canvas.add(shape)
    img = canvas.img.resize((w, h), Image.LANCZOS)
    if background is not None:
        plate = Image.new("RGBA", img.size, tuple(background) + (255,))
        plate.alpha_composite(img)
        img = plate
    return img, canvas.warning


def span_png(attrs, cols, rows, cellw, cellh, palette,
             supersample=SUPERSAMPLE, background=None):
    """span_image as PNG bytes, for transmission over the wire."""
    img, warn = span_image(attrs, cols, rows, cellw, cellh, palette,
                           supersample, background)
    if img is None:
        return None, None
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), warn


# ------------------------------------------------------------ whole frames


def block_rect(ch, x, y, w, h):
    """The filled rectangle for a block element, in cell coordinates.

    Block elements are geometry, not typography: they are defined as exact
    fractions of the cell, and a terminal tiles them seamlessly. Drawing
    them as glyphs at a font size that only approximates the cell leaves
    seams between them, which turns the heat grid into a smear. Returns
    None for anything that is not a block element.
    """
    o = ord(ch)
    if o == 0x2588:                                  # full block
        return (x, y, x + w, y + h)
    if 0x2581 <= o <= 0x2587:                        # lower eighths
        frac = (o - 0x2580) / 8.0
        return (x, y + h * (1 - frac), x + w, y + h)
    if o == 0x2580:                                  # upper half
        return (x, y, x + w, y + h / 2)
    if o == 0x2590:                                  # right half
        return (x + w / 2, y, x + w, y + h)
    if 0x2589 <= o <= 0x258F:                        # left eighths
        frac = (0x2590 - o) / 8.0
        return (x, y, x + w * frac, y + h)
    return None


#: Braille dot to (column, row) in the 2 x 4 matrix, by bit.
_BRAILLE_DOTS = [(0, 0), (0, 1), (0, 2), (1, 0),
                 (1, 1), (1, 2), (0, 3), (1, 3)]


def braille_dots(ch, x, y, w, h):
    """Dot rectangles for a braille cell, or None if `ch` is not braille.

    Same reasoning as block_rect: the scatter's resolution comes from these
    dots landing on an exact 2 x 4 grid within the cell.
    """
    o = ord(ch)
    if not 0x2800 <= o <= 0x28FF:
        return None
    bits = o - 0x2800
    dw, dh = w / 2.0, h / 4.0
    r = min(dw, dh) * 0.38
    out = []
    for i, (cx, cy) in enumerate(_BRAILLE_DOTS):
        if bits & (1 << i):
            mx = x + (cx + 0.5) * dw
            my = y + (cy + 0.5) * dh
            out.append((mx - r, my - r, mx + r, my + r))
    return out


def draw_text_grid(img, grid, cellw, cellh, palette, fonts,
                   hide=frozenset()):
    """Paint a character grid onto an image.

    `grid` maps (row, col) to (char, fg, bg), where the colours are either
    hex strings (what render.parse produces) or RGB tuples. `fonts` is a
    FontSet, so each cell is drawn with a face that actually has its glyph.
    Shared with the terminal simulator so there is one implementation of
    what text looks like next to the graphics.
    """
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    default = palette["fg"]
    if not isinstance(fonts, FontSet):          # a bare font still works
        fonts = _SingleFont(fonts)
    for (r, c), cell in sorted(grid.items()):
        if (r, c) in hide:
            continue
        ch, fg, bg = cell
        x, y = c * cellw, r * cellh
        if bg:
            draw.rectangle([x, y, x + cellw - 1, y + cellh - 1],
                           fill=parse_color(bg))
        if not ch or ch == " ":
            continue
        ink = parse_color(fg) or default
        rect = block_rect(ch, x, y, cellw, cellh)
        if rect is not None:
            draw.rectangle(rect, fill=ink)
            continue
        dots = braille_dots(ch, x, y, cellw, cellh)
        if dots is not None:
            for dot in dots:
                draw.ellipse(dot, fill=ink)
            continue
        draw.text((x, y + cellh // 2), ch, font=fonts.draw_with(ch),
                  fill=ink, anchor="lm")


class _SingleFont:
    def __init__(self, font):
        self.primary = font

    def draw_with(self, ch):
        return self.primary


def frame_png(stream, cellw=10, cellh=20, palette=None, enhanced=True,
              pad=12, supersample=SUPERSAMPLE):
    """Compose a whole stream, text and spans together, as PNG bytes.

    This is what a graphics-capable terminal shows, produced without one.
    `enhanced=False` gives the fallback view, which is what every terminal
    shows today.
    """
    require_pillow()
    from PIL import Image
    palette = palette or build_palette()
    grid, spans, nrows = parse(stream)
    ncols = max((c for _, c in grid), default=0) + 1
    hide = set()
    if enhanced:
        for sp in spans:
            hide |= {(r, c) for r, c, _ in sp["cells"]}

    inner = Image.new("RGBA", (ncols * cellw, nrows * cellh),
                      tuple(palette["bg"]) + (255,))
    fonts = FontSet(max(1, int(round(cellh * 0.82))))
    warning = fonts.warning
    draw_text_grid(inner, grid, cellw, cellh, palette, fonts, hide)

    if enhanced:
        for sp in spans:
            bb = cell_bbox(sp["cells"])
            if bb is None:
                continue
            r0, c0, nr, nc = bb
            img, warn = span_image(attrs_dict(sp["attrs"]), nc, nr,
                                   cellw, cellh, palette, supersample)
            warning = warning or warn
            if img is not None:
                inner.alpha_composite(img, (c0 * cellw, r0 * cellh))

    out = Image.new("RGB", (inner.width + 2 * pad, inner.height + 2 * pad),
                    tuple(palette["bg"]))
    out.paste(inner, (pad, pad), inner)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), warning
