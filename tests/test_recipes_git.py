"""The git recipes beyond log: parsers against captured output, chart
layout, and the CLI on this repository."""

import os
import re
import shutil
import subprocess
import sys

import pytest

from gracefall import strip_spans
from gracefall.recipes import parse_git_numstat
from gracefall.recipes_git import (_branch_lists_only, _shortlog_args, blame_chart,
                                   branch_chart, churn_line, diff_chart,
                                   parse_blame_authors, parse_numstat, parse_refs,
                                   parse_shortlog, parse_status, shortlog_chart,
                                   status_chart)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return SGR.sub("", strip_spans(s))


# --------------------------------------------------------------------------
# shortlog

SHORTLOG = "   147\tMitchell Hashimoto\n    28\tLukas\n    17\tLeah Amelia Chen\n     1\tsomeone\n"


def test_parse_shortlog():
    assert parse_shortlog(SHORTLOG) == [(147, "Mitchell Hashimoto"), (28, "Lukas"),
                                        (17, "Leah Amelia Chen"), (1, "someone")]
    assert parse_shortlog("garbage\n") == []


def test_shortlog_args_supply_a_revision_and_drop_our_own_flags():
    assert _shortlog_args(["-sn"]) == ["HEAD"]
    assert _shortlog_args(["-s", "-n", "--no-merges"]) == ["--no-merges", "HEAD"]
    assert _shortlog_args(["-sn", "main..feature"]) == ["main..feature"]
    assert _shortlog_args(["-sn", "--", "src"]) == ["HEAD", "--", "src"]


def test_shortlog_chart_scales_to_the_busiest_and_sums():
    p = plain(shortlog_chart(parse_shortlog(SHORTLOG), cols=100))
    lines = p.split("\n")
    assert lines[0].startswith("Mitchell Hashimoto") and "147" in lines[0] and " 76%" in lines[0]
    assert "193 commits, 4 authors" in lines[-1]
    # top ten, then the rest folded
    many = [(10 - i, f"author {i}") for i in range(14)]
    p = plain(shortlog_chart(many, cols=100))
    assert "+ 4 more" in p
    assert "+ 4 more" not in plain(shortlog_chart(many, cols=100, full=True))
    for cols in (60, 80, 120):
        for line in plain(shortlog_chart(many, cols=cols)).split("\n"):
            assert len(line) <= cols


# --------------------------------------------------------------------------
# diff

NUMSTAT = "81\t9\tsrc/gracefall/recipes.py\n6\t1\tsrc/gracefall/cli.py\n-\t-\tdocs/demo.gif\n"


def test_parse_numstat_reads_counts_and_binaries():
    assert parse_numstat(NUMSTAT) == [(81, 9, "src/gracefall/recipes.py"),
                                      (6, 1, "src/gracefall/cli.py"),
                                      (None, None, "docs/demo.gif")]


def test_diff_chart_two_meters_per_file_and_a_total():
    text = diff_chart(parse_numstat(NUMSTAT), cols=110)
    p = plain(text)
    lines = p.split("\n")
    assert lines[0].startswith("src/gracefall/recipes.py") and "+81" in lines[0] and "-9" in lines[0]
    assert "binary" in lines[2]
    assert lines[-1].startswith("total") and "3 files, +87 -10 (90% added)" in lines[-1]
    # a teal and a coral meter per counted file, one teal for the total
    assert text.count("c=teal") == 3 and text.count("c=coral") == 2
    for cols in (60, 80, 100, 130):
        for line in plain(diff_chart(parse_numstat(NUMSTAT), cols=cols)).split("\n"):
            assert len(line) <= cols, (cols, line)


# --------------------------------------------------------------------------
# branch

REFS = ("main\torigin/main\t\t1755000000\t*\n"
        "feature\torigin/feature\tahead 3, behind 1\t1755100000\t \n"
        "old\torigin/old\tgone\t1740000000\t \n"
        "local-only\t\t\t1755050000\t \n")


def test_parse_refs():
    rows = parse_refs(REFS)
    assert [r["name"] for r in rows] == ["main", "feature", "old", "local-only"]
    assert rows[0]["head"] and rows[0]["ahead"] == 0
    assert (rows[1]["ahead"], rows[1]["behind"]) == (3, 1)
    assert rows[2]["gone"]
    assert rows[3]["upstream"] == ""


