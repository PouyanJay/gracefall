"""gracefall.creature: a cat, assembled out of spans.

The creature is drawn the only way gracefall can draw anything: with the
v1 span types. Nothing here is a picture. At the sizes that have room it
is a single `scatter`, whose braille grid is two dots per cell across and
four down, so thirteen cells by four rows is a 26 x 16 canvas and twenty
six by eight is 52 x 32. At one and two rows it is a `lanes` figure
instead, because four dot rows cannot hold a face and a lane cell gets a
whole glyph. Under every size that has the room sits a `meter` of the
load, so the creature reports as well as breathes.

Three rules make it usable as an animation:

- Frames are pure. `(mood, signals, size, tick)` decides every byte. No
  clock, no randomness, no environment. A caller that wants motion counts
  ticks itself, and a test can assert a frame.
- A tick is a beat, not a frame, and it may be fractional. Every function
  below is continuous in it, so a caller sampling twice as often gets
  twice as many *distinct* frames rather than each one twice.
- Every frame of a size is the same visible width, `Creature.width()`, so
  a caller can redraw it in place without clearing the line.

The cat is authored once, in a 0..1 box at a fixed ratio, and fitted into
whatever canvas the size gives it. Fitted and centred, never stretched: a
canvas three times wider than it is tall would otherwise hold a cat three
times wider than it is tall, which is a different animal.

Signals drive it, and are all optional:

    cpu      0..1   the meter, and how wide the eyes open
    rate     >= 0   how fast it breathes (soft-kneed, no magic scale)
    latency  >= 0   how far the whiskers droop (the same knee)
    ci       "pass" | "fail" | None   a failure turns the cat coral
    dirty    bool   an uncommitted tree turns the meter amber

`mood_for(signals)` is the suggested reading of a set of signals, for
callers that would rather not pick a mood themselves.
"""

import math

from . import lanes, meter, scatter
from . import shade as _shade

__all__ = ["MOODS", "SIZES", "Creature", "mood_for"]

MOODS = ("idle", "working", "happy", "sad", "sleepy")

#: Rows the creature may occupy, and the cells it is wide on each. Braille
#: dots are square (two per cell across, four down, on a cell about twice
#: as tall as it is wide), so a taller creature needs proportionally more
#: columns or the canvas it is fitted into is a letterbox with the cat
#: stranded in the middle of it.
SIZES = (1, 2, 4, 6, 8, 12, 16)
WIDTHS = {1: 13, 2: 13, 4: 13, 6: 20, 8: 26, 12: 34, 16: 30}

#: The width of the small creature, and the one a caller laying out a
#: line beside it can assume: `frame()` at size 1 is this wide, which is
#: what the live line under a wrapped command and the launch splash both
#: size themselves against. Ask `Creature.width()` for any other size.
WIDTH = WIDTHS[1]

#: The smallest size drawn in braille. Below this the cat is a `lanes`
#: figure: two rows is an eight dot tall canvas, and a face needs more
#: than eight dots of height before it stops being a smudge.
MIN_BRAILLE = 4

#: The smallest size drawn with tone, by `shade.py`. Three techniques,
#: each used where it wins:
#:
#:   lanes    1-2 rows    one glyph a cell, no resolution but no ambiguity
#:   scatter  4-12 rows   eight braille dots a cell, an outline
#:   heat    16+ rows     ten levels of ink a cell, a shaded solid
#:
#: Tone is made of gradients and a gradient across twenty cells is four
#: characters wide, so below `shade.COLS_MIN` columns it turns to mush and
#: the outline wins. That is the whole reason the line falls at sixteen:
#: a figure keeps its proportions, so its width follows its height, and
#: sixteen rows is the first size wide enough to shade.
MIN_SHADED = 16

#: The cat's own proportions, width over height in dots.
ASPECT = 1.55

