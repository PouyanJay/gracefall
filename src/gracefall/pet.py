"""gracefall.pet: the creature, breathing in place until you press a key.

`gfl pet` is the simplest thing that can be built on top of
`gracefall.creature`: read a few cheap signals, hand them to a Creature,
and repaint one frame four times a second through the same `watch()` loop
every live recipe uses, so it sits under the same margin as every other
chart. In a plain terminal that is block art moving; in a terminal that
implements OSC 4700 it is the drawn creature, because the bytes are the
same either way.

Three rules keep it a screensaver rather than a background job:

- Nothing here touches the network, and nothing costs a process more than
  once every few seconds. Load average is a free read from the kernel;
  the tree's state is asked for at most every `Signals.every` seconds;
  CI is whatever the `ci` hook returns, and the default hook reads an
  environment variable a prompt can export.
- The terminal is put into cbreak so a single keypress is enough to
  leave, and it is restored on every exit path, keypress, ctrl-c or
  exception. If stdin is not a tty there is no key handling at all,
  rather than a failure.
- The last frame stays on screen. Leaving does not clear it, which is
  what makes `gfl pet --once` and the end of an animation the same
  picture.

`--once` prints a single frame and exits, for a test, for a
`PROMPT_COMMAND`, and for a recording.
"""

import os
import select
import subprocess
import sys
import time

from . import strip_spans
from .creature import Creature, mood_for
from .recipes import MARGIN, PET_HZ, frame, watch

__all__ = ["Signals", "cpu_load", "ci_env", "git_dirty", "run",
           "graphics_body"]

#: The floor under `--every`. Fifty frames a second is past the point
#: where another frame shows anything, and a loop faster than that is a
#: busy wait with a creature on it.
MIN_EVERY = 0.02

#: The default frame interval: twenty frames a second.
#:
#: This used to be 0.25, which was chosen to match `recipes.PET_HZ`, the
#: rate the creature's motion is designed at. That conflated two different
#: things. The creature moves at PET_HZ beats a second whatever we do here,
#: because the tick handed to it is measured in beats and taken from the
#: clock; `every` only decides how often that motion is *sampled*. At 0.25
#: it was sampled at exactly the rate it moved, which is the worst case:
#: half the frames landed on the same eighth-block as the one before and
#: were redrawn identically, so the animation stuttered at half the rate
#: it claimed.
DEFAULT_EVERY = 0.05



#: How often the tree may be asked whether it is dirty. A `git status` on
#: a large repository is the one expensive reading here, so it is cached
#: for this many seconds and every frame in between reuses the answer.
DIRTY_EVERY = 5.0

#: How often the signals are read at all. At twenty frames a second,
#: asking the kernel for a load average every frame is twenty syscalls a
#: second to watch a number that updates every five. The creature is
#: interpolated between readings by its tick, not by re-reading.
SIGNALS_EVERY = 0.5


def cpu_load():
    """The machine's load over its core count, as a 0..1 signal.

    The one minute average, not a sample: it needs no interval to measure
    over, so a frame costs one syscall. A load equal to the core count
    reads as a fully busy machine and anything above that is clamped,
    because the creature has no cell for 3.5.
    """
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):     # not every platform has one
        return 0.0
    return max(0.0, min(1.0, load / (os.cpu_count() or 1)))


def git_dirty(root=None):
    """Whether the tree at `root` has uncommitted changes.

    False for anything that is not a git checkout, and false when git is
    missing or slow: a creature on a prompt line may not raise, and may
    not hang either.
    """
    try:
        p = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=2)
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0 and bool(p.stdout.strip())


def ci_env():
    """The CI signal, from the environment and nowhere else.

    This is the hook. A prompt function, a `gh` cache or a CI watcher can
    export `GFL_CI=pass` or `GFL_CI=fail` and the creature reads it on the
    next frame; `gfl pet` itself never asks the network, so it draws at
    the same speed on a plane as it does at a desk.
    """
    v = os.environ.get("GFL_CI", "").strip().lower()
    return v if v in ("pass", "fail") else None


class Signals:
    """The machine's readings, cheap enough to take four times a second.

    `read()` returns the dict `Creature` wants. Everything that costs a
    process is cached behind `every` seconds on the clock the caller
    passes, which is what makes the caching testable without sleeping.
    """

    def __init__(self, root=None, every=DIRTY_EVERY, ci=None,
                 clock=time.monotonic):
        self.root = root
        self.every = every
        self.ci = ci or ci_env
        self._clock = clock
        self._at = None
        self._dirty = False

    def read(self):
        return {"cpu": cpu_load(), "dirty": self.dirty(), "ci": self.ci()}

    def dirty(self):
        """The cached answer, refreshed when `every` seconds have gone."""
        now = self._clock()
        if self._at is None or now - self._at >= self.every:
            self._at = now
            self._dirty = git_dirty(self.root)
        return self._dirty


