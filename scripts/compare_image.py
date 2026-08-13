#!/usr/bin/env python3
"""Regenerate docs/compare.png from the real pipeline.

The README's headline image claims one byte stream produces two renderings.
This builds that claim out of the shipping code: both panels come from the
same `.gfall` file through `raster.frame_png`, which is the same backend
`gfl view` paints with, so the picture cannot flatter the implementation.

    python scripts/compare_image.py

Left is what every terminal shows today. Right is what a terminal
implementing OSC 4700 shows.
"""

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from gracefall.raster import build_palette, frame_png, load_font  # noqa: E402

BG = (10, 12, 17)
LABEL = (150, 160, 180)
ACCENT = (95, 227, 192)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stream", default=str(ROOT / "examples" /
                                            "inference.gfall"))
    ap.add_argument("--out", default=str(ROOT / "docs" / "compare.png"))
    ap.add_argument("--cell", default="16x34")
    ap.add_argument("--gap", type=int, default=48)
    args = ap.parse_args()

    from PIL import Image, ImageDraw
    cellw, cellh = (int(v) for v in args.cell.lower().split("x"))
    stream = pathlib.Path(args.stream).read_text(encoding="utf-8")
    palette = build_palette(BG)

    panels = []
    for enhanced in (False, True):
        data, warn = frame_png(stream, cellw, cellh, palette,
                               enhanced=enhanced, pad=28)
        if warn:
            print(f"note: {warn}")
        panels.append(Image.open(__import__("io").BytesIO(data)))

    font, _ = load_font(max(14, cellh // 2))
    head = cellh + 16
    w = panels[0].width + args.gap + panels[1].width
    h = head + max(p.height for p in panels)
    out = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(out)

    out.paste(panels[0], (0, head))
    out.paste(panels[1], (panels[0].width + args.gap, head))
    draw.text((28, 14), "every terminal today", font=font, fill=LABEL)
    draw.text((panels[0].width + args.gap + 28, 14),
              "a terminal that implements OSC 4700", font=font, fill=ACCENT)

    out.save(args.out)
    print(f"wrote {args.out}  {out.size[0]}x{out.size[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
