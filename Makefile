PYTHON ?= python3

.PHONY: install references test check
install:
	$(PYTHON) -m pip install -e '.[dev]'

# Fetch version-pinned upstream sources; no experiment is executed.
references:
	$(PYTHON) scripts/prepare_references.py

# AgentDojo tests require its optional dependencies: pip install -e '.[agentdojo]'.
test:
	$(PYTHON) -m pytest

check:
	$(PYTHON) -m compileall -q src scripts tests examples
