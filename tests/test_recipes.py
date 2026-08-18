"""Recipes: charts added to commands people already run.

The parsers are pure functions over real command output, so they are
tested against captured output from macOS and Linux. The shell integration
is tested by parsing what `gfl init` prints with the shells themselves, and
the pty relay by wrapping a real command.
"""

import os
import re
import shutil
import subprocess
import sys

import pytest

from gracefall import recipes, strip_spans
from gracefall import MAX_ATTRS
from gracefall.recipes import (PingChart, TestChart, df_panel,
                               git_activity, git_dashboard, git_query_args,
                               git_when, git_window, init_script, parse_df,
                               parse_df_full, parse_du, parse_git_log,
                               parse_git_numstat, parse_ping_line,
                               parse_test_summary, watch)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return SGR.sub("", strip_spans(s))


# --------------------------------------------------------------------------
# df


DF_MACOS = """\
Filesystem     1024-blocks      Used Available Capacity  Mounted on
/dev/disk3s1s1   971298980  12274580 611282264     2%    /
devfs                  205       205         0   100%    /dev
/dev/disk3s6     971298980   8388628 611282264     2%    /System/Volumes/VM
/dev/disk3s2     971298980   8865992 611282264     2%    /System/Volumes/Preboot
/dev/disk3s5     971298980 328706476 611282264    35%    /System/Volumes/Data
map auto_home            0         0         0   100%    /System/Volumes/Data/home
/dev/disk4s1        976000    900000     76000    93%    /Volumes/Backup Disk
"""

DF_LINUX = """\
Filesystem     1024-blocks     Used Available Capacity Mounted on
udev               8000000        0   8000000       0% /dev
tmpfs              1600000     2000   1598000       1% /run
/dev/nvme0n1p2   490000000 210000000 255000000      46% /
tmpfs              8000000        0   8000000       0% /dev/shm
/dev/nvme0n1p1      523000     6000    517000       2% /boot/efi
/dev/sda1       1900000000 1700000000 100000000      95% /data
"""


def test_df_keeps_the_volumes_a_person_means():
    rows = parse_df(DF_MACOS)
    mounts = [m for m, _, _, _ in rows]
    # Data and root, and a mounted disk with a space in its name. Not the
    # APFS helpers, not devfs, not the automount map.
    assert mounts == ["/", "/System/Volumes/Data", "/Volumes/Backup Disk"]
    assert rows[1] == ("/System/Volumes/Data", 328706476, 971298980, 611282264)


def test_df_on_linux():
    rows = parse_df(DF_LINUX)
    assert [m for m, _, _, _ in rows] == ["/", "/data"]


def test_df_ignores_garbage_lines():
    assert parse_df("Filesystem\nnot a df line\n") == []
    assert parse_df("") == []


DF_MACOS_INODES = """\
Filesystem     1024-blocks      Used Available Capacity iused      ifree %iused  Mounted on
/dev/disk3s1s1   971298980  12274580 611282264     2%  458726 4293873116    0%   /
devfs                  205       205         0   100%     712          0  100%   /dev
/dev/disk3s5     971298980 328706476 611282264    35% 3429738 6154973800    0%   /System/Volumes/Data
map auto_home            0         0         0   100%       0          0  100%   /System/Volumes/Data/home
/dev/disk4s1        976000    900000     76000    93%   12000      88000   12%   /Volumes/Backup Disk
"""

DF_LINUX_INODES = """\
Filesystem       Inodes  IUsed    IFree IUse% Mounted on
udev             990000    500   989500    1% /dev
/dev/nvme0n1p2 30000000 900000 29100000    3% /
/dev/sda1      12000000 11000000 1000000  92% /data
"""

DF_BUSYBOX_INODES = """\
Filesystem              Inodes      Used Available Capacity Mounted on
overlay               60710912   2096678  58614234   3% /
"""


