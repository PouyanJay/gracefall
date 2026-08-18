"""`gfl git log`: the parser against real `git log --numstat` shapes, the
listing's layout rules, and the CLI end to end on this repository."""

import os
import re
import shutil
import subprocess
import sys
import time

import pytest

from gracefall import strip_spans
from gracefall.gitlog import (DEFAULT_PAGER, _pager, _refs, build,
                              parse_listing, render_listing)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return SGR.sub("", strip_spans(s))


def _local(y, m, d, h):
    return int(time.mktime((y, m, d, h, 0, 0, 0, 0, -1)))


LOG = ("\x01abc1234\t{t1}\tAda Lovelace\tp1\tHEAD -> main, tag: v1.0, origin/main\tAdd the engine\n"
       "10\t2\tsrc/engine.py\n"
       "3\t0\tREADME.md\n"
       "-\t-\tdocs/demo.gif\n"
       "\n"
       "\x01def5678\t{t2}\tBob\tp1 p2\t\tMerge branch 'feature'\n"
       "\x01aaa0000\t{t3}\tAda Lovelace\tp1\t\tRename things\n"
       "1\t1\tsrc/{{old => new}}/x.py\n"
       "4\t4\told.txt => new.txt\n"
       "\n")


def _log():
    return LOG.format(t1=_local(2026, 8, 17, 22), t2=_local(2026, 8, 17, 21),
                      t3=_local(2026, 8, 13, 9))


def test_parse_listing_reads_every_field():
    c = parse_listing(_log())
    assert [x["hash"] for x in c] == ["abc1234", "def5678", "aaa0000"]
    first = c[0]
    assert first["author"] == "Ada Lovelace"
    assert first["subject"] == "Add the engine"
    assert first["refs"] == ["main", "v1.0"]          # origin/main dropped
    assert not first["merge"]
    assert (first["add"], first["rm"], first["files"]) == (13, 2, 3)
    assert first["paths"] == {"src": 12, "README.md": 3, "docs": 0}
    assert c[1]["merge"] and c[1]["files"] == 0
    assert c[2]["paths"] == {"src": 2, "new.txt": 8}


def test_parse_listing_skips_garbage():
    assert parse_listing("not a log\n5\t5\n") == []
    assert parse_listing("\x01bad line without tabs\n") == []


@pytest.mark.parametrize("decoration,refs", [
    ("HEAD -> main, tag: v0.5.0, origin/main", ["main", "v0.5.0"]),
    ("HEAD", []),
    ("tag: v1, tag: v1", ["v1"]),
    ("", []),
])
def test_refs(decoration, refs):
    assert _refs(decoration) == refs


def test_render_listing_groups_by_day_and_marks_landmarks():
    commits = parse_listing(_log())
    now = _local(2026, 8, 18, 12)
    text = render_listing(commits, cols=120, now=now)
    p = plain(text)
    lines = p.split("\n")
    # newest day first, with its own count and churn
    assert lines[0].startswith("Mon Aug 17")
    assert "2 commits, +13 -2" in lines[0]
    assert "Thu Aug 13" in p and "1 commit, +5 -5" in p
    # every commit line: hash, then subject; refs at the end of the
    # subject column; the merge says so and shows no stat
    assert "abc1234" in lines[1] and "Add the engine" in lines[1]
    assert re.search(r"Add the engine\s+main v1.0\s+22:00", lines[1])
    assert "merge" in lines[2] and "+0" not in lines[2]
    # more than one author, so the author column is on
    assert "Ada Lovelace" in lines[1] and "Bob" in lines[2]
    # every meter is a span, one per non-merge commit
    assert text.count("t=meter") == 2


def test_render_listing_hides_the_author_column_for_a_single_author():
    commits = [c for c in parse_listing(_log()) if c["author"] != "Bob"]
    p = plain(render_listing(commits, cols=120, now=_local(2026, 8, 18, 12)))
    assert "Ada Lovelace" not in p