#: Every signal this module reads, with the value that means "nothing was
#: measured". A creature with no signals at all still draws.
DEFAULTS = {"cpu": 0.0, "rate": 0.0, "latency": 0.0, "ci": None,
            "dirty": False}

#: mood -> the body's colour role.
_ROLE = {"idle": "teal", "working": "amber", "happy": "teal",
         "sad": "coral", "sleepy": "dim"}

#: mood -> how the mouth turns: up is a smile, down is a frown.
_TURN = {"happy": 1.0, "sad": -1.0}

#: mood -> the two mouth cells of the one row cat. `r` then `l` meet at
#: the bottom, which is a smile; `l` then `r` meet at the top, which is a
#: frown; two bars is a mouth open on something. A mood a reader cannot
#: tell from another mood without looking at the colour is not a mood, and
#: colour is the one thing a mono terminal takes away.
_MOUTH = {"happy": ("r", "l"), "sad": ("l", "r"),
          "working": ("b", "b"), "idle": ("h", "h"), "sleepy": ("h", "h")}

#: The tail's cells, in order, as a lane kind. A one row cat has no room
#: to breathe and its face is discrete, so without this the only thing
#: that ever changes is the blink: one frame in twelve beats, which under
#: a command that is thinking reads as a dead creature rather than a
#: waiting one. The tail is the only continuously moving part it has.
_TAIL = ("b", "r", "h", "r")

#: One blink every twelfth beat, which at a two-a-second animation is
#: about the rate a person blinks.
_BLINK = 12

#: How long the eyes stay shut, in beats. A window rather than an equality
#: test, because the tick is continuous: `tick % _BLINK == 0` is a test a
#: caller sampling twenty times a second would almost always miss, and the
#: blink would come and go depending on the frame rate.
_BLINK_HOLD = 1.0

# The cat, in a 0..1 box, y downwards. Everything below is in these units
# and nothing knows how many dots it will get.
_HEAD_RX, _HEAD_RY = 0.34, 0.30
_HEAD_CX, _HEAD_CY = 0.50, 0.56
_EAR_DX, _EAR_TOP = 0.235, 0.06
_EYE_DX, _EYE_DY, _EYE_R = 0.155, 0.075, 0.055
_NOSE_DY = 0.085
_MOUTH_DY, _MOUTH_W = 0.175, 0.10
_WHISK_DX = 0.10


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


class _Canvas:
    """A dot grid the cat is drawn onto, fitted to `ASPECT` and centred.

    `bob` shifts the whole drawing vertically and is measured in *dots*,
    not in the 0..1 design box. Anything smaller than a dot is not motion:
    a breath of four percent of the head's radius is a fifth of a dot on a
    sixteen dot canvas, so it rounds to the same picture every frame and
    the cat sits perfectly still while the numbers underneath it move.
    """

    def __init__(self, gw, gh, bob=0.0):
        self.gw, self.gh, self.on = gw, gh, set()
        bh = min(gw / ASPECT, float(gh))
        self.bw, self.bh = bh * ASPECT, bh
        self.x0 = (gw - self.bw) / 2.0
        self.y0 = (gh - self.bh) / 2.0 + bob

    def px(self, u, v):
        x = int(round(self.x0 + u * (self.bw - 1)))
        y = int(round(self.y0 + v * (self.bh - 1)))
        if 0 <= x < self.gw and 0 <= y < self.gh:
            self.on.add((x, y))

    def arc(self, cu, cv, ru, rv, a0=0.0, a1=360.0):
        n = max(12, int((a1 - a0) / 360.0 * 2.2 * max(self.bw, self.bh)))
        for i in range(n + 1):
            th = math.radians(a0 + (a1 - a0) * i / n)
            self.px(cu + ru * math.cos(th), cv + rv * math.sin(th))

    def line(self, u0, v0, u1, v1):
        n = max(2, int(math.hypot((u1 - u0) * self.bw,
                                  (v1 - v0) * self.bh)) + 1)
        for i in range(n + 1):
            self.px(u0 + (u1 - u0) * i / n, v0 + (v1 - v0) * i / n)

    def curve(self, u0, u1, fn):
        n = max(3, int(abs(u1 - u0) * self.bw) + 1)
        for i in range(n + 1):
            u = u0 + (u1 - u0) * i / n
            self.px(u, fn(u))

    def points(self):
        """The dots, with y flipped: this grid runs downwards and
        `scatter` reads its y upwards."""
        return [(x, self.gh - 1 - y) for x, y in sorted(self.on)]