def test_parse_df_full_keeps_every_volume_in_df_order():
    rows = parse_df_full(DF_MACOS)
    assert [r["mount"] for r in rows] == [
        "/", "/dev", "/System/Volumes/VM", "/System/Volumes/Preboot",
        "/System/Volumes/Data", "/System/Volumes/Data/home", "/Volumes/Backup Disk"]
    # a filesystem with a space and a mount with a space both survive
    assert rows[5]["fs"] == "map auto_home" and rows[5]["total"] == 0
    assert rows[6] == dict(fs="/dev/disk4s1", mount="/Volumes/Backup Disk",
                           used=900000, total=976000, avail=76000,
                           iused=None, ifree=None)


def test_parse_df_full_joins_inodes_by_mount_on_macos_and_linux():
    rows = parse_df_full(DF_MACOS, DF_MACOS_INODES)
    by = {r["mount"]: r for r in rows}
    assert (by["/"]["iused"], by["/"]["ifree"]) == (458726, 4293873116)
    assert by["/Volumes/Backup Disk"]["iused"] == 12000
    assert by["/System/Volumes/VM"]["iused"] is None      # not in the -i run
    rows = parse_df_full(DF_LINUX, DF_LINUX_INODES)
    by = {r["mount"]: r for r in rows}
    assert (by["/data"]["iused"], by["/data"]["ifree"]) == (11000000, 1000000)
    rows = parse_df_full("Filesystem 1024-blocks Used Available Capacity Mounted on\n"
                         "overlay 100 50 50 50% /\n", DF_BUSYBOX_INODES)
    assert (rows[0]["iused"], rows[0]["ifree"]) == (2096678, 58614234)


def test_df_panel_covers_every_volume_most_full_first():
    rows = parse_df_full(DF_MACOS, DF_MACOS_INODES)
    text = df_panel(rows, cols=120)
    p = plain(text)
    lines = p.split("\n")
    assert len(lines) == len(rows)
    # sorted by fullness, zero-size pseudo volumes last and unmetered
    assert lines[0].startswith("/dev ") and "100%" in lines[0]
    assert lines[1].startswith("/Volumes/Backup Disk") and "93%" in lines[1]
    # long mounts keep their end, which is the part that tells them apart
    assert lines[-1].startswith("…ystem/Volumes/Data/home") and "%" not in lines[-1]
    # every real volume: space meter, percent, used / total, inode meter, device
    assert re.search(r"/Volumes/Backup Disk\s+\S+\s+93%\s+879M / 953M\s+inodes \S+\s+12%\s+/dev/disk4s1", lines[1])
    # a space meter for the six sized volumes, an inode meter for the
    # four the -i run reported on
    assert text.count("t=meter") == 6 + 4
    for cols in (60, 80, 100, 140):
        for line in plain(df_panel(rows, cols=cols)).split("\n"):
            assert len(line) <= cols, (cols, line)


def test_df_panel_without_inodes_has_no_inode_column():
    p = plain(df_panel(parse_df_full(DF_LINUX), cols=120))
    assert "inodes" not in p and p.count("\n") == 5


def test_watch_repaints_in_place_and_clears_the_previous_frame():
    import io
    frames = iter(["a\nb\nc", "d"])
    out = io.StringIO()
    watch(lambda: next(frames), every=0, out=out, ticks=2)
    s = out.getvalue()
    # first frame from the current line, second frame moves up over the
    # first (three lines plus the hint) and clears from there down
    assert s.startswith("\r\x1b[J")
    assert "\x1b[4A\r\x1b[J" in s
    assert s.count("ctrl-c to stop") == 2
    assert "d" in s.split("\x1b[4A")[1]


def test_watch_strips_envelopes_when_not_emitting():
    import io
    from gracefall import meter
    out = io.StringIO()
    watch(lambda: meter(0.5), every=0, emit=False, out=out, ticks=1)
    assert "\x1b]4700" not in out.getvalue()


# --------------------------------------------------------------------------
# du


def test_du_parses_tab_and_space_separated():
    assert parse_du("376\tsrc\n404\ttests\n") == [("src", 376), ("tests", 404)]
    assert parse_du("12  a dir with spaces\n") == [("a dir with spaces", 12)]
    assert parse_du("du: cannot read\n") == []


# --------------------------------------------------------------------------
# git


