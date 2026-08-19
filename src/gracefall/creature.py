"""gracefall.creature: a mascot assembled out of spans.

The creature is drawn the only way gracefall can draw anything: with the
v1 span types. Its head is a `lanes` figure, the same primitive a commit
graph is made of, so in a plain terminal it is box characters and in a
terminal that implements OSC 4700 it is smooth curves with a bead on each
eye. Its arms are a `spark`, its belly a `meter`, and at the largest size
the air around it is a `heat` glow or a `scatter` of specks. Nothing here
is a picture: every limb is data, and the fallback text is generated from
that same data by the same functions every other chart uses.

Two rules make it usable as an animation:

- Frames are pure. `(mood, signals, size, tick)` decides every byte. No
  clock, no randomness, no environment. A caller that wants motion counts
  ticks itself, and a test can assert a frame.
- Every frame of a size is the same visible width, `Creature.width()`, so
  a caller can redraw it in place without clearing the line.

Layout, on a 13 cell grid at every size. The head is a five cell figure
with the eyes at its left and right corners; the crown's curves land on
the eyes and the mouth's curves rise to meet them, so the whole head is
one closed outline:

    size 1     ARM(3) SP HEAD(5) SP ARM(3)      `▂▁▂ ● ─ ● ▂▁▂`
    size 2     that row, then the belly         `  █████▌▁▁▁  `
    size 4     AURA(4) CROWN(5) AURA(4)         `▀▀▀▀ ╱ ╲ ▀▀▀▀`
               ARM(3) SP EYES(5) SP ARM(3)      `▂▁▂ ●   ● ▂▁▂`
               PAD(4) MOUTH(5) PAD(4)           `     ╲ ╱     `
               PAD(2) BELLY(9) PAD(2)           `  █████▌▁▁▁  `

Signals drive the limbs, and are all optional:

    cpu      0..1   the belly's fill and how far the arms swing
    rate     >= 0   how fast the arms swing (soft-kneed, no magic scale)
    latency  >= 0   how far the arms droop (the same knee)
    ci       "pass" | "fail" | None   a failure turns the body coral
    dirty    bool   an uncommitted tree turns the crown amber

`mood_for(signals)` is the suggested reading of a set of signals, for
callers that would rather not pick a mood themselves.
"""

import math

from . import heat, lanes, meter, scatter, spark

__all__ = ["MOODS", "SIZES", "Creature", "mood_for"]

MOODS = ("idle", "working", "happy", "sad", "sleepy")
SIZES = (1, 2, 4)

#: The cell budget, and it is the same at every size so a caller can swap
#: sizes without relaying out the line around the creature.
WIDTH = 13
HEAD = 5
ARM = 3
BELLY = 9
AURA = 4

#: Every signal this module reads, with the value that means "nothing was
#: measured". A creature with no signals at all still draws.
DEFAULTS = {"cpu": 0.0, "rate": 0.0, "latency": 0.0, "ci": None,
            "dirty": False}

#: mood -> (eye cell, the three mouth cells). The mouth is read left to
#: right between the eyes at size 1 and 2, and on its own row at size 4.
#: `r` then `l` meet at the bottom of the cell between them, which is a
#: smile; `l` then `r` meet at the top, which is a frown.
_FACE = {
    "idle":    ("d", (".", "h", ".")),
    "working": ("d", ("h", "h", "h")),
    "happy":   ("d", ("r", ".", "l")),
    "sad":     ("m", ("l", ".", "r")),
    "sleepy":  ("h", (".", "h", ".")),
}

#: mood -> the body's colour role. The face keeps its own roles.
_ROLE = {"idle": "teal", "working": "amber", "happy": "teal",
         "sad": "coral", "sleepy": "dim"}

#: One blink every twelfth tick, which at a two-a-second animation is
#: about the rate a person blinks. It lands on the tick before the
#: multiple, so a caller starting at zero does not meet a creature with
#: its eyes shut. Sleepy eyes are already shut.
_BLINK = 12


def _clamp01(v):
    """None is a reading that has not arrived yet, not an error. The
    creature sits on a prompt line: it may never raise."""
    return 0.0 if v is None else max(0.0, min(1.0, float(v)))


def _knee(v):
    """Map [0, inf) onto [0, 1) without inventing a scale.

    `rate` and `latency` arrive in whatever units the caller measures in,
    so any constant we picked would be wrong somewhere. v/(1+v) is smooth,
    monotonic, and needs no constant: 1 lands at a half, 9 at nine tenths.
    """
    v = 0.0 if v is None else max(0.0, float(v))
    return v / (1.0 + v)


def mood_for(signals):
    """The suggested mood for a set of signals.

    Nothing else in this module calls it: mood stays an explicit attribute
    so a caller can say what it means. This is for the caller that has only
    measurements to go on, and would rather not invent the reading twice.
    """
    s = dict(DEFAULTS, **(signals or {}))
    if s.get("ci") == "fail":
        return "sad"
    if _clamp01(s.get("cpu", 0.0)) > 0.35:
        return "working"
    if s.get("ci") == "pass" and not s.get("dirty"):
        return "happy"
    if not s.get("dirty") and _clamp01(s.get("cpu", 0.0)) < 0.05:
        return "sleepy"
    return "idle"


