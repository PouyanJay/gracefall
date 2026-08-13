#!/usr/bin/env bash
# Install the built wheel into a throwaway venv and exercise the shipped CLI.
#
# This is the check that catches what unit tests cannot: broken entry points,
# a bad requires-python floor, metadata that only looks right in the repo,
# and the isatty policy as the installed artifact actually applies it.
#
#   scripts/smoke.sh [wheel]        default: the newest wheel in dist/
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-3.9}"   # the floor in requires-python, so smoke-test there
if [ -n "${1:-}" ]; then
  WHEEL="$1"
else
  # `make build` wipes dist/ first, so this is normally a single entry.
  shopt -s nullglob
  WHEELS=("$ROOT"/dist/*.whl)
  WHEEL="${WHEELS[${#WHEELS[@]}-1]:-}"
fi

if [ -z "$WHEEL" ] || [ ! -f "$WHEEL" ]; then
  echo "no wheel found. Run: make build" >&2
  exit 1
fi

VENV="$(mktemp -d)/venv"
trap 'rm -rf "$(dirname "$VENV")"' EXIT

echo "wheel:  $(basename "$WHEEL")"
echo "python: $PY"
uv venv -q --python "$PY" "$VENV"
VIRTUAL_ENV="$VENV" uv pip install -q "$WHEEL"

G="$VENV/bin/gracefall"
fail() { echo "  FAIL  $1" >&2; exit 1; }
ok()   { echo "  ok    $1"; }

[ -x "$G" ] || fail "no gracefall entry point"
[ -x "$VENV/bin/gfl" ] || fail "no gfl entry point"
ok "entry points: gracefall, gfl"

"$VENV/bin/python" -m gracefall --version >/dev/null || fail "python -m gracefall"
ok "python -m gracefall"

"$G" --help >/dev/null 2>&1 || fail "gracefall --help"
for c in spark meter dist flow scatter heat demo strip render view; do
  "$G" "$c" --help >/dev/null 2>&1 || fail "$c --help"
done
ok "--help for every parser"

# The view extra is not installed here, so the shim must say what is
# missing rather than traceback, and must still pass text through when
# the terminal has no graphics support.
"$G" view "$ROOT/examples/inference.gfall" >/dev/null 2>&1 \
  || fail "view failed on a non-graphics terminal"
ok "view degrades without a graphics terminal"

seq 1 20   | "$G" spark   >/dev/null || fail "spark"
seq 1 200  | "$G" dist    >/dev/null || fail "dist"
printf '1 2\n2 5\n3 7\n4 11\n' | "$G" scatter >/dev/null || fail "scatter"
printf '1 2 3\n4 5 6\n'        | "$G" heat    >/dev/null || fail "heat"
"$G" meter 62% -c amber >/dev/null || fail "meter"
"$G" flow a:done b:active c:pending >/dev/null || fail "flow"
ok "every span type renders"

# The isatty policy: piped output must be pure fallback, both flag orders.
[ "$("$G" spark 1 2 3 | grep -c $'\x1b]4700' || true)" = "0" ] \
  || fail "piped output leaked an envelope"
[ "$("$G" --force-osc spark 1 2 3 | grep -c $'\x1b]4700' || true)" = "1" ] \
  || fail "--force-osc before subcommand emitted nothing"
[ "$("$G" spark 1 2 3 --force-osc | grep -c $'\x1b]4700' || true)" = "1" ] \
  || fail "--force-osc after subcommand emitted nothing"
[ "$("$G" demo --force-osc | grep -c $'\x1b]4700' || true)" -gt "1" ] \
  || fail "the README's demo line emitted nothing"
ok "isatty policy and both flag orders"

TMP="$(dirname "$VENV")/round"
"$G" --force-osc demo > "$TMP.gfall"
"$G" render "$TMP.gfall" -o "$TMP.svg" >/dev/null 2>&1
"$G" render "$TMP.gfall" --plain -o "$TMP.plain.svg" >/dev/null 2>&1
[ -s "$TMP.svg" ] && [ -s "$TMP.plain.svg" ] || fail "render produced nothing"
"$G" strip "$TMP.gfall" | grep -qv $'\x1b]4700' || fail "strip left envelopes"
ok "emit, render, strip roundtrip"

"$VENV/bin/python" - <<'PY' || exit 1
from importlib.metadata import metadata
m = metadata("gracefall")
deps = m.get_all("Requires-Dist") or []
runtime = [d for d in deps if "extra ==" not in d]
assert not runtime, f"core package grew a runtime dependency: {runtime}"
urls = dict(u.split(", ", 1) for u in (m.get_all("Project-URL") or []))
bad = [k for k, v in urls.items() if "CHANGEME" in v or "<user>" in v]
assert not bad, f"placeholder URLs in metadata: {bad}"
print("  ok    zero runtime dependencies, metadata clean")
PY

echo "smoke passed"