def test_git_activity_buckets_by_day_oldest_first():
    now = 1_000_000_000
    day = 86400
    stamps = [now - 10, now - day - 10, now - day - 20, now - 55 * day - 100]
    counts = git_activity(stamps, days=56, now=now)
    assert len(counts) == 56
    assert counts[-1] == 1          # today
    assert counts[-2] == 2          # yesterday
    assert counts[0] == 1           # the oldest day still in range
    assert sum(counts) == 4


def test_git_activity_drops_out_of_range():
    now = 1_000_000_000
    counts = git_activity([now - 100 * 86400, now + 5], days=56, now=now)
    assert sum(counts) == 0


@pytest.mark.parametrize("argv,filters,bounded,timed", [
    (["log"], [], False, []),
    # display flags are dropped, count limits are kept and bound the log
    (["log", "--oneline", "--graph", "-20"], ["-20"], True, []),
    (["log", "-p", "--stat", "-n", "5"], ["-n", "5"], True, []),
    (["log", "-n5", "--decorate"], ["-n5"], True, []),
    # filters that do not bound the log keep the eight-week default
    (["log", "--author=me", "--no-merges"], ["--author=me", "--no-merges"],
     False, []),
    (["log", "--author", "me"], ["--author", "me"], False, []),
    (["log", "-S", "needle", "-Sfoo", "-i"], ["-S", "needle", "-Sfoo", "-i"],
     False, []),
    # time bounds are forwarded and collected in rev-parse spelling
    (["log", "--since=2.weeks"], ["--since=2.weeks"], True, ["--since=2.weeks"]),
    (["log", "--since", "2 weeks ago", "--until=2026-08-15"],
     ["--since", "2 weeks ago", "--until=2026-08-15"], True,
     ["--since=2 weeks ago", "--until=2026-08-15"]),
    # a dropped flag's separate value is not mistaken for a revision
    (["log", "--date", "iso", "--format", "%h"], [], False, []),
    # paths narrow, revisions bound; pathspecs after -- pass through
    (["log", "tests"], ["tests"], False, []),
    (["log", "HEAD~3..HEAD"], ["HEAD~3..HEAD"], True, []),
    (["log", "v0.1.0"], ["v0.1.0"], True, []),
    (["log", "--oneline", "--", "src", "--not-a-flag"],
     ["--", "src", "--not-a-flag"], False, []),
])
def test_git_query_args_keeps_filters_and_drops_display(argv, filters,
                                                        bounded, timed):
    assert git_query_args(argv) == (filters, bounded, timed)


def test_parse_git_log():
    text = "1700000000\tAda Lovelace\n1700000100\tBob\ngarbage\n"
    assert parse_git_log(text) == [(1700000000, "Ada Lovelace"),
                                   (1700000100, "Bob")]


def test_parse_git_numstat_sums_lines_and_charges_top_level_paths():
    text = ("\x011700000000\n"
            "10\t2\tsrc/gracefall/cli.py\n"
            "3\t0\tREADME.md\n"
            "-\t-\tdocs/demo.gif\n"              # binary: touched, no lines
            "\x011700000100\n"
            "1\t1\tsrc/{old => new}/x.py\n"      # rename, charged to new
            "4\t4\told.txt => new.txt\n"
            "\n")
    assert parse_git_numstat(text) == [
        (1700000000, 15, {"src": 12, "README.md": 3, "docs": 0}),
        (1700000100, 10, {"src": 2, "new.txt": 8}),
    ]


def _local(y, m, d, h):
    import time
    return int(time.mktime((y, m, d, h, 0, 0, 0, 0, -1)))


def test_git_when_is_weekday_by_local_hour():
    # 2026-08-17 is a Monday.
    grid = git_when([_local(2026, 8, 17, 9), _local(2026, 8, 17, 9),
                     _local(2026, 8, 23, 23)])
    assert len(grid) == 7 and all(len(r) == 24 for r in grid)
    assert grid[0][9] == 2
    assert grid[6][23] == 1
    assert sum(map(sum, grid)) == 3


