#!/usr/bin/env python3
"""Pixel-diff the renderer's output against another git ref.

The point of gracefall is that one stream produces two renderings from one
source of truth. Refactors that "look the same" are exactly the ones that
quietly move a curve or blunt an arrowhead, so this compares real rasterized
pixels rather than trusting an eyeball or an SVG string diff.

    python scripts/visual_diff.py --ref v0.1.1

Renders the same stream twice, once with the working tree and once with the
code at REF checked out into a throwaway git worktree, rasterizes both, and
reports the differing pixel count for the enhanced and the fallback view.
Exits nonzero if anything moved.

Needs Pillow and a rasterizer. Run it through `make visual-diff`, which
supplies Pillow via uv.
"""

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Rendered by a subprocess so the ref's code is imported in a clean
# interpreter instead of fighting with an already-imported gracefall.
_RENDER = """
import pathlib, sys
from gracefall.render import render
stream = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
pathlib.Path(sys.argv[2]).write_text(
    render(stream, enhanced=True), encoding="utf-8")
pathlib.Path(sys.argv[3]).write_text(
    render(stream, enhanced=False), encoding="utf-8")
"""

CHROMES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome", "chromium", "chromium-browser",
]


def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True,
                          **kw)


def render_with(src_root, stream, out_dir, label):
    """Write <label>.svg and <label>.plain.svg using the code under src_root."""
    out_dir.mkdir(parents=True, exist_ok=True)
    enhanced = out_dir / f"{label}.svg"
    plain = out_dir / f"{label}.plain.svg"
    env = dict(os.environ, PYTHONPATH=str(src_root))
    _run([sys.executable, "-c", _RENDER, str(stream), str(enhanced),
          str(plain)], env=env)
    return enhanced, plain


def svg_size(path):
    head = path.read_text(encoding="utf-8")[:4000]
    m = re.search(r'width="(\d+)"\s+height="(\d+)"', head)
    if not m:
        raise SystemExit(f"no width/height in {path}")
    return int(m.group(1)), int(m.group(2))


def find_rasterizer():
    """Return (name, callable). Exact converters first; a browser is the
    fallback because its output depends on the local install."""
    if shutil.which("rsvg-convert"):
        def rsvg(svg, png, w, h):
            _run(["rsvg-convert", "-w", str(w), "-h", str(h),
                  str(svg), "-o", str(png)])
        return "rsvg-convert", rsvg
    try:
        import cairosvg  # noqa: F401

        def cairo(svg, png, w, h):
            import cairosvg
            cairosvg.svg2png(url=str(svg), write_to=str(png),
                             output_width=w, output_height=h)
        return "cairosvg", cairo
    except ImportError:
        pass
    for cand in CHROMES:
        exe = cand if os.path.exists(cand) else shutil.which(cand)
        if not exe:
            continue

        def chrome(svg, png, w, h, _exe=exe):
            subprocess.run(
                [_exe, "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=1", f"--screenshot={png}",
                 f"--window-size={w},{h}", svg.as_uri()],
                capture_output=True)
            if not pathlib.Path(png).exists():
                raise SystemExit(f"{_exe} produced no screenshot")
        return pathlib.Path(exe).name, chrome
    return None, None


def compare(png_a, png_b, diff_png):
    from PIL import Image, ImageChops
    a = Image.open(png_a).convert("RGB")
    b = Image.open(png_b).convert("RGB")
    if a.size != b.size:
        return None, None, f"size mismatch {a.size} vs {b.size}"
    d = ImageChops.difference(a, b)
    # get_flattened_data replaced getdata in Pillow 11; support both.
    getter = getattr(d, "get_flattened_data", None) or d.getdata
    px = list(getter())
    n = sum(1 for p in px if any(p))
    worst = max(max(p) for p in px)
    if n:
        d.point(lambda v: min(255, v * 8)).save(diff_png)
    return n, worst, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ref", default="HEAD",
                    help="git ref to compare the working tree against")
    ap.add_argument("--stream", default=str(ROOT / "examples" /
                                            "inference.gfall"))
    ap.add_argument("--out", default=str(ROOT / "build" / "visual-diff"))
    ap.add_argument("--max-diff", type=int, default=0,
                    help="differing pixels tolerated per view")
    ap.add_argument("--skip-missing", action="store_true",
                    help="exit 0 instead of failing when no rasterizer")
    args = ap.parse_args()

    name, raster = find_rasterizer()
    if raster is None:
        msg = ("no rasterizer found. Install librsvg (brew install librsvg), "
               "or cairosvg, or Chrome.")
        if args.skip_missing:
            print(f"SKIP: {msg}")
            return 0
        raise SystemExit(msg)

    out = pathlib.Path(args.out)
    stream = pathlib.Path(args.stream).resolve()
    if out.exists():
        shutil.rmtree(out)
    print(f"stream:     {stream.relative_to(ROOT)}")
    print(f"rasterizer: {name}")

    cur_svgs = render_with(ROOT / "src", stream, out, "working")
    with tempfile.TemporaryDirectory() as tmp:
        wt = pathlib.Path(tmp) / "wt"
        try:
            _run(["git", "worktree", "add", "--detach", "-f", str(wt),
                  args.ref], cwd=ROOT)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"cannot check out {args.ref!r}:\n{e.stderr}")
        try:
            sha = _run(["git", "rev-parse", "--short", "HEAD"],
                       cwd=wt).stdout.strip()
            print(f"comparing:  working tree vs {args.ref} ({sha})\n")
            ref_svgs = render_with(wt / "src", stream, out, "ref")
        finally:
            _run(["git", "worktree", "remove", "--force", str(wt)], cwd=ROOT)

    failed = False
    for view, cur, ref in zip(("enhanced", "fallback"), cur_svgs, ref_svgs):
        w, h = svg_size(cur)
        cur_png = out / f"working.{view}.png"
        ref_png = out / f"ref.{view}.png"
        raster(cur, cur_png, w, h)
        raster(ref, ref_png, w, h)
        n, worst, err = compare(ref_png, cur_png, out / f"diff.{view}.png")
        if err:
            print(f"  {view:9s} FAIL  {err}")
            failed = True
        elif n > args.max_diff:
            print(f"  {view:9s} FAIL  {n} differing pixels, "
                  f"max channel delta {worst}")
            print(f"            see {out / f'diff.{view}.png'}")
            failed = True
        else:
            print(f"  {view:9s} ok    {n} differing pixels")
    if failed:
        print("\nRendering moved. If that was intended, say so explicitly "
              "in the commit message.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
