"""Disk, files and memory recipes: `free` and `vm_stat`, `swapon` and
`sysctl vm.swapusage`, `ls -l`, `iostat`, `smartctl -a`. (`du` lives in
recipes.py; its --max-depth form is handled there.)

Memory and swap are "after" charts from a query the recipe makes for
itself: `free -b` on Linux, `vm_stat` plus `sysctl hw.memsize` on macOS,
`swapon --show --bytes`, `sysctl vm.swapusage`. `ls -l` sizes come from
the filesystem directly. `iostat` and `smartctl` are relayed through a
pty like ping and pytest: iostat because it is live, smartctl because it
usually needs root and the only data we can rely on is the output the
person is already looking at.

Every parser is a pure function over captured output, and every chart is
meters, sparks and dists.
"""

import os
import re
import shutil
import stat

from . import SGR, dist, meter, spark
from .recipes import W, _fit, _human, _label_width, _run, _wrap, recipe

R = "\x1b[0m"
D = SGR["dim"]


def _cols():
    return shutil.get_terminal_size((80, 24)).columns


def _gb(b):
    return _human(b / 1024)


def _fill(frac):
    return "coral" if frac > 0.9 else "amber" if frac > 0.75 else "teal"


# --------------------------------------------------------------------------
# memory: free (Linux) and vm_stat (macOS)


def parse_free(text):
    """`free -b` into dict(total, used, free, cached, available, swap_total,
    swap_used) in bytes, or None. `used` is total minus available, which
    is what a person means by used; free's own used column excludes cache
    or includes it depending on the version."""
    mem = swap = None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "Mem:" and all(p.isdigit() for p in parts[1:]):
            mem = [int(p) for p in parts[1:]]
        elif len(parts) >= 3 and parts[0] == "Swap:" and all(p.isdigit() for p in parts[1:]):
            swap = [int(p) for p in parts[1:]]
    if not mem:
        return None
    total = mem[0]
    available = mem[5] if len(mem) > 5 else mem[2]
    cached = mem[4] if len(mem) > 4 else 0
    return dict(total=total, used=max(0, total - available), free=mem[2], cached=cached,
                available=available, kinds=[("used", max(0, total - available)),
                                            ("cached", cached), ("free", mem[2])],
                swap_total=swap[0] if swap else 0, swap_used=swap[1] if swap else 0)


_VM = re.compile(r"^(Pages free|Pages active|Pages inactive|Pages speculative|"
                 r"Pages wired down|Pages purgeable|File-backed pages|Anonymous pages|"
                 r"Pages occupied by compressor):\s+(\d+)\.", re.M)


def parse_vm_stat(text, memsize, pagesize=None):
    """`vm_stat` plus the machine's memory size into the same dict as
    parse_free. Used is what Activity Monitor calls Memory Used: app
    memory (anonymous less purgeable) plus wired plus compressed."""
    m = re.search(r"page size of (\d+) bytes", text)
    ps = pagesize or (int(m.group(1)) if m else 4096)
    pages = {k: int(v) for k, v in _VM.findall(text)}
    if not pages or not memsize:
        return None
    app = max(0, pages.get("Anonymous pages", 0) - pages.get("Pages purgeable", 0)) * ps
    wired = pages.get("Pages wired down", 0) * ps
    compressed = pages.get("Pages occupied by compressor", 0) * ps
    cached = pages.get("File-backed pages", 0) * ps
    used = min(memsize, app + wired + compressed)
    return dict(total=memsize, used=used, free=max(0, memsize - used - cached), cached=cached,
                available=max(0, memsize - used),
                kinds=[("app", app), ("wired", wired), ("compressed", compressed),
                       ("cached", cached)],
                swap_total=0, swap_used=0)


def parse_swapusage(text):
    """`sysctl vm.swapusage` into (used, total) in bytes, or None."""
    m = re.search(r"total = ([\d.]+)([KMGT]?)\s+used = ([\d.]+)([KMGT]?)", text)
    if not m:
        return None
    mult = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}
    total = float(m.group(1)) * mult[m.group(2)]
    used = float(m.group(3)) * mult[m.group(4)]
    return int(used), int(total)


