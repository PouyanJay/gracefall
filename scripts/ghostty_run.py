#!/usr/bin/env python3
"""Build, re-sign and launch the OSC 4700 Ghostty fork, drawing the
progress with the protocol it is building.

Lives here rather than in the fork so the fork's branch stays a pure
src/terminal change, which is what a pull request there would be.
Point GHOSTTY_SRC at the checkout; the default is the sibling directory
this repository is normally cloned next to.

Three stages as a gracefall flow, and under it a meter fed by zig's own
step counter. Zig writes "[N/M] steps" to its progress line when stderr is
a terminal, so it is run on a pty here, that count is parsed out, and its
own tree is not shown: the meter is the same number said once. In a plain
terminal both lines are their unicode fallbacks; from an already patched
Ghostty they draw as capsules and a gauge.

The re-signing matters: zig build replaces the binary inside an already
signed app bundle, which invalidates the signature, and macOS then kills
the app on launch with SIGKILL and a Code Signature Invalid crash report.
It looks exactly like a crash in your own code and is not one.

The app is detached with its log in a file. A debug build chats on stderr
from the first frame, and a foregrounded app dies with the terminal that
launched it, so Ctrl-C here used to kill Ghostty.
"""
import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios

try:
    from gracefall import flow, meter, strip_spans
except ImportError:  # the shell shim reinstalls, this script only warns
    sys.exit("gracefall is not installed: pipx install gracefall")

HERE = os.environ.get("GHOSTTY_SRC") or os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "ghostty"))
ZIG = os.environ.get("ZIG", "/opt/homebrew/bin/zig")
APP = os.path.join(HERE, "zig-out", "Ghostty.app")
LOG = os.path.join(os.environ.get("TMPDIR", "/tmp"), "ghostty-gracefall.log")
STEPS = re.compile(rb"\[(\d+)/(\d+)\] steps")
DIM, R = "\x1b[2m", "\x1b[0m"

# Envelopes go out whenever stdout is a terminal. The gracefall functions
# always emit them; the CLI is what strips for pipes, and this is not it.
tty = sys.stdout.isatty()


def emit(s):
    sys.stdout.write(s if tty else strip_spans(s))
    sys.stdout.flush()


class Panel:
    """Two lines, repainted in place: the flow, then a meter with a label.

    Cursor-up by the lines we own is safe only because nothing else writes
    between repaints, which is why zig's tree is swallowed instead of shown.
    """

    def __init__(self):
        self.drawn = 0
        self.stages = ["pending"] * 3

    def paint(self, frac, label):
        body = (
            "  " + flow(["build", "sign", "launch"], self.stages) + "\n"
            + "  " + meter(frac, width=30, color="teal") + f"  {DIM}{label}{R}\n"
        )
        # Return to the top of what we drew last time. Each line is cleared
        # before rewriting so a shorter label leaves no tail.
        up = f"\x1b[{self.drawn}A" if self.drawn else ""
        emit(up + "\x1b[J" + body)
        self.drawn = 2

    def stage(self, i, status, frac=None, label=""):
        self.stages[i] = status
        self.paint(frac if frac is not None else (i / 3), label)


def build(panel):
    """Run zig on a pty and feed its step counter to the meter."""
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(HERE)
        env = dict(os.environ, TERM="xterm-256color")
        os.execvpe(ZIG, ["zig", "build", "-Doptimize=Debug"], env)
    # Zig only draws progress when it believes it has a real terminal, and
    # that includes a window size.
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))

    tail = b""
    done = total = 0
    while True:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            tail = (tail + chunk)[-8192:]
            m = None
            for m in STEPS.finditer(tail):
                pass
            if m:
                done, total = int(m.group(1)), int(m.group(2))
                panel.stage(0, "active", done / total if total else 0,
                            f"zig  {done}/{total} steps")
    _, status = os.waitpid(pid, 0)
    code = os.waitstatus_to_exitcode(status)
    if code != 0:
        # The reason is in what zig printed. Replay it under the panel so
        # the failure is not a coral capsule with nothing after it.
        panel.stage(0, "failed", done / total if total else 0, "zig build failed")
        sys.stdout.write("\n")
        sys.stdout.buffer.write(re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x1b]*\x1b\\|\r", b"", tail))
        sys.stdout.write("\n")
        sys.exit(code)
    panel.stage(0, "done", 1 / 3, f"zig  {total}/{total} steps")


def sign(panel):
    panel.stage(1, "active", 1 / 3, "codesign")
    r = subprocess.run(["codesign", "--force", "--deep", "--sign", "-", APP],
                       capture_output=True, text=True)
    if r.returncode != 0:
        panel.stage(1, "failed", 1 / 3, "codesign failed")
        sys.stdout.write("\n" + r.stderr)
        sys.exit(r.returncode)
    panel.stage(1, "done", 2 / 3, "codesign")


def launch(panel, argv):
    panel.stage(2, "active", 2 / 3, "ghostty")
    with open(LOG, "wb") as log:
        subprocess.Popen(
            [os.path.join(APP, "Contents", "MacOS", "ghostty"), *argv],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    panel.stage(2, "done", 1.0, "ghostty")


def main():
    if not os.path.isfile(os.path.join(HERE, "build.zig")):
        sys.exit(f"no Ghostty checkout at {HERE}, set GHOSTTY_SRC=/path/to/ghostty")
    if not shutil.which(ZIG) and not os.path.exists(ZIG):
        sys.exit(f"zig not found at {ZIG}, set ZIG=/path/to/zig")
    panel = Panel()
    panel.paint(0, "starting")
    build(panel)
    sign(panel)
    launch(panel, sys.argv[1:])
    emit(f"\n  {DIM}log  {LOG}{R}\n  {DIM}try  gracefall demo{R}\n")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    main()