class Creature:
    """A cat made of spans, on 1, 2, 4, 6 or 8 lines.

        c = Creature("working", {"cpu": 0.62}, size=8)
        for i in range(40):
            print("\\n".join(c.lines(i * 0.1)))
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
        """The visible cell width of every frame at this size."""
        return WIDTHS[self.size]

    def frame(self, tick):
        """One line: the cat's face as a `lanes` figure.

        This is the whole creature at size 1, and the row a caller puts
        beside a prompt or on a live status line at any size. It stays
        `lanes` at every size on purpose: one row is four dot rows of
        braille, and a face needs more than that, while a lane cell gets a
        whole glyph and reads at a glance.

        Always `WIDTH` cells, never `width()`: this is the compact form,
        and a caller putting it after a chart wants the face, not the face
        centred in twenty six columns of padding.
        """
        return self._lanes_row(float(tick), WIDTH)

    def lines(self, tick):
        """`size` lines, each exactly `width()` cells wide."""
        tick = float(tick)
        if self.size == 1:
            return [self._lanes_row(tick)]
        if self.size < MIN_BRAILLE:
            return [self._lanes_row(tick), self._meter_row()]
        rows = self.size - 1
        if self.size >= MIN_SHADED:
            return self._shaded_rows(tick, rows) + [self._meter_row()]
        return self._braille_rows(tick, rows) + [self._meter_row()]

    # ------------------------------------------------------------------
    # the readings

    def _role(self):
        if self.signals.get("ci") == "fail":
            return "coral"
        return _ROLE[self.mood]

    def _shut(self, tick):
        """Whether the eyes are closed on this beat."""
        if self.mood == "sleepy":
            return True
        return (tick + 1) % _BLINK < _BLINK_HOLD

    def _speed(self):
        speed = 0.6 + 1.2 * _knee(self.signals.get("rate", 0.0))
        return speed * 0.5 if self.mood == "sleepy" else speed

    def _breath(self, tick):
        """The head's scale on this beat."""
        return 1.0 + 0.06 * math.sin(self._speed() * tick)

    def _bob(self, tick):
        """How far the whole cat sits off centre, in whole dots.

        Rounded to dots on purpose. A sub-dot offset is not a smaller
        movement, it is no movement: it rounds away and the frame is
        identical to the last one. One dot either side of centre is the
        smallest breath a braille canvas can actually show.
        """
        return round(1.4 * math.sin(self._speed() * tick + 0.7))

    # ------------------------------------------------------------------
    # the rows

    def _meter_row(self):
        """The load, under the cat. The one row that is a reading rather
        than a drawing, and it may not be decorated: its value is `cpu`
        and nothing else, or the number on screen is not the number."""
        w = self.width()
        bar = max(4, w - 4)
        pad = (w - bar) // 2
        role = "amber" if self.signals.get("dirty") else self._role()
        return (" " * pad + meter(_clamp01(self.signals.get("cpu", 0.0)),
                                  bar, role) + " " * (w - bar - pad))

    def _lanes_row(self, tick, w=None):
        """The cat as one row of lanes: ears leaving and joining, eyes on
        their own lanes, a mouth sliding between them."""
        c = self._role()
        eye = "h" if self._shut(tick) else "d"
        mouth = _MOUTH[self.mood]
        # `l` then `r` side by side is a peak, which is an ear. A blank
        # between the ear and the eye is what stops the two reading as
        # one shape.
        tail = _TAIL[int(tick * self._speed() * 1.6) % len(_TAIL)]
        cells = [("l", c), ("r", c), (".", None),
                 (eye, "fg"), (".", None), (mouth[0], c), (mouth[1], c),
                 (".", None), (eye, "fg"),
                 (".", None), ("l", c), ("r", c), (tail, c)]
        w = self.width() if w is None else w
        pad = (w - len(cells)) // 2
        return (" " * pad + lanes(cells)
                + " " * (w - len(cells) - pad))

    def _braille_rows(self, tick, rows):
        """The cat as one `scatter`, `rows` terminal rows tall.

        One span rather than one per row: the drawing is a single figure
        and the rows of it are not independent, so splitting it would make
        every row's canvas depend on what happened to be drawn in it.
        """
        w = self.width()
        gw, gh = w * 2, rows * 4
        c = _Canvas(gw, gh, bob=self._bob(tick))
        self._draw(c, tick)
        # Explicit bounds, or the picture would rescale on every frame:
        # scatter's canvas defaults to the extent of its own points, and
        # the cat's extent changes as it breathes.
        return scatter(c.points(), w=w, h=rows, color=self._role(),
                       trend=False, xlo=0, xhi=gw - 1,
                       ylo=0, yhi=gh - 1).split("\n")

    def _shaded_rows(self, tick, rows):
        """The cat with tone, one `heat` span per row.

        A span per row rather than one for the grid because the grid does
        not fit: a fifty six by fifteen frame is about four thousand bytes
        of payload against a cap of 2048. A row is a few hundred.
        """
        return _shade.rows(self.width(), rows, tick, self.mood,
                           self.signals, color=self._role())

    # ------------------------------------------------------------------
    # the cat

    def _draw(self, c, tick):
        s = self.signals
        breath = self._breath(tick)
        shut = self._shut(tick)
        rx, ry = _HEAD_RX, _HEAD_RY * breath
        cy = _HEAD_CY
        c.arc(_HEAD_CX, cy, rx, ry)
        for sx in (-1, 1):                              # ears
            bx = _HEAD_CX + sx * _EAR_DX
            top = cy - ry * 0.72
            c.line(bx - 0.075, top, bx + sx * 0.015, _EAR_TOP)
            c.line(bx + sx * 0.015, _EAR_TOP, bx + 0.075, top)
        wide = 1.0 + 0.25 * _clamp01(s.get("cpu", 0.0))
        for sx in (-1, 1):                              # eyes
            ex, ey = _HEAD_CX + sx * _EYE_DX, cy - _EYE_DY
            if shut:
                c.line(ex - _EYE_R, ey, ex + _EYE_R, ey)
            else:
                c.arc(ex, ey, _EYE_R, _EYE_R * wide)
                if min(c.bw, c.bh) >= 24:               # a pupil, given room
                    c.px(ex, ey)
        ny = cy + _NOSE_DY                              # nose
        c.line(_HEAD_CX - 0.028, ny, _HEAD_CX + 0.028, ny)
        c.line(_HEAD_CX, ny, _HEAD_CX, ny + 0.035)
        turn = _TURN.get(self.mood, 0.0)                # mouth
        my = cy + _MOUTH_DY
        for sx in (-1, 1):
            c.curve(_HEAD_CX, _HEAD_CX + sx * _MOUTH_W,
                    lambda u: my - turn * 0.055 *
                    math.sin(abs(u - _HEAD_CX) / _MOUTH_W * math.pi))
        droop = 0.10 * _knee(s.get("latency", 0.0))     # whiskers
        for sx in (-1, 1):
            for k in range(3):
                y = cy + 0.03 + (k - 1) * 0.055
                c.line(_HEAD_CX + sx * (rx * 0.92), y,
                       _HEAD_CX + sx * (rx * 0.92 + _WHISK_DX),
                       y + (k - 1) * 0.025 + droop)