def test_git_window():
    now = 1_000_000_000
    day = 86400
    commits = [(now - 3 * day, "a"), (now - 10 * day, "b")]
    assert git_window(commits, False, now=now) == (now - 56 * day, now)
    # bounded by count or revision: the span of the commits seen
    assert git_window(commits, True, now=now) == (now - 10 * day, now - 3 * day)
    # bounded in time: the window the user asked for, up to now
    assert git_window(commits, True, since=now - 30 * day, now=now) == \
        (now - 30 * day, now)
    assert git_window(commits, True, until=now - day, now=now) == \
        (now - 10 * day, now - day)


def _fake_history(now):
    day = 86400
    commits = [(now - i * day - 3600 * (i % 5), "Ada" if i % 3 else "Bob")
               for i in range(20)]
    numstat = [(ts, 10 * (i + 1), {"src": 6 * (i + 1), "tests": 4 * (i + 1)})
               for i, (ts, _) in enumerate(commits)]
    return commits, numstat


def test_git_dashboard_has_every_section_and_keeps_labels_outside_spans():
    now = 1_700_000_000
    commits, numstat = _fake_history(now)
    text = git_dashboard(commits, numstat, git_window(commits, False, now=now))
    p = plain(text)
    for section in ("commits, last 8 weeks", "by weekday and hour",
                    "Mon Tue", "Sun", "0h", "by author", "Ada", "Bob",
                    "lines per commit", "median", "by path", "src", "tests"):
        assert section in p, section
    spans = re.findall(r"\x1b\]4700;(t=[^\x1b]*)\x1b\\(.*?)\x1b\]4700;\x1b\\",
                       text, re.S)
    assert len(spans) >= 8
    for attrs, body in spans:
        assert len(attrs) <= MAX_ATTRS
        # A span's fallback is the chart alone; the day names, numbers and
        # section labels are ordinary text around it, never inside it.
        assert "\n" not in body
        assert not re.search(r"[A-Za-z]", SGR.sub("", body)), body
    kinds = [a.split(";")[0] for a, _ in spans]
    assert kinds.count("t=heat") == 4 and "t=dist" in kinds
    assert "t=spark" in kinds and "t=meter" in kinds


def test_git_dashboard_without_numstat_skips_size_and_paths():
    now = 1_700_000_000
    commits, _ = _fake_history(now)
    p = plain(git_dashboard(commits, None, git_window(commits, False, now=now)))
    assert "by author" in p
    assert "lines per commit" not in p and "by path" not in p


def test_git_dashboard_is_none_when_nothing_is_in_the_window():
    now = 1_700_000_000
    commits = [(now - 400 * 86400, "Ada")]
    assert git_dashboard(commits, None, git_window(commits, False, now=now)) is None


# --------------------------------------------------------------------------
# ping


@pytest.mark.parametrize("line,expected", [
    (b"64 bytes from 127.0.0.1: icmp_seq=0 ttl=64 time=0.045 ms", 0.045),
    (b"64 bytes from 1.1.1.1: icmp_seq=3 ttl=57 time=12.7 ms", 12.7),
    (b"64 bytes from host (10.0.0.1): icmp_seq=1 ttl=64 time=0.5 ms", 0.5),
    (b"64 bytes from 10.0.0.1: icmp_seq=1 ttl=64 time<1 ms", 1.0),
    (b"Request timeout for icmp_seq 4", None),
    (b"PING 127.0.0.1 (127.0.0.1): 56 data bytes", None),
    (b"", None),
])
def test_ping_line_parsing(line, expected):
    assert parse_ping_line(line) == expected


def test_ping_chart_keeps_a_window_and_reports_the_current_value():
    c = PingChart()
    assert c.feed(b"PING x") is None
    for i in range(50):
        line = c.feed(b"64 bytes: icmp_seq=%d ttl=64 time=%d.0 ms" % (i, i + 1))
    assert len(c.times) == 40
    p = plain(line)
    assert "50 ms" in p and "min 11" in p and "max 50" in p
    assert "▁" in p or "█" in p     # a spark was drawn


# --------------------------------------------------------------------------
# test runners


