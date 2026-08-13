"""gracefall.view: paint spans as real graphics inside today's terminals.

No terminal implements OSC 4700 yet. This shim stands in for one: it reads a
stream, works out where each span's cells landed, rasterizes the span's
geometry, and places the image over those exact cells using the kitty
graphics protocol, which Ghostty, kitty, and WezTerm already speak.

    gfl demo --force-osc | gfl view

The geometry comes from shapes.py, the same source the SVG renderer uses, so
this cannot drift from the reference rendering. Nothing here changes the wire
format, and a terminal that cannot do graphics still gets the fallback text
untouched, which is the whole point of the protocol.

Everything that decides *what bytes to write* is a pure function so it can be
tested without a terminal. Only the probe and the metric queries touch a tty.

Coordinate spaces
-----------------
shapes.py geometry is authored in abstract pixels where one cell is 12 x 24
(the constants in render.py). Real cells are not that size, so the raster
backend scales abstract units to device pixels rather than asking shapes.py
for a differently sized box: the insets and bar heights in the geometry are
absolute, so re-boxing would distort them.
"""

import base64
import io
import os
import re
import sys

from .render import CHH, CW, attrs_dict, parse
from .shapes import cell_bbox, flatten, catmull_rom, shapes_for

#: kitty transmits at most this much base64 per escape sequence.
CHUNK = 4096

#: Fallback when the terminal will not say how big a cell is.
DEFAULT_CELL = (10, 20)

SUPERSAMPLE = 4

FONTS = [
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
]

_SGR = re.compile(r"\x1b\[([0-9;]*)m")


# ---------------------------------------------------------------- palette


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def build_palette(bg=None):
    """Resolve SPEC.md roles plus the two pseudo-roles shapes.py can emit.

    `track` is mixed from the background rather than hardcoded so the meter
    groove sits correctly on a light theme as well as a dark one.
    """
    from . import ROLE_RGB
    pal = dict(ROLE_RGB)
    pal["bg"] = bg if bg else (16, 19, 26)
    pal["track"] = mix(pal["bg"], pal["fg"], 0.14)
    return pal


def parse_color(s):
    """Accept #rgb, #rrggbb, and the rgb:RRRR/GGGG/BBBB of an OSC 11 reply."""
    if not s:
        return None
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


# ------------------------------------------------------- pure byte layout


def place_moves(row0, nrows, col0):
    """Cursor moves to reach a span's top-left cell and to come back.

    Called with the cursor at column 0 of the line *after* the block, which
    is where printing the text leaves it.
    """
    up = nrows - row0
    before = (f"\x1b[{up}A" if up > 0 else "") + (f"\x1b[{col0}C"
                                                  if col0 > 0 else "")
    after = "\r" + (f"\x1b[{up}B" if up > 0 else "")
    return before, after


def apc_chunks(payload_b64, control, chunk_size=CHUNK):
    """Split a base64 payload into kitty graphics escape sequences.

    The first sequence carries the control keys; `m=1` means more follows and
    `m=0` ends the transmission. A payload that fits in one sequence carries
    no `m` at all, which is what kitty expects for the unchunked case.
    """
    if not payload_b64:
        return []
    parts = [payload_b64[i:i + chunk_size]
             for i in range(0, len(payload_b64), chunk_size)]
    out = []
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if i == 0:
            keys = control if last else control + ",m=1"
        else:
            keys = "m=0" if last else "m=1"
        out.append(f"\x1b_G{keys};{part}\x1b\\")
    return out


def image_sequence(png, cols, rows, placement="over"):
    """The full escape sequence run that places one PNG over `cols` x `rows`
    cells. C=1 leaves the cursor alone, q=2 suppresses the terminal's reply
    so it never leaks into the next command's stdin."""
    control = f"a=T,f=100,c={cols},r={rows},C=1,q=2"
    if placement == "under":
        control += ",z=-1"
    return "".join(apc_chunks(base64.b64encode(png).decode("ascii"), control))


