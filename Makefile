.PHONY: test lint check run

PYTHON ?= python3

test:
	$(PYTHON) -m unittest discover -v

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .

check:
	$(PYTHON) -m compileall -q codex_credit_monitor_windows tests
	$(MAKE) PYTHON=$(PYTHON) lint

run:
	$(PYTHON) -m codex_credit_monitor_windows
