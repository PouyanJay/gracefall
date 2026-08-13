"""Tests for `gfl shell`, the pty wrapper.

All of it runs without a pty. The tracker is the part that has to be right:
it decides where on screen a span landed, and a wrong answer paints a chart
over unrelated text. Getting no chart is fine, the fallback is already
there; getting one in the wrong place corrupts the screen.
"""

import pytest

from gracefall import meter, spark
from gracefall.shell import SpanTracker, placement_bytes


def track(chunks, width=80):
    """Feed chunks and return (all passthrough bytes, all spans, tracker)."""
    t = SpanTracker(width)
    out, spans = bytearray(), []
    for chunk in chunks:
        for seg in t.feed(chunk if isinstance(chunk, bytes) else
                          chunk.encode()):
            if isinstance(seg, bytes):
                out += seg
            else:
                spans.append(seg)
    return bytes(out), spans, t


# ---------------------------------------------------------- passthrough


def test_every_byte_is_passed_through_unchanged():
    """The shim must never alter the stream. The fallback is what the user
    already sees, and swallowing a byte would corrupt the shell."""
    data = ("hello\r\n" + spark([1, 4, 2, 8]) + "\r\n$ ").encode()
    out, _, _ = track([data])
    assert out == data


def test_stream_split_at_every_byte_still_passes_through():
    """Reads from a pty land on arbitrary boundaries, including the middle
    of an escape sequence."""
    data = ("x" + meter(0.5, 10) + "y").encode()
    for size in (1, 2, 3, 7, 64):
        chunks = [data[i:i + size] for i in range(0, len(data), size)]
        out, spans, t = track(chunks)
        assert out + t._buf == data, f"chunk size {size}"
        assert len(spans) == 1, f"chunk size {size}"


# ------------------------------------------------------------ placement


def test_single_row_span_reports_where_it_started():
    _, spans, _ = track(["prefix " + spark([1, 4, 2, 8])])
    assert len(spans) == 1
    s = spans[0]
    assert s["col"] == 7, "span starts after 'prefix '"
    assert s["rows"] == 1
    assert s["cols"] == 4
    assert s["up"] == 0, "still on the same row"


def test_multi_row_span_reports_the_rows_to_go_up():
    body = "\x1b]4700;t=x\x1b\\aa\r\nbb\r\ncc\x1b]4700;\x1b\\"
    _, spans, _ = track([body])
    s = spans[0]
    assert s["rows"] == 3
    assert s["up"] == 2, "cursor ends on the last row of the span"
    assert s["col"] == 0


def test_indent_is_excluded_from_the_box():
    """SPEC.md computes the rectangle from non-space cells, so a multi-line
    span's indentation must not widen it."""
    body = ("\x1b]4700;t=x\x1b\\" + "    ab\r\n" + "    cd"
            + "\x1b]4700;\x1b\\")
    _, spans, _ = track([body])
    s = spans[0]
    assert s["col"] == 4
    assert s["cols"] == 2
    assert s["rows"] == 2


def test_autowrap_counts_as_a_new_row():
    """A span wider than the terminal wraps, and the box has to follow."""
    body = "\x1b]4700;t=x\x1b\\" + "a" * 15 + "\x1b]4700;\x1b\\"
    _, spans, _ = track([body], width=10)
    s = spans[0]
    assert s["rows"] == 2
    assert s["up"] == 1


def test_span_with_only_spaces_is_dropped():
    body = "\x1b]4700;t=x\x1b\\    \x1b]4700;\x1b\\"
    _, spans, _ = track([body])
    assert spans == []


def test_absolute_cursor_positioning_aborts_the_span():
    """After CSI H our row is no longer relative to the span's start, so we
    cannot say where it began. Dropping it leaves the fallback, which is
    correct; guessing would paint a chart over unrelated text."""
    body = "\x1b]4700;t=x\x1b\\ab\x1b[5;1Hcd\x1b]4700;\x1b\\"
    _, spans, _ = track([body])
    assert spans == []


def test_sgr_colours_do_not_move_the_cursor():
    """The real fallbacks are wrapped in colour codes, so this is the
    common case, not an edge case."""
    _, spans, _ = track(["ab" + spark([1, 2, 3, 4])])
    assert spans[0]["col"] == 2
    assert spans[0]["cols"] == 4


def test_carriage_return_and_backspace():
    body = "\x1b]4700;t=x\x1b\\abcd\rxy\x1b]4700;\x1b\\"
    _, spans, _ = track([body])
    assert spans[0]["col"] == 0
    assert spans[0]["cols"] == 4


@pytest.mark.parametrize("seq,expect_col", [
    ("\x1b[5C", 5),      # cursor forward
    ("\x1b[3C\x1b[1D", 2),
    ("\x1b[9G", 8),      # absolute column
])
def test_cursor_movement_before_a_span(seq, expect_col):
    _, spans, _ = track([seq + spark([1, 2])])
    assert spans[0]["col"] == expect_col


def test_two_spans_in_one_line_are_both_found():
    _, spans, _ = track(["a" + spark([1, 2]) + " " + meter(0.5, 6) + "z"])
    assert len(spans) == 2
    assert spans[0]["cols"] == 2
    assert spans[1]["cols"] == 6
    assert spans[1]["col"] == 1 + 2 + 1


# --------------------------------------------------------- placement bytes