def memory_chart(mem, cols=None):
    """Three lines: memory used against total, the breakdown as small
    meters on one scale, and swap when there is any. Pure."""
    if not mem or not mem["total"]:
        return None
    total = mem["total"]
    frac = mem["used"] / total
    lw = len("memory")
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 22))
    lines = [f"{D}{'memory':<{lw}}{R}  " + meter(frac, width=mw, color=_fill(frac))
             + f"  {_gb(mem['used'])} / {_gb(total)}  {D}{round(100 * frac)}%{R}"]
    parts = []
    for name, n in mem["kinds"]:
        parts.append(f"{D}{name}{R} " + meter(n / total, width=8, color="blue" if name != "cached" and name != "free" else "dim")
                     + f" {_gb(n)}")
    lines.append(f"{'':<{lw}}  " + "  ".join(parts))
    if mem["swap_total"]:
        sf = mem["swap_used"] / mem["swap_total"]
        lines.append(f"{D}{'swap':<{lw}}{R}  " + meter(sf, width=mw, color=_fill(sf))
                     + f"  {_gb(mem['swap_used'])} / {_gb(mem['swap_total'])}  {D}{round(100 * sf)}%{R}")
    return "\n".join(lines)


@recipe("free", "after", help="free: memory used against total, the breakdown, and swap")
def free(argv, full=False):
    if not shutil.which("free"):
        return None
    out = _run(["free", "-b"])
    if not out:
        return None
    return memory_chart(parse_free(out), cols=_cols())


def _vm_stat_once(rest):
    """`vm_stat 1` runs until interrupted; only the one-shot form gets a
    chart after it."""
    return not any(a.isdigit() for a in rest)


@recipe("vm_stat", "after", matches=_vm_stat_once,
        help="vm_stat: memory used against total, app / wired / compressed / cached, and swap")
def vm_stat(argv, full=False):
    if not shutil.which("vm_stat"):
        return None
    out = _run(["vm_stat"])
    size = _run(["sysctl", "-n", "hw.memsize"])
    if not out or not size or not size.strip().isdigit():
        return None
    mem = parse_vm_stat(out, int(size.strip()))
    if mem is None:
        return None
    swap = _run(["sysctl", "-n", "vm.swapusage"])
    su = parse_swapusage(swap or "")
    if su:
        mem["swap_used"], mem["swap_total"] = su
    return memory_chart(mem, cols=_cols())


# --------------------------------------------------------------------------
# swap: swapon (Linux) and sysctl vm.swapusage (macOS)


def parse_swapon(text):
    """`swapon --show=NAME,SIZE,USED --bytes --noheadings` into
    [(name, size, used)]."""
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
            rows.append((parts[0], int(parts[1]), int(parts[2])))
    return rows


def swap_chart(rows, cols=None):
    """One meter per swap device, and the total when there are several.
    Pure."""
    if not rows:
        return None
    lw = _label_width([n for n, _, _ in rows], cap=24)
    mw = W if not cols else max(10, min(W, cols - lw - 2 - 2 - 22))
    lines = []
    for name, size, used in rows:
        f = used / size if size else 0
        lines.append(f"{D}{_fit(name, lw):<{lw}}{R}  " + meter(f, width=mw, color=_fill(f))
                     + f"  {_gb(used)} / {_gb(size)}  {D}{round(100 * f)}%{R}")
    if len(rows) > 1:
        size = sum(s for _, s, _ in rows)
        used = sum(u for _, _, u in rows)
        f = used / size if size else 0
        lines.append(f"{D}{'total':<{lw}}{R}  " + meter(f, width=mw, color=_fill(f))
                     + f"  {_gb(used)} / {_gb(size)}  {D}{round(100 * f)}%{R}")
    return "\n".join(lines)


def _swapon_shows(rest):
    return all(a.startswith("-") for a in rest) and not any(
        a in ("-a", "--all", "-d", "--discard", "-p", "--priority") for a in rest)


@recipe("swapon", "after", matches=_swapon_shows, help="swapon: swap in use, per device")
def swapon(argv, full=False):
    if not shutil.which("swapon"):
        return None
    out = _run(["swapon", "--show=NAME,SIZE,USED", "--bytes", "--noheadings"])
    if not out:
        return None
    return swap_chart(parse_swapon(out), cols=_cols())


@recipe("sysctl", "after", matches=lambda argv: "vm.swapusage" in argv and "-w" not in argv,
        when='"$1" == vm.swapusage', help="sysctl vm.swapusage: swap in use")
def sysctl(argv, full=False):
    if not shutil.which("sysctl"):
        return None
    out = _run(["sysctl", "-n", "vm.swapusage"])
    su = parse_swapusage(out or "")
    if not su:
        return None
    return swap_chart([("swap", su[1], su[0])], cols=_cols())


# --------------------------------------------------------------------------
# ls -l: sizes in the listing


