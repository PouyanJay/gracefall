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
from gracefall.recipes import (PingChart, TestChart, git_activity,
                               init_script, parse_df, parse_du,
                               parse_ping_line, parse_test_summary)

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
    mounts = [m for m, _, _ in rows]
    # Data and root, and a mounted disk with a space in its name. Not the
    # APFS helpers, not devfs, not the automount map.
    assert mounts == ["/", "/System/Volumes/Data", "/Volumes/Backup Disk"]
    assert rows[1] == ("/System/Volumes/Data", 328706476, 971298980)


def test_df_on_linux():
    rows = parse_df(DF_LINUX)
    assert [m for m, _, _ in rows] == ["/", "/data"]


def test_df_ignores_garbage_lines():
    assert parse_df("Filesystem\nnot a df line\n") == []
    assert parse_df("") == []


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
        assert r["mode"] in ("before", "wrap")
        assert r["help"]
        assert callable(r["fn"])


def test_matches_narrow_git_and_npm_to_the_right_subcommand():
    assert recipes.get("git")["matches"](["log", "--oneline"])
    assert not recipes.get("git")["matches"](["status"])
    assert not recipes.get("git")["matches"]([])
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
    r = run_cli("fmt", "git", "status")
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


@pytest.mark.skipif(not shutil.which("df"), reason="df not installed")
def test_df_recipe_draws_at_least_the_root():
    r = run_cli("fmt", "df", "/")
    assert r.returncode == 0
    assert "▁" in r.stdout or "█" in r.stdout


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
