"""Memory, swap, ls, du depth, iostat and smartctl: parsers against
captured macOS and Linux output, chart layout, and the CLI where the
command exists on this machine."""

import os
import re
import shutil
import subprocess
import sys

import pytest

from gracefall import strip_spans
from gracefall.recipes import du_chart, du_depth
from gracefall.recipes_sys import (IostatChart, _ls_long, _swapon_shows, _vm_stat_once,
                                   ls_chart, ls_sizes, memory_chart, parse_free,
                                   parse_smart, parse_swapon, parse_swapusage,
                                   parse_vm_stat, smart_chart, swap_chart)

SGR = re.compile(r"\x1b\[[0-9;]*m")


def plain(s):
    return SGR.sub("", strip_spans(s))


# --------------------------------------------------------------------------
# memory

FREE = """\
               total        used        free      shared  buff/cache   available
Mem:     16656486400  6291456000  2147483648   419430400  8217546752 9663676416
Swap:     2147483648   536870912  1610612736
"""

VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    13517.
Pages active:                                 433878.
Pages inactive:                               431836.
Pages speculative:                              1641.
Pages throttled:                                   0.
Pages wired down:                             202076.
Pages purgeable:                                   2.
"Translation faults":                     3164019447.
File-backed pages:                            296462.
Anonymous pages:                              570893.
Pages stored in compressor:                  2291777.
Pages occupied by compressor:                 957636.
"""


def test_parse_free_uses_available_for_used():
    m = parse_free(FREE)
    assert m["total"] == 16656486400
    assert m["used"] == 16656486400 - 9663676416
    assert m["cached"] == 8217546752
    assert (m["swap_total"], m["swap_used"]) == (2147483648, 536870912)
    assert parse_free("nonsense\n") is None


def test_parse_vm_stat_is_activity_monitor_memory_used():
    memsize = 34359738368
    m = parse_vm_stat(VM_STAT, memsize)
    ps = 16384
    app = (570893 - 2) * ps
    assert m["total"] == memsize
    assert m["used"] == app + 202076 * ps + 957636 * ps
    assert dict(m["kinds"])["cached"] == 296462 * ps
    assert parse_vm_stat("", memsize) is None


def test_parse_swapusage():
    assert parse_swapusage("vm.swapusage: total = 6144.00M  used = 4801.62M  free = 1342.38M  (encrypted)") \
        == (int(4801.62 * 1024 ** 2), 6144 * 1024 ** 2)
    assert parse_swapusage("total = 2.00G  used = 512.00M  free = 1.50G") == (512 * 1024 ** 2, 2 * 1024 ** 3)
    assert parse_swapusage("junk") is None


def test_memory_chart_has_the_total_the_breakdown_and_swap():
    text = memory_chart(parse_free(FREE), cols=100)
    p = plain(text)
    lines = p.split("\n")
    assert lines[0].startswith("memory") and "42%" in lines[0]
    assert "used" in lines[1] and "cached" in lines[1] and "free" in lines[1]
    assert lines[2].startswith("swap") and "25%" in lines[2]
    assert text.count("t=meter") == 5
    m = parse_vm_stat(VM_STAT, 34359738368)
    p = plain(memory_chart(m, cols=100))
    assert "app" in p and "wired" in p and "compressed" in p and "swap" not in p
    for cols in (60, 80, 100, 140):
        for line in plain(memory_chart(parse_free(FREE), cols=cols)).split("\n")[::2]:
            assert len(line) <= cols, (cols, line)


def test_vm_stat_only_the_one_shot_form():
    assert _vm_stat_once([])
    assert not _vm_stat_once(["1"])


# --------------------------------------------------------------------------
# swap

SWAPON = "/dev/dm-1  8589934592 1073741824\n/swapfile  2147483648  0\n"


def test_parse_swapon_and_chart():
    rows = parse_swapon(SWAPON)
    assert rows == [("/dev/dm-1", 8589934592, 1073741824), ("/swapfile", 2147483648, 0)]
    p = plain(swap_chart(rows, cols=100))
    lines = p.split("\n")
    assert lines[0].startswith("/dev/dm-1") and "12%" in lines[0]
    assert lines[-1].startswith("total") and "10%" in lines[-1]
    assert _swapon_shows([]) and _swapon_shows(["--show"]) and not _swapon_shows(["-a"])
    assert not _swapon_shows(["/dev/sda2"])


# --------------------------------------------------------------------------
# ls -l


def test_ls_sizes_and_chart(tmp_path):
    for name, size in (("big.bin", 5000), ("mid.txt", 1200), ("small.md", 100),
                       (".hidden", 9000), ("tiny", 1)):
        (tmp_path / name).write_bytes(b"x" * size)
    (tmp_path / "sub").mkdir()
    rows = ls_sizes([str(tmp_path)])
    assert sorted(rows) == [("big.bin", 5000), ("mid.txt", 1200), ("small.md", 100), ("tiny", 1)]
    assert (".hidden", 9000) in ls_sizes([str(tmp_path)], hidden=True)
    p = plain(ls_chart(rows, cols=100))
    lines = p.split("\n")
    assert lines[0].startswith("largest") and "big.bin" in lines[0]
    assert lines[-1].startswith("sizes") and "4 files" in lines[-1]
    assert ls_chart([], cols=100) is None
    assert _ls_long(["-l"]) and _ls_long(["-la"]) and _ls_long(["-lS", "src"])
    assert not _ls_long([]) and not _ls_long(["-a"]) and not _ls_long(["--color"])


# --------------------------------------------------------------------------
# du --max-depth


def test_du_depth_reads_every_spelling():
    assert du_depth(["-h", "--max-depth=1"]) == 1
    assert du_depth(["--max-depth", "2", "."]) == 2
    assert du_depth(["-d", "1", "src"]) == 1
    assert du_depth(["-d1"]) == 1
    assert du_depth(["-sh", "*"]) is None


def test_du_chart_full_adds_a_dist():
    rows = [("a", 500), ("b", 300), ("c", 200), ("d", 1)]
    p = plain(du_chart(rows, cols=100))
    assert p.split("\n")[0].startswith("a") and "sizes" not in p
    p = plain(du_chart(rows, cols=100, full=True))
    assert "sizes" in p and "4 entries" in p


# --------------------------------------------------------------------------
# iostat

IOSTAT_MAC = b"""\
              disk0               disk4       cpu    load average
    KB/t  tps  MB/s     KB/t  tps  MB/s  us sy id   1m   5m   15m
   37.25   90  3.28   125.87    0  0.00  10  3 87  3.39 2.94 2.87
  107.17   48  5.00     0.00    0  0.00   7  4 90  3.39 2.94 2.87
