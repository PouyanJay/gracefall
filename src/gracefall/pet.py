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
from .recipes import frame, watch

__all__ = ["Signals", "cpu_load", "ci_env", "git_dirty", "run"]

#: The floor under `--every`. Four frames a second is the animation this
#: was designed for, and a loop faster than twenty is a busy wait with a
#: creature on it.
MIN_EVERY = 0.05

#: How often the tree may be asked whether it is dirty. A `git status` on
#: a large repository is the one expensive reading here, so it is cached
#: for this many seconds and every frame in between reuses the answer.
DIRTY_EVERY = 5.0


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


def run(a, emit=True, out=None):
    """`gfl pet`: draw the creature once, or until a key is pressed."""
    out = sys.stdout if out is None else out
    size = getattr(a, "size", 1)
    every = max(MIN_EVERY, getattr(a, "every", 0.25))
    fixed = getattr(a, "mood", None)
    signals = Signals()
    creature = Creature(fixed or "idle", signals.read(), size=size)
    state = {"tick": 0}

    def draw():
        s = signals.read()
        creature.update(**s)
        if not fixed:
            creature.mood = mood_for(creature.signals)
        text = "\n".join(creature.lines(state["tick"]))
        state["tick"] += 1
        return text

    if getattr(a, "once", False) or not out.isatty():
        text = frame(draw())
        out.write(text if emit else strip_spans(text))
        out.flush()
        return 0

    fd = _stdin_fd()
    restore = _cbreak(fd) if fd is not None else None
    wait = key_waiter(fd) if restore else None
    out.write("\x1b[?25l")                # a screensaver has no caret
    try:
        return watch(draw, every, emit=emit, out=out, wait=wait)
    finally:
        out.write("\x1b[?25h")
        out.flush()
        if restore:
            restore()
