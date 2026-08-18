# gracefall developer task runner.
#
# The library itself is zero-dependency pure stdlib and needs none of this.
# These targets exist for the workflows that are multi-step enough to get
# done wrong from memory. `make help` lists them.
#
# Rule: CI runs `make test`, the same target you run locally, so the two
# cannot drift apart.

PY     ?= 3.13
PYS    ?= 3.9 3.10 3.13
PYTHON ?= python3
REF    ?= HEAD
SIZE   ?= 14
APP    ?= ghostty
STREAM ?= examples/inference.gfall

# Prefer uv when it is here: hermetic, and no install step before testing.
# CI has a real interpreter per matrix entry and no uv, and falls through to
# plain pytest against the same code.
UV := $(shell command -v uv 2	/dev/null)
ifeq ($(UV),)
RUN_TEST = $(PYTHON) -m pytest -q
RUN_CLI  = $(PYTHON) -m gracefall
else
RUN_TEST = uv run -q --python $(PY) --with pytest --with pillow --with-editable . pytest -q
RUN_CLI  = uv run -q --with pillow --with-editable . python -m gracefall
endif

.DEFAULT_GOAL := help
.PHONY: help install dev-terminals test test-all verify golden render \
        visual-diff view-sim view view-ghostty ghostty-run png compare demo-gif gitlog-demo \
        smoke build \
        publish clean

help: ## show this list
	@echo "gracefall targets:"
	@grep -hE '^[a-z][a-z-]*:.*## ' $(MAKEFILE_LIST) \
	  | sed 's/:.*## /|/' \
	  | awk -F'|' '{printf "  %-14s %s\n", $$1, $$2}'
	@echo ""
	@echo "vars: PY=$(PY)  PYS='$(PYS)'  REF=$(REF)"

install: ## editable install plus the dev tools
	$(PYTHON) -m pip install -e . pytest pillow

dev-terminals: ## install the terminals Phase 1 targets (macOS, Homebrew)
	@command -v brew 	/dev/null 2	&1 \
	  || { echo "Homebrew not found: https://brew.sh"; exit 1; }
	brew install --cask ghostty kitty
	@echo ""
	@echo "Installed. The acceptance check is visual and cannot be automated:"
	@echo "  open Ghostty, run 'gfl demo | gfl view', confirm all seven spans"
	@echo "  are smooth and aligned, then repeat at a different font size."

test: ## run the test suite (CI runs this exact target)
	$(RUN_TEST)

test-all: ## run the suite on every supported interpreter
	@for v in $(PYS); do \
	  echo "== python $$v =="; \
	  uv run -q --python $$v --with pytest --with pillow --with-editable . \
	    pytest -q \
	    || exit 1; \
	done

verify: test visual-diff ## the gate before committing a refactor

golden: ## regenerate shape goldens, only when geometry moved on purpose
	@echo "Regenerating goldens. Look at the render before you commit these."
	GRACEFALL_UPDATE_GOLDEN=1 uv run -q --with pytest --with-editable . \
	  pytest -q tests/test_shapes.py
	@git --no-pager diff --stat -- tests/golden || true

render: ## write both SVG views of the example stream into build/
	@mkdir -p build
	@$(RUN_CLI) render $(STREAM) -o build/enhanced.svg
	@$(RUN_CLI) render $(STREAM) --plain -o build/plain.svg
	@echo "wrote build/enhanced.svg and build/plain.svg"

visual-diff: ## pixel-diff the rendering vs REF (make visual-diff REF=v0.1.1)
	@uv run -q --with pillow python scripts/visual_diff.py --ref $(REF)

view-sim: ## composite what `gfl view` emits, without needing a terminal
	@uv run -q --with pillow python scripts/kitty_sim.py $(ARGS)

view: ## paint the demo in this terminal (needs Ghostty, kitty, or WezTerm)
	@uv run -q --with pillow --with-editable . python -m gracefall \
	  --force-osc demo | uv run -q --with pillow --with-editable . \
	  python -m gracefall view --stats

view-ghostty: ## open Ghostty and paint the demo there (SIZE=14, APP=ghostty)
	@./scripts/view_in_ghostty.sh $(SIZE) $(APP)

ghostty-run: ## build, sign and launch the OSC 4700 Ghostty fork (GHOSTTY_SRC=../ghostty)
	@uv run -q --with-editable . python scripts/ghostty_run.py

png: ## rasterize the example stream to PNG, both views
	@mkdir -p build
	@$(RUN_CLI) render $(STREAM) --png --cell 16x34 -o build/frame.png
	@$(RUN_CLI) render $(STREAM) --png --plain --cell 16x34 \
	  -o build/frame.plain.png

compare: ## regenerate docs/compare.png from the real pipeline
	@uv run -q --with pillow python scripts/compare_image.py

demo-gif: ## re-record docs/demo.gif (needs vhs, and gracefall on PATH)
	@command -v vhs >/dev/null 2>&1 || { echo "brew install vhs"; exit 1; }
	@vhs docs/demo.tape

gitlog-demo: ## re-record docs/gitlog.gif (needs vhs) and docs/gitlog.png through the reference renderer
	@command -v vhs >/dev/null 2>&1 || { echo "brew install vhs"; exit 1; }
	@vhs docs/gitlog.tape
	@mkdir -p build
	@COLUMNS=112 $(RUN_CLI) --force-osc git log --no-pager -12 > build/gitlog.gfall
	@$(RUN_CLI) render build/gitlog.gfall --png --cell 16x34 -o docs/gitlog.png

smoke: build ## install the built wheel in a clean venv and exercise the CLI
	@./scripts/smoke.sh

build: ## sdist and wheel
	@rm -rf dist
	uv build

publish: ## upload dist/ to PyPI (needs UV_PUBLISH_TOKEN)
	@test -n "$$UV_PUBLISH_TOKEN" \
	  || { echo "set UV_PUBLISH_TOKEN (a project-scoped token)"; exit 1; }
	uv publish

clean: ## remove build artefacts and caches
	rm -rf build dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