def key_waiter(fd):
    """A `wait(seconds)` for `watch()` that returns true on a keypress.

    It sleeps in `select`, so a key lands within the poll rather than at
    the end of the frame, and a frame that nobody interrupts costs one
    timed out select. End of file counts as a key: a pet whose input has
    gone away has nobody to watch it.
    """
    def wait(seconds):
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return False
            try:
                ready, _, _ = select.select([fd], [], [], left)
            except (OSError, ValueError):
                return True
            if ready:
                try:
                    os.read(fd, 1024)     # drain it, whatever it was
                except OSError:
                    pass
                return True
    return wait


def _cbreak(fd):
    """Put `fd` into cbreak and return the restore function.

    cbreak leaves ISIG on, so ctrl-c still raises KeyboardInterrupt and
    `watch()` handles it exactly as it does for every other live view.
    Returns None when the fd cannot be put into cbreak at all.
    """
    try:
        import termios
        import tty
    except ImportError:                   # pragma: no cover, not posix
        return None
    try:
        saved = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        return None

    def restore():
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        except Exception:
            pass
    return restore


def _stdin_fd():
    """The tty keys would arrive on, or None when there is not one.

    Not a tty is the ordinary case, not a failure: a pet in a pipeline or
    under a `PROMPT_COMMAND` has no keyboard, and it still draws.
    """
    try:
        return sys.stdin.fileno() if sys.stdin.isatty() else None
    except (AttributeError, ValueError, OSError):
        return None


# --------------------------------------------------------------------------
# the drawn creature
#
# `gfl pet --graphics` is the same creature, the same spans and the same
# numbers, drawn instead of quantized. It exists because the fallback has a
# ceiling that has nothing to do with how often it is redrawn: thirteen
# cells with eight vertical steps each is the entire resolution available,
# so an arm moving a hundredth of a cell renders as the arm not moving.
# Sampling that faster produces more identical frames, not smoother motion.
#
# Nothing about the stream changes here. The creature emits exactly the
# bytes it always emitted, and this reads them back the way `gfl view`
# does, which is why the drawn creature cannot disagree with the text one:
# both come from the same spans through shapes.py.


def graphics_body(png, cols, rows, nrows, row0, col0):
    """The escape sequence run that places one image over the creature's
    cells, given the cursor is at column 0 of the line after the block.

    Pure, so the cursor arithmetic is a unit test rather than something
    that is only ever checked by looking at a terminal.
    """
    from .view import image_sequence, place_moves
    before, after = place_moves(row0, nrows, col0)
    return before + image_sequence(png, cols, rows, "over") + after


def _graphics_painter(size, env, err):
    """A `paint(lines) -> body` for this terminal, or None when it cannot
    show an image and the caller should draw text instead.

    Every reason to decline is checked once, here, before the first frame:
    a loop that discovers halfway through that it cannot draw has already
    blanked the cells it was going to draw over.
    """
    from . import view
    backend = view.backend_from_env(env)
    if backend is None:
        backend = "probe" if view.probe_kitty() else None
    if backend is None:
        print(f"gfl pet: {view.describe_terminal(env)} does not speak the "
              f"kitty graphics protocol, so the creature is drawn as text. "
              f"For the drawn one, run this in Ghostty, kitty or WezTerm.",
              file=err)
        return None
    warning = view.tmux_passthrough_warning(env)
    if warning and not env.get("GRACEFALL_TMUX_OK"):
        # tmux would eat the images and leave the blanked cells behind,
        # which is strictly worse than the text the blanking replaced.
        print(f"gfl pet: {warning}", file=err)
        print("gfl pet: drawing the creature as text instead. Set "
              "GRACEFALL_TMUX_OK=1 to draw it anyway.", file=err)
        return None

    from .raster import block_png, build_palette, require_pillow
    require_pillow()
    cellw, cellh, _ = view.cell_metrics()
    palette = build_palette(view.background_color())

    def paint(lines):
        """One frame: blank cells for the creature to be drawn over, the
        hint under it, and the image placed on top."""
        png, cols, rows, _ = block_png("\n".join(lines), cellw, cellh,
                                       palette)
        text = frame("\n".join(" " * len(strip_spans(l)) for l in lines))
        # frame() is a leading blank line, the rows, and a trailing one, so
        # the creature starts on row 1 and the block is len(lines) + 2 tall.
        nrows = len(lines) + 2
        if png is None:
            return text
        return text + graphics_body(png, cols, rows, nrows, 1, len(MARGIN))

    return paint