"""

IOSTAT_LINUX = b"""\
Linux 6.8.0 (host) 	08/18/2026 	_x86_64_	(8 CPU)

avg-cpu:  %user   %nice %system %iowait  %steal   %idle
           1.20    0.00    0.50    0.10    0.00   98.20

Device             tps    kB_read/s    kB_wrtn/s    kB_dscd/s    kB_read    kB_wrtn    kB_dscd
sda              12.00       512.00      1536.00         0.00     100000     200000          0
nvme0n1           3.00      1024.00         0.00         0.00      50000      10000          0

"""


def test_iostat_chart_reads_macos_rows():
    c = IostatChart()
    lines = []
    for raw in IOSTAT_MAC.splitlines(keepends=True):
        got = c.feed(raw)
        if got:
            lines.append(plain(got))
    assert len(lines) == 2
    assert "3.28 MB/s" in lines[0] and "disk0 3.28" in lines[0] and "disk4 0.00" in lines[0]
    assert "5.00 MB/s" in lines[1]
    assert c.samples == [3.28, 5.0]


def test_iostat_swims_at_the_throughput_and_sleeps_when_the_disk_does():
    from gracefall.creature import Creature
    c = IostatChart(Creature("idle"))
    for raw in IOSTAT_MAC.splitlines(keepends=True):
        line = c.feed(raw)
    assert c.pet.mood == "working"                 # 5 MB/s against a 5 MB/s peak
    assert "5.00 MB/s" in plain(line)              # the chart is still the chart
    assert plain(line).endswith(plain(c.pet.frame(c.ticks)))
    quiet = IostatChart(Creature("idle"))
    quiet.feed("          0.00   0.00   0.00       0.00   0.00   0.00   1  2 97  1.0 1.0 1.0\n")
    assert quiet.pet.mood == "sleepy"
    # and with no creature the chart is exactly what it was
    row = "          0.00   0.00   0.00       0.00   0.00   0.00   1  2 97  1.0 1.0 1.0\n"
    assert plain(IostatChart().feed(row)) == plain(quiet.line())


def test_iostat_chart_reads_linux_reports():
    c = IostatChart()
    lines = [plain(g) for g in (c.feed(raw) for raw in IOSTAT_LINUX.splitlines(keepends=True)) if g]
    assert len(lines) == 1
    # (512 + 1536 + 1024) kB/s = 3.00 MB/s
    assert "3.00 MB/s" in lines[0] and "sda 2.00" in lines[0] and "nvme0n1 1.00" in lines[0]


# --------------------------------------------------------------------------
# smartctl

SMART_NVME = """\
=== START OF SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED

SMART/Health Information (NVMe Log 0x02)
Critical Warning:                   0x00
Temperature:                        34 Celsius
Available Spare:                    100%
Available Spare Threshold:          10%
Percentage Used:                    3%
Data Units Read:                    12,345,678 [6.32 TB]
Power On Hours:                     1,234
"""

SMART_ATA = """\
ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   095   095   000    Old_age   Always       -       21567
177 Wear_Leveling_Count     0x0013   094   094   000    Pre-fail  Always       -       130
194 Temperature_Celsius     0x0022   067   051   000    Old_age   Always       -       33 (Min/Max 18/49)
"""


def test_parse_smart_nvme_and_ata():
    assert parse_smart(SMART_NVME) == dict(temp=34, wear=3, spare=100, hours=1234)
    ata = parse_smart(SMART_ATA)
    assert ata == dict(reallocated=0, hours=21567, wear=6, temp=33)
    assert parse_smart("nothing here") == {}


def test_smart_chart():
    text = smart_chart(parse_smart(SMART_NVME))
    p = plain(text)
    assert "wear" in p and "3% used" in p
    assert "temperature" in p and "34 °C" in p
    assert "spare" in p and "100% left" in p
    assert "1,234 hours on" in p
    assert text.count("t=meter") == 3
    assert smart_chart({}) is None


# --------------------------------------------------------------------------
# the CLI, where the command exists


def run_cli(*args, env=None):
    e = dict(os.environ, **(env or {}))
    return subprocess.run([sys.executable, "-m", "gracefall.cli", *args],
                          capture_output=True, text=True, env=e)


@pytest.mark.skipif(not shutil.which("vm_stat"), reason="not macOS")
def test_vm_stat_recipe_here():
    r = run_cli("fmt", "vm_stat", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert p.strip().startswith("memory") and "wired" in p


@pytest.mark.skipif(not shutil.which("free"), reason="no free here")
def test_free_recipe_here():
    r = run_cli("fmt", "free", "-h", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    assert SGR.sub("", r.stdout).strip().startswith("memory")


def test_ls_and_du_recipes_here():
    r = run_cli("fmt", "ls", "-l", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    assert "largest" in SGR.sub("", r.stdout)
    r = run_cli("fmt", "ls", env={"COLUMNS": "100"})           # not the long form: silent
    assert r.returncode == 0 and r.stdout == ""
    r = run_cli("fmt", "du", "-h", "-d", "1", ".", env={"COLUMNS": "100"})
    assert r.returncode == 0, r.stderr
    p = SGR.sub("", r.stdout)
    assert "./src" in p                                 # entries, in du's own spelling
    assert not re.search(r"^\.\s", p, re.M)             # not the total of "." itself