def compose_text(grid, nrows, hide=frozenset()):
    """Rebuild the stream's visible text, blanking the cells `hide` covers.

    Cells under a span become plain spaces so nothing shows through the
    image, while every other cell keeps its colors: the surrounding labels
    and numbers are ordinary text and must stay ordinary text.
    """
    rows = {}
    for (r, c), cell in grid.items():
        rows.setdefault(r, {})[c] = cell
    out = []
    for r in range(nrows):
        cells = rows.get(r, {})
        line = []
        fg = bg = None
        for c in range(max(cells) + 1 if cells else 0):
            ch, cfg, cbg = cells.get(c, (" ", None, None))
            if (r, c) in hide:
                ch, cfg, cbg = " ", None, None
            if (cfg, cbg) != (fg, bg):
                line.append("\x1b[0m")
                if cfg:
                    line.append(_truecolor(cfg, 38))
                if cbg:
                    line.append(_truecolor(cbg, 48))
                fg, bg = cfg, cbg
            line.append(ch)
        if fg or bg:
            line.append("\x1b[0m")
        out.append("".join(line).rstrip())
    return "\n".join(out)


def _truecolor(hexs, base):
    r, g, b = parse_color(hexs) or (0, 0, 0)
    return f"\x1b[{base};2;{r};{g};{b}m"


def parse_cell_size_reply(s):
    """CSI 16 t answers with CSI 6 ; height ; width t."""
    m = re.search(r"\x1b\[6;(\d+);(\d+)t", s)
    return (int(m.group(2)), int(m.group(1))) if m else None


# -------------------------------------------------------- capability probe


def backend_from_env(env):
    """Recognize a graphics-capable terminal without talking to it.

    Cheap, and it is the only detection available when stdin is not a tty,
    which is the normal case for `gfl demo | gfl view`.
    """
    if env.get("KITTY_WINDOW_ID") or env.get("GHOSTTY_RESOURCES_DIR"):
        return "env"
    if (env.get("TERM_PROGRAM") or "").lower() in ("ghostty", "wezterm"):
        return "env"
    if (env.get("TERM") or "").startswith("xterm-kitty"):
        return "env"
    return None


def probe_kitty(timeout=0.3):
    """Ask the terminal whether it speaks the kitty graphics protocol.

    Sends a 1x1 query plus a device attributes request. Every terminal
    answers DA, so a DA reply with no graphics reply is a definite no rather
    than a timeout. Returns None when there is no tty to ask.
    """
    try:
        import termios
        import tty
    except ImportError:
        return None
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return None
    try:
        tty.setraw(fd)
        os.write(fd, b"\x1b_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA\x1b\\\x1b[c")
        reply = _read_until(fd, timeout, lambda s: s.endswith("c"))
        return "_Gi=31" in reply
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)


def query_terminal(request, terminator, timeout=0.2):
    """Write a query to the tty and read its reply. Returns "" if there is no
    tty or the terminal stays quiet."""
    try:
        import termios
        import tty
    except ImportError:
        return ""
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return ""
    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        os.close(fd)
        return ""
    try:
        tty.setraw(fd)
        os.write(fd, request)
        return _read_until(fd, timeout, terminator)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        os.close(fd)


def _read_until(fd, timeout, done):
    import select
    import time
    buf = ""
    deadline = time.monotonic() + timeout
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return buf
        r, _, _ = select.select([fd], [], [], left)
        if not r:
            return buf
        try:
            chunk = os.read(fd, 1024)
        except OSError:
            return buf
        if not chunk:
            return buf
        buf += chunk.decode("utf-8", "replace")
        if callable(done) and done(buf):
            return buf


def cell_metrics():
    """Pixel size of one cell, and where the number came from.

    A wrong guess makes the output blurry, never broken, so the fallback is
    a plausible default rather than an error.
    """
    try:
        import fcntl
        import struct
        import termios
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            try:
                packed = fcntl.ioctl(stream.fileno(), termios.TIOCGWINSZ,
                                     b"\0" * 8)
            except (OSError, ValueError, AttributeError):
                continue
            rows, cols, xpix, ypix = struct.unpack("HHHH", packed)
            if xpix and ypix and rows and cols:
                return xpix // cols, ypix // rows, "ioctl"
    except ImportError:
        pass
    got = parse_cell_size_reply(query_terminal(b"\x1b[16t",
                                               lambda s: s.endswith("t")))
    if got:
        return got[0], got[1], "CSI 16 t"
    return DEFAULT_CELL[0], DEFAULT_CELL[1], "default"