def test_render_listing_fits_the_terminal_width():
    commits = parse_listing(_log())
    for cols in (60, 80, 100, 140):
        text = render_listing(commits, cols=cols, now=_local(2026, 8, 18, 12))
        for line in plain(text).split("\n"):
            assert len(line) <= cols, (cols, len(line), line)
    # narrow: the subject is truncated with an ellipsis, files column off
    p = plain(render_listing(commits, cols=60, now=_local(2026, 8, 18, 12)))
    assert "files" not in p
    long = parse_listing(_log())
    long[0]["subject"] = "x" * 200
    p = plain(render_listing(long, cols=80, now=_local(2026, 8, 18, 12)))
    assert "…" in p and "x" * 60 not in p


def test_render_listing_shows_the_year_when_it_is_not_this_one():
    commits = parse_listing(_log())
    p = plain(render_listing(commits, cols=100, now=_local(2027, 3, 1, 12)))
    assert "Mon Aug 17 2026" in p


def test_pager_default_and_override(monkeypatch):
    monkeypatch.delenv("GFL_PAGER", raising=False)
    if shutil.which("less"):
        assert _pager() == DEFAULT_PAGER.split()
        assert "-r" in DEFAULT_PAGER.split()[1]        # -R would strip OSC
    monkeypatch.setenv("GFL_PAGER", "cat")
    assert _pager() is None
    monkeypatch.setenv("GFL_PAGER", "")
    assert _pager() is None
    monkeypatch.setenv("GFL_PAGER", "less -X --tabs=4")
    assert _pager() == ["less", "-X", "--tabs=4"]


# --------------------------------------------------------------------------
# the CLI, on this repository


def run_cli(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, "-m", "gracefall.cli", *args],
                          capture_output=True, text=True, env=e)


needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


@needs_git
def test_git_log_lists_this_repository_with_a_summary_on_top():
    r = run_cli("git", "log", "-5", env={"COLUMNS": "120"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert "by weekday and hour" in p          # the summary
    assert re.search(r"^\s*\w{3} \w{3} \d+", p, re.M)   # a day header
    assert "5 total" in p
    assert "\x1b]4700" not in r.stdout        # piped: plain text
    f = run_cli("--force-osc", "git", "log", "-5", "--no-summary")
    assert "\x1b]4700;t=meter" in f.stdout
    assert "by author" not in SGR.sub("", f.stdout)


@needs_git
def test_git_log_takes_git_log_arguments():
    r = run_cli("git", "log", "--author=nobody-has-this-name")
    assert r.returncode == 0
    assert "no commits" in SGR.sub("", r.stdout)
    r = run_cli("git", "log", "-2", "--oneline", "--", "src")
    assert r.returncode == 0 and "2 total" in SGR.sub("", r.stdout)


def test_git_other_subcommands_are_a_clean_error():
    r = run_cli("git", "status")
    assert r.returncode != 0
    assert "supports `log`" in r.stderr and "Traceback" not in r.stderr


@needs_git
@pytest.mark.skipif(sys.platform == "win32", reason="pty")
def test_git_log_pages_through_the_pager_on_a_terminal(tmp_path):
    # On a tty the page goes to $GFL_PAGER with its envelopes intact; the
    # pager here just records what it was given.
    import pty
    sink = tmp_path / "paged"
    pager = tmp_path / "pager.py"
    pager.write_text("import sys\nopen(sys.argv[1], 'wb').write(sys.stdin.buffer.read())\n")
    env = dict(os.environ, GFL_PAGER=f"{sys.executable} {pager} {sink}",
               COLUMNS="100")
    pid, fd = pty.fork()
    if pid == 0:  # pragma: no cover
        os.environ.update(env)
        os.execvp(sys.executable, [sys.executable, "-m", "gracefall.cli",
                                   "git", "log", "-3", "--no-summary"])
    try:
        while os.read(fd, 4096):        # macOS returns b"" at EOF, Linux raises
            pass
    except OSError:
        pass
    _, status = os.waitpid(pid, 0)
    assert os.WEXITSTATUS(status) == 0
    paged = sink.read_bytes().decode()
    assert "\x1b]4700;t=meter" in paged
    assert "3 total" not in paged
    assert re.search(r"^\s*\w{3} \w{3} \d+", SGR.sub("", paged), re.M)