def test_branch_chart_orders_by_recency_and_marks_head():
    text = branch_chart(parse_refs(REFS), cols=110, now=1755200000)
    p = plain(text)
    lines = p.split("\n")
    assert lines[0].startswith("feature") and "ahead" in lines[0] and "3" in lines[0]
    assert lines[1].startswith("local-only") and "no upstream" in lines[1]
    assert lines[2].startswith("main") and "origin/main" in lines[2]
    assert "upstream gone" in lines[3]
    assert "\x1b[1mmain" in text            # the checked-out branch is bold
    for cols in (60, 80, 100, 130):
        for line in plain(branch_chart(parse_refs(REFS), cols=cols)).split("\n"):
            assert len(line) <= cols, (cols, line)


def test_branch_only_lists():
    assert _branch_lists_only([])
    assert _branch_lists_only(["-v", "-a"])
    assert not _branch_lists_only(["newname"])
    assert not _branch_lists_only(["-d", "old"])
    assert not _branch_lists_only(["--set-upstream-to=origin/main"])


# --------------------------------------------------------------------------
# status

STATUS = ("## main...origin/main [ahead 2, behind 1]\n"
          "M  src/a.py\n"
          " M src/b.py\n"
          "MM src/c.py\n"
          "?? new.txt\n"
          "UU merge.py\n"
          "A  added.py\n")


def test_parse_status():
    st = parse_status(STATUS)
    assert (st["branch"], st["upstream"], st["ahead"], st["behind"]) == ("main", "origin/main", 2, 1)
    assert (st["staged"], st["unstaged"], st["untracked"], st["conflicts"]) == (3, 2, 1, 1)
    st = parse_status("## feature\n")
    assert st["branch"] == "feature" and st["upstream"] == ""
    st = parse_status("## gone-branch...origin/gone [gone]\n")
    assert st["upstream"] == ""


def test_status_chart():
    p = plain(status_chart(parse_status(STATUS)))
    lines = p.split("\n")
    assert lines[0].startswith("main") and "ahead" in lines[0] and "origin/main" in lines[0]
    assert "staged" in lines[1] and "conflicts" in lines[1]
    clean = plain(status_chart(parse_status("## main...origin/main\n")))
    assert "clean" in clean


# --------------------------------------------------------------------------
# blame

BLAME = ("abc 1 1 1\nauthor Ada\nauthor-mail <a@x>\n\tline one\n"
         "abc 2 2\nauthor Ada\n\tauthor Bob is mentioned here\n"
         "def 3 3 1\nauthor Bob\n\tline three\n")


def test_parse_blame_authors_counts_headers_only():
    assert parse_blame_authors(BLAME) == [(2, "Ada"), (1, "Bob")]


def test_blame_chart():
    p = plain(blame_chart(parse_blame_authors(BLAME), cols=100))
    assert p.split("\n")[0].startswith("Ada") and " 67%" in p
    assert "3 lines, 2 authors" in p


# --------------------------------------------------------------------------
# log --stat: churn in order


def test_churn_line_is_in_time_order():
    numstat = parse_git_numstat("\x01300\n10\t0\ta\n\x01200\n50\t50\ta\n\x01100\n5\t5\ta\n")
    text = churn_line(numstat, cols=100)
    assert "t=spark;d=10,100,10" in text        # oldest first
    assert "120 over 3 commits, largest 100" in plain(text)


# --------------------------------------------------------------------------
# the CLI, on this repository


def run_cli(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, "-m", "gracefall.cli", *args],
                          capture_output=True, text=True, env=e)


needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git not installed")


@needs_git
def test_git_shortlog_recipe_on_this_repository():
    r = run_cli("fmt", "git", "shortlog", "-sn", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert "commits," in p and "authors" in p
    assert "\x1b]4700" not in r.stdout


@needs_git
def test_git_branch_and_status_recipes_on_this_repository():
    r = run_cli("fmt", "git", "branch", "-v", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    assert re.search(r"ahead|no upstream|upstream gone", SGR.sub("", r.stdout))
    r = run_cli("fmt", "git", "status", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    assert "working tree" in SGR.sub("", r.stdout)


@needs_git
def test_git_blame_and_diff_recipes_on_this_repository():
    r = run_cli("fmt", "git", "blame", "README.md", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    assert "lines," in SGR.sub("", r.stdout)
    # a diff of two commits always has files; the working tree may be clean
    r = run_cli("fmt", "git", "diff", "HEAD~1", "HEAD", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert p == "" or "total" in p


@needs_git
def test_git_log_stat_adds_the_churn_line():
    r = run_cli("fmt", "git", "log", "--stat", "-3", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert "commits" in p and "lines per commit" in p
    plain_log = SGR.sub("", run_cli("fmt", "git", "log", "-3", env={"COLUMNS": "100"}).stdout)
    assert "lines per commit" not in plain_log