def test_placement_walks_the_cursor_back_without_decsc():
    """There is one saved-cursor slot and a program inside the shell may be
    using it. Clobbering vim's saved position to draw a chart is a bad
    trade, so the return path is computed instead."""
    out = placement_bytes(b"\x89PNG", {"up": 3, "col": 9, "end_col": 29,
                                       "rows": 1, "cols": 20}).decode()
    assert "\x1b7" not in out and "\x1b8" not in out
    assert out.startswith("\x1b[3A\x1b[10G"), "up then to the start column"
    assert out.endswith("\x1b[3B\x1b[30G"), "back down and to where we were"
    assert "a=T,f=100,c=20,r=1" in out


def test_placement_omits_zero_row_moves():
    """CSI 0 A still moves a line in some terminals."""
    import re
    out = placement_bytes(b"x", {"up": 0, "col": 0, "end_col": 4,
                                 "rows": 1, "cols": 4}).decode()
    # Look for the escape sequence, not the letter: base64 is full of As.
    assert not re.search(r"\x1b\[\d*[AB]", out)
    assert out.startswith("\x1b[1G")
    assert out.endswith("\x1b[5G")


def test_a_span_is_emitted_before_the_bytes_that_follow_it():
    """The killer ordering bug: one read usually carries the newline after a
    chart, and painting the image after that newline puts it a row too low.
    The span has to come out of feed() at the point it closed."""
    t = SpanTracker(80)
    segs = t.feed((spark([1, 4, 2, 8]) + "\r\n$ ").encode())
    kinds = ["span" if isinstance(s, dict) else "bytes" for s in segs]
    assert "span" in kinds, kinds
    span_at = kinds.index("span")
    tail = b"".join(s for s in segs[span_at:] if isinstance(s, bytes))
    assert tail.startswith(b"\r\n"), "the newline must come after the span"
    span = [s for s in segs if isinstance(s, dict)][0]
    assert span["up"] == 0
    assert span["end_col"] == 4, "cursor sits just past the fallback"


# ---------------------------------------------------------- relaunching


def test_offer_relaunch_lists_what_is_installed(monkeypatch, capsys):
    """Being told "your terminal cannot do this" is only useful if the next
    step is obvious. Here it is a different window, so offer to open it."""
    import io
    from gracefall import shell
    monkeypatch.setattr(shell, "available_terminals",
                        lambda: [("Ghostty", "/bin/true", lambda e, c, d: [e]),
                                 ("kitty", "/bin/true", lambda e, c, d: [e])])
    monkeypatch.setattr(shell.sys.stdin, "isatty", lambda: True)
    launched = {}
    monkeypatch.setattr(shell.subprocess if hasattr(shell, "subprocess")
                        else __import__("subprocess"), "Popen",
                        lambda *a, **k: launched.setdefault("argv", a[0]))
    out = io.StringIO()
    assert shell.offer_relaunch(["gfl", "shell"], out=out, ask=lambda _: "2",
                                color=False)
    text = out.getvalue()
    assert "1  Ghostty" in text and "2  kitty" in text
    assert "opened kitty" in text


def test_menu_is_drawn_with_gracefalls_own_output(monkeypatch):
    """The charts in the menu are real spans rendered as text. That is the
    argument, not decoration: this is what you already have, next to an
    offer of the smooth version."""
    from gracefall.shell import _menu
    text = _menu("vscode", [("Ghostty", "x", None)], color=True)
    assert "\u2588" in text or "\u2581" in text, "no block art in the menu"
    assert "\x1b[38;2;95;227;192m" in text, "gracefall's teal is missing"
    assert "vscode" in text.lower()
    plain = _menu("vscode", [("Ghostty", "x", None)], color=False)
    assert "\x1b[" not in plain, "NO_COLOR must strip every escape"


def test_menu_labels_its_example_charts():
    """Unlabelled block art is a puzzle, and the whole point being made is
    that the degraded view is readable."""
    from gracefall.shell import _menu, _plain
    text = _plain(_menu("vscode", [("Ghostty", "x", None)], color=False))
    assert "a trend, rising" in text
    assert "a meter, 62% full" in text
    rows = [r for r in text.split("\n") if "a trend" in r or "a meter" in r]
    starts = [r.index("a ") for r in rows]
    assert len(set(starts)) == 1, f"labels are not aligned: {starts}"


def test_offer_relaunch_accepts_q(monkeypatch):
    import io
    from gracefall import shell
    monkeypatch.setattr(shell, "available_terminals",
                        lambda: [("Ghostty", "/bin/true", lambda e, c, d: [e])])
    monkeypatch.setattr(shell.sys.stdin, "isatty", lambda: True)
    out = io.StringIO()
    assert shell.offer_relaunch(["gfl"], out=out, ask=lambda _: "q",
                                color=False) is False


def test_offer_relaunch_says_how_to_install_when_nothing_is_there():
    import io
    from gracefall import shell
    out = io.StringIO()
    orig = shell.available_terminals
    shell.available_terminals = lambda: []
    try:
        assert shell.offer_relaunch(["gfl"], out=out, ask=lambda _: "1",
                                    color=False) is False
    finally:
        shell.available_terminals = orig
    assert "brew install --cask ghostty" in out.getvalue()


def test_offer_relaunch_does_not_prompt_when_not_interactive(monkeypatch):
    """A prompt that nobody can answer would hang a script."""
    import io
    from gracefall import shell
    monkeypatch.setattr(shell, "available_terminals",
                        lambda: [("Ghostty", "/bin/true", lambda e, c, d: [e])])
    monkeypatch.setattr(shell.sys.stdin, "isatty", lambda: False)
    out = io.StringIO()

    def boom(_):
        raise AssertionError("must not prompt")

    assert shell.offer_relaunch(["gfl"], out=out, ask=boom,
                                color=False) is False
    assert "run this inside Ghostty" in out.getvalue()