def _ls_long(argv):
    """Only the long listing earns a chart: an -l somewhere in the flags."""
    for a in argv:
        if a.startswith("-") and not a.startswith("--") and "l" in a[1:]:
            return True
    return False


def ls_sizes(paths, hidden=False):
    """The regular files a listing shows, as [(name, bytes)]. Directories
    are listed but not sized, as in ls itself."""
    rows = []
    for p in paths or ["."]:
        try:
            if os.path.isdir(p):
                with os.scandir(p) as it:
                    for e in it:
                        if e.name.startswith(".") and not hidden:
                            continue
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISREG(st.st_mode):
                            rows.append((e.name, st.st_size))
            elif os.path.isfile(p):
                rows.append((p, os.stat(p).st_size))
        except OSError:
            continue
    return rows


def ls_chart(rows, cols=None, full=False):
    """The largest files as meters on one scale, then a dist of every
    file's size. Pure."""
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (-r[1], r[0]))
    top = rows if full else rows[:5]
    mx = rows[0][1] or 1
    lw = _label_width([n for n, _ in top], cap=28)
    mw = 20 if not cols else max(8, min(20, cols - lw - 2 - 2 - 12))
    lines = []
    for i, (name, size) in enumerate(top):
        label = "largest" if i == 0 else ""
        lines.append(f"{D}{label:<7}{R} {D}{_fit(name, lw):<{lw}}{R}  "
                     + meter(size / mx, width=mw, color="blue") + f"  {_gb(size)}")
    if len(rows) >= 3:
        sizes = [s for _, s in rows]
        cap = max(1, sorted(sizes)[int(0.9 * (len(sizes) - 1))])
        med = sorted(sizes)[len(sizes) // 2]
        lines.append(f"{D}{'sizes':<7}{R} {'':<{lw}}  "
                     + dist([min(s, cap) for s in sizes], bins=min(20, mw + 4), lo=0, hi=cap,
                            color="violet")
                     + f"  {D}{len(rows)} files, {_gb(sum(sizes))} total, median {_gb(med)}{R}")
    return "\n".join(lines)


@recipe("ls", "after", matches=_ls_long,
        help="ls -l: the largest files as meters and a dist of every size")
def ls(argv, full=False):
    hidden = any(a.startswith("-") and not a.startswith("--") and ("a" in a[1:] or "A" in a[1:])
                 for a in argv)
    paths = [a for a in argv if not a.startswith("-")]
    return ls_chart(ls_sizes(paths, hidden), cols=_cols(), full=full)


# --------------------------------------------------------------------------
# iostat: read and write throughput, live


class IostatChart:
    """Keeps the last 40 samples of total disk throughput and draws them
    as one line under the output. Reads both shapes: macOS (one row per
    interval, three columns per disk with MB/s third) and Linux sysstat
    (one row per device with kB_read/s and kB_wrtn/s, a report ending in
    a blank line)."""

    def __init__(self):
        self.samples = []
        self.disks = []
        self.linux_rows = []
        self.per = []

    def feed(self, line):
        # the relay hands over raw bytes, one line at a time
        if isinstance(line, bytes):
            line = line.decode("utf-8", "replace")
        s = re.sub(r"\x1b\[[0-9;]*m", "", line.rstrip("\r\n"))
        parts = s.split()
        if not parts:
            if self.linux_rows:
                total = sum(r + w for _, r, w in self.linux_rows) / 1024
                self.per = [(d, (r + w) / 1024) for d, r, w in self.linux_rows]
                self.linux_rows = []
                return self._sample(total)
            return None
        if all(not _num(p) for p in parts):
            # a header: macOS names its disks on the line before the
            # column names ("disk0 disk4 cpu load average"), Linux starts a
            # report with "Device ..."
            if "cpu" in parts:
                self.disks = parts[:parts.index("cpu")]
            elif parts[0] == "Device":
                self.linux_rows = []
            return None
        if all(_num(p) for p in parts):
            # macOS: three columns per disk (KB/t, tps, MB/s), then us sy
            # id and the three load averages
            n = len(parts)
            if n >= 9 and (n - 6) % 3 == 0:
                ndisk = (n - 6) // 3
                mbs = [float(parts[3 * i + 2]) for i in range(ndisk)]
                names = self.disks if len(self.disks) == ndisk \
                    else [f"disk{i}" for i in range(ndisk)]
                self.per = list(zip(names, mbs))
                return self._sample(sum(mbs))
            return None
        if len(parts) >= 4 and _num(parts[1]) and _num(parts[2]) and _num(parts[3]):
            # Linux: device tps kB_read/s kB_wrtn/s ...
            self.linux_rows.append((parts[0], float(parts[2]), float(parts[3])))
        return None

    def _sample(self, total):
        self.samples.append(total)
        self.samples = self.samples[-40:]
        return self.line()

    def line(self):
        if not self.samples:
            return None
        cur = self.samples[-1]
        per = "  ".join(f"{d} {v:.2f}" for d, v in self.per[:4])
        return (f"{D}io{R}  " + spark(self.samples, lo=0, color="blue")
                + f"  {cur:.2f} MB/s  {D}{per}{R}")

    def finish(self, text):
        return None


def _num(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


@recipe("iostat", "wrap", help="iostat: a live spark of disk throughput under the output")
def iostat(argv, emit):
    return _wrap(["iostat"] + argv, IostatChart(), emit)


# --------------------------------------------------------------------------
# smartctl -a: wear, temperature, spare, hours

_NVME_TEMP = re.compile(r"^Temperature:\s+(\d+) Celsius", re.M)
_NVME_USED = re.compile(r"^Percentage Used:\s+(\d+)%", re.M)
_NVME_SPARE = re.compile(r"^Available Spare:\s+(\d+)%", re.M)
_NVME_HOURS = re.compile(r"^Power On Hours:\s+([\d,]+)", re.M)
_ATA_ROW = re.compile(r"^\s*(\d+)\s+(\S+)\s+0x[0-9a-fA-F]+\s+(\d+)\s+(\d+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(\S+)", re.M)


def parse_smart(text):
    """SMART health from `smartctl -a`, NVMe or ATA: dict with any of
    wear (percent used, 0..100), temp (Celsius), spare (percent), hours,
    reallocated. Empty dict when nothing recognisable is there."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    out = {}
    m = _NVME_TEMP.search(text)
    if m:
        out["temp"] = int(m.group(1))
    m = _NVME_USED.search(text)
    if m:
        out["wear"] = int(m.group(1))
    m = _NVME_SPARE.search(text)
    if m:
        out["spare"] = int(m.group(1))
    m = _NVME_HOURS.search(text)
    if m:
        out["hours"] = int(m.group(1).replace(",", ""))
    for id_, name, value, worst, thresh, raw in _ATA_ROW.findall(text):
        id_ = int(id_)
        rawn = int(re.match(r"\d+", raw).group(0)) if re.match(r"\d+", raw) else None
        if id_ in (194, 190) and rawn is not None and "temp" not in out:
            out["temp"] = rawn
        elif id_ in (177, 231, 233, 202) and "wear" not in out:
            out["wear"] = max(0, 100 - int(value))
        elif id_ == 9 and rawn is not None:
            out["hours"] = rawn
        elif id_ == 5 and rawn is not None:
            out["reallocated"] = rawn
    return out


def smart_chart(info):
    """Meters for wear, temperature and spare, and the plain figures.
    Pure."""
    if not info:
        return None
    lines = []
    if "wear" in info:
        f = info["wear"] / 100
        lines.append(f"{D}{'wear':<12}{R}  " + meter(f, width=W, color=_fill(f))
                     + f"  {info['wear']}% used")
    if "temp" in info:
        t = info["temp"]
        f = min(1.0, t / 85)
        lines.append(f"{D}{'temperature':<12}{R}  "
                     + meter(f, width=W, color="coral" if t >= 70 else "amber" if t >= 55 else "teal")
                     + f"  {t} °C")
    if "spare" in info:
        f = info["spare"] / 100
        lines.append(f"{D}{'spare':<12}{R}  "
                     + meter(f, width=W, color="coral" if f < 0.1 else "amber" if f < 0.5 else "teal")
                     + f"  {info['spare']}% left")
    tail = []
    if "hours" in info:
        tail.append(f"{info['hours']:,} hours on")
    if info.get("reallocated") is not None:
        tail.append(f"{info['reallocated']} reallocated sectors")
    if tail:
        lines.append(f"{D}{'':<12}  {', '.join(tail)}{R}")
    return "\n".join(lines) if lines else None


class SmartChart:
    def feed(self, line):
        return None

    def finish(self, text):
        return smart_chart(parse_smart(text))


@recipe("smartctl", "wrap", matches=lambda argv: any(a in ("-a", "--all", "-x", "--xall", "-A",
                                                             "-H", "--health") for a in argv),
        when='"$*" == *-a* || "$*" == *-x* || "$*" == *-A* || "$*" == *-H*',
        help="smartctl -a: wear, temperature and spare as meters")
def smartctl(argv, emit):
    return _wrap(["smartctl"] + argv, SmartChart(), emit)
