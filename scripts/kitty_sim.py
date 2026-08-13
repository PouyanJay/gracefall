#!/usr/bin/env python3
"""Play the role of a kitty-graphics terminal, for verifying `gfl view`.

render.py already plays terminal for the OSC 4700 side. This does the same
for the shim's output: it consumes exactly the bytes `gfl view` writes,
applies the cursor movements, reassembles the chunked base64 images, and
composites everything into a PNG.

That makes the two things that are silently wrong rather than loudly broken
checkable without a GPU terminal and without screen capture: whether an
image lands on the cells its span actually occupied, and whether the chunk
boundaries reassemble into a valid PNG.

    python scripts/kitty_sim.py --out build/view-sim.png

It is a verification oracle, not a terminal. It implements only the escape
sequences the shim emits, and treats anything else as a hard error rather
than ignoring it, because silently skipping a sequence would hide the very
bug this exists to catch.
"""

import argparse
import base64
import io
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gracefall.view import FONTS  # noqa: E402

_SGR = re.compile(r"\x1b\[([0-9;]*)m")
_CSI = re.compile(r"\x1b\[(\d*)([ABCDG])")
_APC = re.compile(r"\x1b_G([^;]*);([^\x1b]*)\x1b\\")


class Term:
    """A character grid plus a list of placed images."""

    def __init__(self):
        self.grid = {}
        self.images = []
        self.row = self.col = 0
        self.fg = (222, 227, 236)
        self.bg = None
        self._chunks = []
        self._control = None

    def feed(self, s):
        i, n = 0, len(s)
        while i < n:
            c = s[i]
            if c == "\x1b":
                m = _APC.match(s, i)
                if m:
                    self._image(m.group(1), m.group(2))
                    i = m.end()
                    continue
                m = _SGR.match(s, i)
                if m:
                    self._sgr(m.group(1))
                    i = m.end()
                    continue
                m = _CSI.match(s, i)
                if m:
                    self._move(m.group(1), m.group(2))
                    i = m.end()
                    continue
                raise SystemExit(
                    f"unhandled escape at {i}: {s[i:i + 20]!r}")
            if c == "\n":
                self.row += 1
                self.col = 0
            elif c == "\r":
                self.col = 0
            else:
                self.grid[(self.row, self.col)] = (c, self.fg, self.bg)
                self.col += 1
            i += 1

    def _sgr(self, params):
        p = [int(x) for x in params.split(";") if x] or [0]
        j = 0
        while j < len(p):
            if p[j] == 0:
                self.fg, self.bg = (222, 227, 236), None
            elif p[j] == 38 and j + 4 < len(p):
                self.fg = tuple(p[j + 2:j + 5])
                j += 4
            elif p[j] == 48 and j + 4 < len(p):
                self.bg = tuple(p[j + 2:j + 5])
                j += 4
            j += 1

    def _move(self, num, kind):
        n = int(num) if num else 1
        if kind == "A":
            self.row = max(0, self.row - n)
        elif kind == "B":
            self.row += n
        elif kind == "C":
            self.col += n
        elif kind == "D":
            self.col = max(0, self.col - n)
        elif kind == "G":
            self.col = max(0, n - 1)

    def _image(self, keys, payload):
        kv = dict(p.split("=", 1) for p in keys.split(",")
                  if "=" in p) if keys else {}
        if self._control is None:
            self._control = kv
        self._chunks.append(payload)
        if kv.get("m") == "1":
            return
        ctrl = self._control
        data = base64.b64decode("".join(self._chunks))
        self._chunks, self._control = [], None
        if ctrl.get("a") != "T":
            raise SystemExit(f"unexpected graphics action: {ctrl}")
        self.images.append({
            "row": self.row, "col": self.col,
            "cols": int(ctrl.get("c", 0)), "rows": int(ctrl.get("r", 0)),
            "z": int(ctrl.get("z", 0)), "png": data,
        })
        # C=1 promises the cursor does not move.
        if ctrl.get("C") != "1":
            raise SystemExit("shim must set C=1 or the layout will drift")


def compose(term, cellw, cellh, bg=(16, 19, 26)):
    from PIL import Image, ImageDraw, ImageFont
    rows = max([r for r, _ in term.grid] + [i["row"] + i["rows"] - 1
                                            for i in term.images] + [0]) + 1
    cols = max([c for _, c in term.grid] + [i["col"] + i["cols"]
                                            for i in term.images] + [0]) + 1
    img = Image.new("RGB", (cols * cellw, rows * cellh), bg)
    draw = ImageDraw.Draw(img)
    font = None
    for path in FONTS:
        if pathlib.Path(path).exists():
            try:
                font = ImageFont.truetype(path, int(cellh * 0.82))
                break
            except OSError:
                continue
    font = font or ImageFont.load_default()

    below = [i for i in term.images if i["z"] < 0]
    above = [i for i in term.images if i["z"] >= 0]
    for spec in below:
        _paste(img, spec, cellw, cellh)
    for (r, c), (ch, fg, cbg) in sorted(term.grid.items()):
        x, y = c * cellw, r * cellh
        if cbg:
            draw.rectangle([x, y, x + cellw - 1, y + cellh - 1], fill=cbg)
        if ch != " ":
            draw.text((x, y + cellh // 2), ch, font=font, fill=fg,
                      anchor="lm")
    for spec in above:
        _paste(img, spec, cellw, cellh)
    return img


def _paste(img, spec, cellw, cellh):
    from PIL import Image
    im = Image.open(io.BytesIO(spec["png"])).convert("RGBA")
    want = (spec["cols"] * cellw, spec["rows"] * cellh)
    if im.size != want:
        print(f"  note: image is {im.size}, cell box is {want}; "
              f"a real terminal would scale it")
        im = im.resize(want, Image.LANCZOS)
    img.paste(im, (spec["col"] * cellw, spec["row"] * cellh), im)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "build" / "view-sim.png"))
    ap.add_argument("--cell", default="10x20")
    ap.add_argument("--placement", default="over",
                    choices=["over", "under"])
    ap.add_argument("--input", help="file of gfl view output; default is to "
                                    "generate it")
    args = ap.parse_args()
    cellw, cellh = (int(v) for v in args.cell.lower().split("x"))

    # Never let text mode near this stream: universal newline translation
    # rewrites the \r in every return move as \n, which silently walks each
    # image one row further down the screen than the shim asked for.
    if args.input:
        with open(args.input, "r", encoding="utf-8", newline="") as fh:
            data = fh.read()
    else:
        env = {"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin",
               "KITTY_WINDOW_ID": "1"}
        gen = subprocess.run(
            [sys.executable, "-m", "gracefall", "--force-osc", "demo"],
            capture_output=True, cwd=ROOT, env=env)
        view = subprocess.run(
            [sys.executable, "-m", "gracefall", "view", "--cell", args.cell,
             "--placement", args.placement, "--no-probe"],
            input=gen.stdout, capture_output=True, cwd=ROOT, env=env)
        if view.returncode:
            raise SystemExit(view.stderr.decode("utf-8", "replace"))
        data = view.stdout.decode("utf-8")

    term = Term()
    term.feed(data)
    print(f"images placed: {len(term.images)}")
    for spec in term.images:
        print(f"  row {spec['row']:2d} col {spec['col']:2d}  "
              f"{spec['cols']}x{spec['rows']} cells  "
              f"{len(spec['png'])} B")
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    compose(term, cellw, cellh).save(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