class Creature:
    """A mascot made of spans, at size 1, 2 or 4 lines.

        c = Creature("working", {"cpu": 0.62}, size=2)
        for tick in range(40):
            print("\\n".join(c.lines(tick)))
    """

    def __init__(self, mood="idle", signals=None, size=1):
        if size not in SIZES:
            raise ValueError(f"size must be one of {SIZES}, not {size!r}")
        self.mood = mood
        self.size = size
        self.signals = dict(DEFAULTS)
        if signals:
            self.signals.update(signals)

    @property
    def mood(self):
        return self._mood

    @mood.setter
    def mood(self, value):
        if value not in MOODS:
            raise ValueError(f"unknown mood {value!r}, use one of {MOODS}")
        self._mood = value

    def update(self, **signals):
        """Merge new readings into `signals`, leaving the rest alone."""
        self.signals.update(signals)

    def width(self):
        """The visible cell width of every frame, at every size."""
        return WIDTH

    def frame(self, tick):
        """One line: the head with an arm on each side. This is the whole
        creature at size 1, and the row a caller puts beside a prompt or
        on a live status line at any size."""
        return self._row(int(tick))

    def lines(self, tick):
        """`size` lines, each exactly `width()` cells wide."""
        tick = int(tick)
        if self.size == 1:
            return [self._row(tick)]
        if self.size == 2:
            return [self._row(tick), self._belly_row()]
        return [self._crown_row(tick), self._row(tick, eyes_only=True),
                self._mouth_row(), self._belly_row()]

    # ------------------------------------------------------------------
    # the rows

    def _row(self, tick, eyes_only=False):
        """arm, head, arm. `eyes_only` drops the mouth out of the head,
        because at size 4 it has a row of its own."""
        arm_l = self._arm(tick, mirror=True)
        arm_r = self._arm(tick, mirror=False)
        return arm_l + " " + self._head(tick, eyes_only) + " " + arm_r

    def _crown_row(self, tick):
        return (self._aura(tick, left=True) + self._crown()
                + self._aura(tick, left=False))

    def _mouth_row(self):
        role = self._body_role()
        cells = [(".", None)] + [(k, role) for k in self._mouth()] + \
            [(".", None)]
        return " " * AURA + lanes(cells) + " " * AURA

    def _belly_row(self):
        pad = " " * ((WIDTH - BELLY) // 2)
        return pad + meter(_clamp01(self.signals.get("cpu", 0.0)),
                           BELLY, self._body_role()) + pad

    # ------------------------------------------------------------------
    # the parts

    def _body_role(self):
        if self.signals.get("ci") == "fail":
            return "coral"
        return _ROLE[self.mood]

    def _eye_cell(self, tick):
        kind = _FACE[self.mood][0]
        if kind != "h" and (tick + 1) % _BLINK == 0:
            kind = "h"                      # one frame with the eyes shut
        return kind

    def _mouth(self):
        return _FACE[self.mood][1]

    def _crown(self):
        """The antennae: up while the creature is awake, drooping when it
        is sad or asleep. Amber when the tree is dirty."""
        role = "amber" if self.signals.get("dirty") else self._body_role()
        up = self.mood not in ("sad", "sleepy")
        a, b = ("l", "r") if up else ("r", "l")
        return lanes([(".", None), (a, role), (".", None), (b, role),
                      (".", None)])

    def _head(self, tick, eyes_only=False):
        """The five cell head: an eye, three cells of mouth, an eye."""
        eye = self._eye_cell(tick)
        eye_role = "dim" if self.mood == "sleepy" else "fg"
        role = self._body_role()
        mid = ((".", ".", ".") if eyes_only else self._mouth())
        cells = [(eye, eye_role)] + \
            [(k, role) for k in mid] + [(eye, eye_role)]
        return lanes(cells)

    def _arm(self, tick, mirror):
        """A three cell spark. The wave's phase is the tick, its swing is
        cpu and its speed is rate, and latency drags the whole arm down.
        The left arm is the same window mirrored, so the creature is
        symmetric: same data, read outwards from the body on both sides.

        lo and hi are pinned to 0 and 1 rather than left to the data, or a
        calm arm would be rescaled into a wild one.
        """
        s = self.signals
        amp = 0.13 + 0.24 * _clamp01(s.get("cpu", 0.0))
        speed = 0.45 + 1.3 * _knee(s.get("rate", 0.0))
        droop = 0.22 * _knee(s.get("latency", 0.0))
        if self.mood == "sleepy":
            amp, speed = amp * 0.35, speed * 0.35
        mid = 0.38 - droop
        pts = []
        for i in range(ARM):
            j = (ARM - 1 - i) if mirror else i
            pts.append(_clamp01(mid + amp * math.sin(speed * tick + 0.9 * j)))
        return spark(pts, self._body_role(), lo=0.0, hi=1.0)

    def _has_aura(self):
        """Whether the air around the head is drawn at all. Blank cells
        cost nothing and keep the width, and a heat row of near-zero cells
        would be a row of dark blocks in a light terminal."""
        return self.mood in ("happy", "working", "sleepy")

    def _aura(self, tick, left):
        if not self._has_aura():
            return " " * AURA
        if self.mood == "happy":
            g = 0.70 + 0.30 * math.sin(tick / 2.0)
            vals = [g * f for f in (0.14, 0.36, 0.66, 1.0)]
            if not left:
                vals.reverse()
            return heat([vals], color="violet", lo=0.0, hi=1.0)
        # working and sleepy: specks in the air, drifting with the tick
        step = 3 if self.mood == "working" else 1
        pts = [(i, ((i * 5 + tick * step) % 7) / 7.0) for i in range(AURA)]
        if not left:
            pts = [(i, y) for i, (_, y) in enumerate(reversed(pts))]
        return scatter(pts, w=AURA, h=1,
                       color="amber" if self.mood == "working" else "dim")
