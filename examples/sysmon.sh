#!/usr/bin/env bash
# A real system dashboard built out of gracefall, for macOS.
#
#   examples/sysmon.sh                       print it once
#   gfl view --watch examples/sysmon.sh      live, in a graphics terminal
#
# Every chart here is generated from live data, and every one of them is
# readable in a terminal with no graphics support at all.
set -uo pipefail

G="${GRACEFALL:-gracefall}"
D=$'\033[38;2;110;120;138m'   # dim
F=$'\033[38;2;222;227;236m'   # fg
R=$'\033[0m'

pct() { awk -v v="$1" 'BEGIN{ if (v=="" || v<0) v=0; if (v>1) v=1; print v }'; }

# --- disk -------------------------------------------------------------
read -r disk_used disk_size disk_pct < <(
  df -H /System/Volumes/Data 2>/dev/null |
  awk 'NR==2{gsub(/%/,"",$5); print $3, $2, $5/100}')

# --- memory -----------------------------------------------------------
mem_pct=$(vm_stat | awk '
  /Pages free/      {f=$3}
  /Pages active/    {a=$3}
  /Pages wired/     {w=$4}
  /Pages inactive/  {i=$3}
  END { gsub(/\./,"",f); gsub(/\./,"",a); gsub(/\./,"",w); gsub(/\./,"",i);
        t=f+a+w+i; if (t>0) print (a+w)/t; else print 0 }')

# --- battery ----------------------------------------------------------
batt_raw=$(pmset -g batt 2>/dev/null | grep -o '[0-9]\{1,3\}%' | head -1)
batt_pct=$(awk -v p="${batt_raw%\%}" 'BEGIN{ print (p=="" ? "" : p/100) }')

# --- load, one point per core ----------------------------------------
load=$(sysctl -n vm.loadavg 2>/dev/null | tr -d '{}' | awk '{print $1, $2, $3}')

echo
printf '%s\n' "${F}system${R}  ${D}$(hostname -s) $(date '+%H:%M')${R}"
echo

printf '%s' "${D}disk     ${R}"
$G meter "$(pct "${disk_pct:-0}")" -c amber -w 28
printf '  %s\n' "${F}${disk_used:-?} / ${disk_size:-?}${R}"

printf '%s' "${D}memory   ${R}"
$G meter "$(pct "$mem_pct")" -c violet -w 28
printf '  %s\n' "${F}$(awk -v v="$mem_pct" 'BEGIN{printf "%.0f%%", v*100}') used${R}"

if [ -n "$batt_pct" ]; then
  printf '%s' "${D}battery  ${R}"
  $G meter "$(pct "$batt_pct")" -c teal -w 28
  printf '  %s\n' "${F}${batt_raw}${R}"
fi

# Load relative to core count is the number that means something: 1.0 is
# "every core busy", so the meter reads as saturation rather than a raw
# figure you have to divide in your head.
ncpu=$(sysctl -n hw.ncpu 2>/dev/null || echo 1)
load1=$(echo "$load" | awk '{print $1}')
printf '%s' "${D}load     ${R}"
$G meter "$(pct "$(awk -v l="$load1" -v n="$ncpu" 'BEGIN{print l/n}')")" \
  -c coral -w 28
printf '  %s\n' "${F}${load1} of ${ncpu} cores${R}"

# --- process CPU, the busiest 30 -------------------------------------
cpu=$(ps -A -o %cpu 2>/dev/null | tail -n +2 | sort -rn | head -30)
if [ -n "$cpu" ]; then
  echo
  printf '%s\n' "${D}cpu% across the busiest 30 processes${R}"
  printf '         '
  echo "$cpu" | $G spark -c teal --style area -w 30
  echo
fi

# --- network round trip ----------------------------------------------
# Off by default: a ping run blocks for seconds, which is wrong for a view
# that refreshes. SYSMON_PING=1 turns it on.
lat=""
if [ "${SYSMON_PING:-0}" = "1" ]; then
  lat=$(ping -c 8 -t 6 1.1.1.1 2>/dev/null |
        grep -o 'time=[0-9.]*' | cut -d= -f2)
fi
if [ -n "$lat" ]; then
  n=$(echo "$lat" | wc -l | tr -d ' ')
  med=$(echo "$lat" | sort -n | awk -v n="$n" 'NR==int((n+1)/2){print $1}')
  printf '%s\n' "${D}round trip to 1.1.1.1, ${n} pings, ms${R}"
  printf '         '
  echo "$lat" | $G spark -c blue --style area -w 24
  printf '  %s\n' "${F}median ${med}ms${R}"
  echo
fi
