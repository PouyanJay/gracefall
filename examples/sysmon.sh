#!/usr/bin/env bash
# A real system dashboard built out of gracefall, for macOS.
#
#   examples/sysmon.sh                       print it once
#   gfl view --watch examples/sysmon.sh      live, repainting in place
#
# Every chart here is generated from live data, and every one of them is
# readable in a terminal with no graphics support at all.
set -uo pipefail

G="${GRACEFALL:-gracefall}"
D=$'\033[38;2;110;120;138m'   # dim
F=$'\033[38;2;222;227;236m'   # fg
R=$'\033[0m'

# Fit the terminal. Under `gfl view --watch` this script's stdout is a pipe,
# so it cannot measure the terminal itself; the watch loop exports COLUMNS
# for exactly that reason. tput covers a direct run.
term_cols() {
  if [ -n "${COLUMNS:-}" ] && [ "${COLUMNS:-0}" -gt 20 ] 2>/dev/null; then
    echo "$COLUMNS"; return
  fi
  tput cols 2>/dev/null || stty size </dev/tty 2>/dev/null | cut -d' ' -f2 \
    || echo 80
}
COLS=$(term_cols)
GUTTER=9         # the "disk     " label column
VALUE=18         # room for "267G / 995G" and friends
BAR=$(( COLS - GUTTER - VALUE ))
[ "$BAR" -lt 10 ] && BAR=10
[ "$BAR" -gt 34 ] && BAR=34
WIDE=$(( COLS - GUTTER - 2 ))
[ "$WIDE" -lt 10 ] && WIDE=10
[ "$WIDE" -gt 46 ] && WIDE=46

pct() { awk -v v="$1" 'BEGIN{ if (v=="" || v<0) v=0; if (v>1) v=1; print v }'; }

# Command substitution strips the trailing newline every gracefall command
# writes, which is what lets a chart and its value share one line.
row() {  # row <label> <0..1> <colour> <value text>
  printf '%s%s  %s\n' \
    "${D}$(printf '%-8s' "$1")${R}" \
    "$($G meter "$(pct "$2")" -c "$3" -w "$BAR")" \
    "${F}$4${R}"
}

# --- readings ---------------------------------------------------------
read -r disk_used disk_size disk_pct < <(
  df -H /System/Volumes/Data 2>/dev/null |
  awk 'NR==2{gsub(/%/,"",$5); print $3, $2, $5/100}')

mem_pct=$(vm_stat | awk '
  /Pages free/      {f=$3}
  /Pages active/    {a=$3}
  /Pages wired/     {w=$4}
  /Pages inactive/  {i=$3}
  END { gsub(/\./,"",f); gsub(/\./,"",a); gsub(/\./,"",w); gsub(/\./,"",i);
        t=f+a+w+i; if (t>0) print (a+w)/t; else print 0 }')

batt_raw=$(pmset -g batt 2>/dev/null | grep -o '[0-9]\{1,3\}%' | head -1)
batt_pct=$(awk -v p="${batt_raw%\%}" 'BEGIN{ print (p=="" ? "" : p/100) }')

ncpu=$(sysctl -n hw.ncpu 2>/dev/null || echo 1)
load1=$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | awk '{print $1}')

# --- output -----------------------------------------------------------
echo
printf '%s  %s\n\n' "${F}system${R}" \
  "${D}$(hostname -s) $(date '+%H:%M')${R}"

row disk   "${disk_pct:-0}" amber  "${disk_used:-?} / ${disk_size:-?}"
row memory "$mem_pct"       violet \
  "$(awk -v v="$mem_pct" 'BEGIN{printf "%.0f%% used", v*100}')"
[ -n "$batt_pct" ] && row battery "$batt_pct" teal "$batt_raw"
# Load over core count is the number that means something: 1.0 is every
# core busy, so the bar reads as saturation rather than a raw figure.
row load "$(awk -v l="${load1:-0}" -v n="$ncpu" 'BEGIN{print l/n}')" coral \
  "${load1:-?} of ${ncpu} cores"

cpu=$(ps -A -o %cpu 2>/dev/null | tail -n +2 | sort -rn | head -30)
if [ -n "$cpu" ]; then
  printf '\n%s\n' "${D}cpu% across the busiest 30 processes${R}"
  printf '%*s%s\n' "$GUTTER" "" \
    "$(echo "$cpu" | $G spark -c teal --style area -w "$WIDE")"
fi

# Off by default: a ping run blocks for seconds, which is wrong for a view
# that refreshes. SYSMON_PING=1 turns it on.
if [ "${SYSMON_PING:-0}" = "1" ]; then
  lat=$(ping -c 8 -t 6 1.1.1.1 2>/dev/null |
        grep -o 'time=[0-9.]*' | cut -d= -f2)
  if [ -n "$lat" ]; then
    n=$(echo "$lat" | wc -l | tr -d ' ')
    med=$(echo "$lat" | sort -n | awk -v n="$n" 'NR==int((n+1)/2){print $1}')
    printf '\n%s\n' "${D}round trip to 1.1.1.1, ${n} pings, ms${R}"
    printf '%*s%s  %s\n' "$GUTTER" "" \
      "$(echo "$lat" | $G spark -c blue --style area -w "$WIDE")" \
      "${F}median ${med}ms${R}"
  fi
fi
echo