@pytest.mark.parametrize("text,expected", [
    ("=================== 17 passed in 0.01s ===================",
     dict(passed=17, failed=0, skipped=0)),
    ("17 passed in 0.01s",
     dict(passed=17, failed=0, skipped=0)),
    ("========= 1 failed, 2 passed, 3 skipped, 1 xfailed in 0.02s =========",
     dict(passed=2, failed=1, skipped=4)),
    ("=== 2 passed, 1 error in 1.2s ===",
     dict(passed=2, failed=1, skipped=0)),
    ("Tests:       2 failed, 10 passed, 12 total\nTime:        1.2 s",
     dict(passed=10, failed=2, skipped=0)),
    ("Tests:       3 skipped, 9 passed, 12 total",
     dict(passed=9, failed=0, skipped=3)),
    ("\n  10 passing (120ms)\n  2 failing\n",
     dict(passed=10, failed=2, skipped=0)),
    ("\x1b[32m===== \x1b[0m5 passed\x1b[32m in 0.1s =====\x1b[0m",
     dict(passed=5, failed=0, skipped=0)),
    ("nothing to see here", None),
    ("", None),
])
def test_test_summary_parsing(text, expected):
    assert parse_test_summary(text) == expected


def test_test_summary_uses_the_last_run_in_a_transcript():
    two = "3 passed in 0.1s\n...\n1 failed, 2 passed in 0.2s\n"
    assert parse_test_summary(two) == dict(passed=2, failed=1, skipped=0)


def test_test_chart_is_teal_when_green_and_coral_when_not():
    ok = TestChart().finish("17 passed in 0.01s")
    bad = TestChart().finish("1 failed, 2 passed in 0.02s")
    assert "17 passed" in plain(ok) and "failed" not in plain(ok)
    assert "2 passed, 1 failed" in plain(bad)
    assert "c=teal" in ok and "c=coral" in bad
    assert TestChart().finish("no summary here") is None


# --------------------------------------------------------------------------
# the registry and the shell


def test_every_recipe_has_a_mode_a_help_and_a_callable():
    for name in recipes.names():
        r = recipes.get(name)
        assert r["mode"] in ("after", "wrap")
        assert r["help"]
        assert callable(r["fn"])


def test_matches_narrow_git_and_npm_to_the_right_subcommand():
    assert recipes.get("git")["matches"](["log", "--oneline"])
    assert recipes.get("git")["matches"](["status"])
    assert not recipes.get("git")["matches"](["push"])
    assert not recipes.get("git")["matches"]([])
    # the shell test is derived from the same registry
    when = recipes.get("git")["when"]()
    for name in ("log", "shortlog", "diff", "branch", "status", "blame"):
        assert f'"$1" == {name}' in when
    assert '"$2" == list' in recipes.get("gh")["when"]()
    assert recipes.get("npm")["matches"](["test"])
    assert not recipes.get("npm")["matches"](["install"])
    assert recipes.get("df")["matches"](["-h", "/"])


@pytest.mark.parametrize("shell", ["zsh", "bash"])
def test_init_script_parses_in_the_shell_it_is_for(shell):
    if not shutil.which(shell):
        pytest.skip(f"{shell} not installed")
    script = init_script(shell)
    r = subprocess.run([shell, "-n"], input=script, text=True,
                       capture_output=True)
    assert r.returncode == 0, r.stderr


def test_init_script_never_runs_a_recipe_off_a_terminal():
    # Every function is guarded by -t 1, and every one still runs the real
    # command. That is the whole safety story in two greps.
    script = init_script("zsh")
    for name in recipes.names():
        body = script.split(f"\n{name}() {{\n", 1)[1].split("\n}\n", 1)[0]
        assert "-t 1" in body, name
        assert f'command {name} "$@"' in body, name
        if recipes.get(name)["mode"] == "after":
            # The chart comes after the command, and the command's own
            # exit status is what the function returns.
            assert body.index(f'command {name}') < body.index("gfl fmt"), name
            assert "return $rc" in body, name


def test_init_script_rejects_other_shells():
    with pytest.raises(ValueError):
        init_script("fish")


# --------------------------------------------------------------------------
# the CLI


def run_cli(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, "-m", "gracefall.cli", *args],
                          capture_output=True, text=True, env=e)