def _graphics_watch(paint, draw, every, out, wait,
                    clock=time.monotonic):
    """The drawn animation loop.

    Every frame deletes the image the last one placed. Overwriting the
    cells underneath does not remove an image, and without the delete they
    accumulate until the terminal is holding thousands of them; this is the
    same rule `gfl view --watch` keeps, for the same reason.

    The whole repaint goes inside synchronized output, so a terminal shows
    the previous frame or the next one and never the erased gap between
    them. At twenty frames a second that gap is what flicker is.

    `every` is a period, not a pause. Rasterizing a frame is a millisecond
    or three, and sleeping the whole interval on top of that made the real
    rate whatever was left: 12 frames a second when 20 were asked for, and
    fewer again on a retina cell where there are four times the pixels to
    fill. Waiting on the deadline instead means the frame rate is the one
    that was asked for until the machine genuinely cannot keep up, and
    then it degrades by dropping the wait rather than by drifting.
    """
    from .view import (BSU, DELETE_IMAGES, ESU, HIDE_CURSOR,
                       cleanup_sequence)
    wait = wait or time.sleep
    prev_rows = 0
    out.write(HIDE_CURSOR)
    try:
        deadline = clock()
        while True:
            body = paint(draw())
            rewind = f"\x1b[{prev_rows}A\x1b[0J" if prev_rows else ""
            out.write(BSU + rewind + DELETE_IMAGES + body + ESU)
            out.flush()
            prev_rows = body.count("\n")
            deadline += every
            # A key still has to land inside the frame it was pressed in,
            # so the short wait is a real wait and not a skipped one.
            if wait(max(0.0, deadline - clock())):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        # The images have to go on the way out too, or they stay on screen
        # over whatever the shell prints next.
        out.write("\n" + cleanup_sequence(graphics=True))
        out.flush()


def run(a, emit=True, out=None, env=None, err=None, clock=time.monotonic):
    """`gfl pet`: draw the creature once, or until a key is pressed."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    env = os.environ if env is None else env
    size = getattr(a, "size", 1)
    every = max(MIN_EVERY, getattr(a, "every", None) or DEFAULT_EVERY)
    fixed = getattr(a, "mood", None)
    signals = Signals()
    creature = Creature(fixed or "idle", signals.read(), size=size)
    state = {"t0": None, "read": None}

    def tick():
        """The beat, from the clock rather than a frame counter.

        Counting frames tied the creature's speed to `--every`: at twice
        the frame rate it moved twice as fast, so raising the rate to get
        a smoother animation got a faster one instead. Taken from the
        clock, `--every` decides only how finely the motion is sampled.
        """
        now = clock()
        if state["t0"] is None:
            state["t0"] = now
        return (now - state["t0"]) * PET_HZ

    def refresh():
        """Take a reading and let it pick the mood."""
        creature.update(**signals.read())
        if not fixed:
            creature.mood = mood_for(creature.signals)

    def draw():
        now = clock()
        if state["read"] is None or now - state["read"] >= SIGNALS_EVERY:
            state["read"] = now
            refresh()
        return creature.lines(tick())

    def draw_text():
        return "\n".join(draw())

    if getattr(a, "once", False) or not out.isatty():
        # Tick zero, not the clock: one frame has to be the same frame
        # every time it is asked for, or `--once` is not testable and a
        # recording of it is not reproducible. The reading still happens,
        # because a single frame that ignored the machine would be a
        # picture rather than a status line.
        refresh()
        text = frame("\n".join(creature.lines(0)))
        out.write(text if emit else strip_spans(text))
        out.flush()
        return 0

    fd = _stdin_fd()
    restore = _cbreak(fd) if fd is not None else None
    wait = key_waiter(fd) if restore else None
    if getattr(a, "graphics", False):
        painter = _graphics_painter(size, env, err)
        if painter is not None:
            try:
                return _graphics_watch(painter, draw, every, out, wait)
            finally:
                if restore:
                    restore()
    out.write("\x1b[?25l")                # a screensaver has no caret
    try:
        return watch(draw_text, every, emit=emit, out=out, wait=wait,
                     sync=True, hint=False)
    finally:
        out.write("\x1b[?25h")
        out.flush()
        if restore:
            restore()