def background_color():
    reply = query_terminal(b"\x1b]11;?\x1b\\",
                           lambda s: s.endswith("\x1b\\") or s.endswith("\a"))
    m = re.search(r"rgb:[0-9a-fA-F/]+", reply)
    return parse_color(m.group(0)) if m else None


# ------------------------------------------------------------ rasterizing


def _require_pillow():
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError:
        raise SystemExit(
            'gfl view needs Pillow: pip install "gracefall[view]"')


def _load_font(size):
    from PIL import ImageFont
    for path in FONTS:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), None
            except OSError:
                continue
    return ImageFont.load_default(), (
        "no monospace font found, flow labels will look wrong")


def _rgba(palette, role, alpha):
    r, g, b = palette.get(role, palette["blue"])
    return (r, g, b, max(0, min(255, int(round(alpha * 255)))))


def _gradient(size, bbox, palette, paint):
    """An RGBA image whose alpha ramps across bbox, for lgrad paints.

    Built as a one pixel wide ramp and stretched, which is both far faster
    than a per-pixel loop and, unlike getchannel("A"), actually attached to
    the image: getchannel returns a copy, so writing through it produces a
    fully transparent gradient and silently drops the shape.
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
        t = i / steps
        v = int(round((a0 + (a1 - a0) * t) * 255))
        load[(0, i) if vertical else (i, 0)] = max(0, min(255, v))
    alpha = Image.new("L", size, int(round(max(a0, a1) * 255)))
    # Clamp outside the ramp the way an SVG gradient does: hold the end stops.
    edge_lo = Image.new("L", size, int(round(a0 * 255)))
    alpha.paste(edge_lo, (0, 0))
    alpha.paste(ramp.resize((size[0], steps) if vertical
                            else (steps, size[1])),
                (0, int(round(lo))) if vertical else (int(round(lo)), 0))
    if vertical and round(lo) + steps < size[1]:
        alpha.paste(Image.new("L", (size[0], size[1] - round(lo) - steps),
                              int(round(a1 * 255))),
                    (0, round(lo) + steps))
    elif not vertical and round(lo) + steps < size[0]:
        alpha.paste(Image.new("L", (size[0] - round(lo) - steps, size[1]),
                              int(round(a1 * 255))),
                    (round(lo) + steps, 0))
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


class _Canvas:
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
        """Run `drawer(target_draw, color)` for solids, or build a mask and
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
        if kind == "line":
            _, x1, y1, x2, y2, paint, w, dash = shape
            p1, p2 = self.pt(x1, y1), self.pt(x2, y2)
            segs = (_dash(p1, p2, dash[0] * self.sx, dash[1] * self.sx)
                    if dash else [(p1, p2)])
            for a, b in segs:
                self._fill(lambda d, c, a=a, b=b:
                           d.line([a, b], fill=c, width=self.width(w)),
                           paint, (0, 0) + self.img.size)
        elif kind == "polyline":
            _, pts, paint, w = shape
            pp = self.pts(pts)
            self._fill(lambda d, c: d.line(pp, fill=c, width=self.width(w),
                                           joint="curve"),
                       paint, (0, 0) + self.img.size)
        elif kind == "curve":
            _, pts, paint, w = shape
            pp = self.pts(flatten(*catmull_rom(pts)))
            self._fill(lambda d, c: d.line(pp, fill=c, width=self.width(w),
                                           joint="curve"),
                       paint, (0, 0) + self.img.size)
        elif kind == "area":
            _, pts, paint, ybase = shape
            poly = self.pts(flatten(*catmull_rom(pts)))
            poly.append(self.pt(pts[-1][0], ybase))
            poly.append(self.pt(pts[0][0], ybase))
            ys = [p[1] for p in poly]
            xs = [p[0] for p in poly]
            self._fill(lambda d, c: d.polygon(poly, fill=c), paint,
                       (min(xs), min(ys), max(xs), max(ys)))
        elif kind == "rrect":
            _, x, y, w, h, rx, fill, stroke, sw = shape
            x0, y0 = self.pt(x, y)
            x1, y1 = self.pt(x + w, y + h)
            r = min(rx * self.sy, (y1 - y0) / 2, (x1 - x0) / 2)
            box = [x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)]
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
            font, warn = _load_font(max(1, int(round(size * self.sy))))
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