def test_fmt_lists_recipes_and_says_how_to_turn_them_on():
    r = run_cli("fmt")
    assert r.returncode == 0
    for name in recipes.names():
        assert name in r.stdout
    assert "gfl init zsh" in r.stdout


def test_fmt_unknown_recipe_is_a_clean_error():
    r = run_cli("fmt", "nope")
    assert r.returncode != 0
    assert "no recipe" in r.stderr
    assert "Traceback" not in r.stderr


def test_fmt_is_silent_for_a_case_the_recipe_is_not_for():
    r = run_cli("fmt", "git", "push")
    assert r.returncode == 0 and r.stdout == ""
    r = run_cli("fmt", "git", "branch", "new-branch-name")
    assert r.returncode == 0 and r.stdout == ""


def test_init_prints_functions_for_every_recipe():
    r = run_cli("init", "zsh")
    assert r.returncode == 0
    for name in recipes.names():
        assert f"{name}() {{" in r.stdout


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_git_log_recipe_on_this_repository():
    # The repo this test lives in has commits, so the recipe has something
    # to draw. Piped, so what comes out is the fallback.
    r = run_cli("fmt", "git", "log")
    assert r.returncode == 0
    p = SGR.sub("", r.stdout)
    assert "commits, last 8 weeks" in p
    assert "\x1b]4700" not in r.stdout        # piped: no envelopes


@pytest.mark.skipif(not shutil.which("git"), reason="git not installed")
def test_git_log_full_view_on_this_repository():
    r = run_cli("fmt", "--full", "git", "log")
    assert r.returncode == 0
    p = SGR.sub("", r.stdout)
    assert "by weekday and hour" in p and "by author" in p
    # the environment switch is the same view, for the shell function
    e = run_cli("fmt", "git", "log", env={"GFL_FULL": "1"})
    assert SGR.sub("", e.stdout) == p
    # and the log's own arguments still narrow the chart
    n = run_cli("fmt", "git", "log", "-3", "--oneline")
    # CI checks out a shallow clone: ask git how many commits -3 can see
    depth = subprocess.run(["git", "rev-list", "--count", "--max-count=3", "HEAD"],
                           capture_output=True, text=True).stdout.strip()
    assert n.returncode == 0 and f"{depth} total" in SGR.sub("", n.stdout)


@pytest.mark.skipif(not shutil.which("df"), reason="df not installed")
def test_df_recipe_draws_at_least_the_root():
    r = run_cli("fmt", "df", "/")
    assert r.returncode == 0
    assert "▁" in r.stdout or "█" in r.stdout


@pytest.mark.skipif(not shutil.which("df"), reason="df not installed")
def test_df_full_view_lists_every_volume_df_does():
    r = run_cli("fmt", "--full", "df", env={"COLUMNS": "120"})
    assert r.returncode == 0
    n = len(subprocess.run(["df", "-Pk"], capture_output=True, text=True)
            .stdout.splitlines()) - 1
    assert SGR.sub("", r.stdout).count("\n") == n
    assert "%" in r.stdout


def test_wrap_relays_output_and_exit_code_and_adds_the_chart(tmp_path):
    # A stand-in for a test runner: prints a pytest-shaped summary and
    # exits 3. Everything it printed must come through byte for byte, the
    # exit code must survive, and the chart must follow the summary.
    fake = tmp_path / "fakerunner"
    fake.write_text("#!/bin/sh\necho collecting\necho '2 passed, 1 failed in 0.1s'\nexit 3\n")
    fake.chmod(0o755)
    from gracefall.recipes import _wrap
    import io

    class Sink(io.BytesIO):
        def flush(self):
            pass
    out = Sink()
    code = _wrap([str(fake)], TestChart(), emit=False, out=out)
    text = out.getvalue().decode()
    assert code == 3
    assert "collecting" in text
    assert "2 passed, 1 failed in 0.1s" in text
    assert "tests" in text and "2 passed, 1 failed" in text
    assert "\x1b]4700" not in text            # emit=False strips


def test_wrap_of_a_missing_command_is_a_clean_127():
    from gracefall.recipes import _wrap
    import io
    code = _wrap(["definitely-not-a-command-xyz"], TestChart(), emit=False,
                 out=io.BytesIO())
    assert code == 127