def render_span_png(attrs, cols, rows, cellw, cellh, palette,
                    supersample=SUPERSAMPLE):
    """Rasterize one span to PNG bytes sized exactly to its cells."""
    _require_pillow()
    from PIL import Image
    w, h = cols * cellw, rows * cellh
    if w <= 0 or h <= 0:
        return None, None
    box = (0, 0, cols * CW, rows * CHH)
    shapes = shapes_for(attrs, box)
    if not shapes:
        return None, None
    s = supersample
    canvas = _Canvas((w * s, h * s), cellw * s / CW, cellh * s / CHH,
                     palette)
    for shape in shapes:
        canvas.add(shape)
    img = canvas.img.resize((w, h), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), canvas.warning


# ------------------------------------------------------------------ paint


def build_output(stream, cellw, cellh, palette, placement="over"):
    """Return (text_to_write, span_report). Pure: no tty, no Pillow calls
    beyond rasterizing, so it can be exercised in tests."""
    grid, spans, nrows = parse(stream)
    hide = set()
    if placement == "over":
        for sp in spans:
            hide |= {(r, c) for r, c, _ in sp["cells"]}
    out = [compose_text(grid, nrows, hide), "\n"]
    report, warning = [], None
    for sp in spans:
        bb = cell_bbox(sp["cells"])
        a = attrs_dict(sp["attrs"])
        if bb is None:
            report.append((a.get("t"), None, "empty span"))
            continue
        r0, c0, nr, nc = bb
        png, warn = render_span_png(a, nc, nr, cellw, cellh, palette)
        warning = warning or warn
        if png is None:
            report.append((a.get("t"), bb, "no shapes, fallback kept"))
            continue
        before, after = place_moves(r0, nrows, c0)
        out.append(before)
        out.append(image_sequence(png, nc, nr, placement))
        out.append(after)
        report.append((a.get("t"), bb, f"{len(png)} B"))
    out.append("\x1b[0m")
    return "".join(out), report, warning


def run(stream, args, out=None, env=None, stderr=None):
    """The `gfl view` entry point."""
    out = out if out is not None else sys.stdout
    err = stderr if stderr is not None else sys.stderr
    env = env if env is not None else os.environ

    backend = backend_from_env(env)
    if backend is None and not args.no_probe:
        backend = "probe" if probe_kitty() else None

    if backend is None:
        out.write(stream if stream.endswith("\n") else stream + "\n")
        print("gfl view: this terminal does not speak the kitty graphics "
              "protocol, so the fallback text above is what you get. Try "
              "Ghostty, kitty, or WezTerm.", file=err)
        return 0

    # Fail before drawing anything, not halfway through a repaint.
    _require_pillow()

    cellw, cellh, source = cell_metrics()
    if args.cell:
        try:
            cw, chh = (int(v) for v in args.cell.lower().split("x"))
            cellw, cellh, source = cw, chh, "--cell"
        except ValueError:
            raise SystemExit("--cell wants WIDTHxHEIGHT, for example 10x20")
    palette = build_palette(background_color())

    text, report, warning = build_output(stream, cellw, cellh, palette,
                                         args.placement)
    if args.stats:
        print(f"backend:  {backend}", file=err)
        print(f"cell:     {cellw}x{cellh} px (from {source})", file=err)
        print(f"palette:  bg {palette['bg']}", file=err)
        print(f"spans:    {len(report)}", file=err)
        for t, bb, note in report:
            print(f"  {t:8s} {str(bb):22s} {note}", file=err)
    if warning:
        print(f"gfl view: {warning}", file=err)
    out.write(text + "\n")
    return 0
