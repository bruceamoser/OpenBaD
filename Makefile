.PHONY: install lint format test check

install:
	pip install -e ".[dev]"

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

check: lint
	ruff format --check src/ tests/

test:
	pytest -m "not integration"

test-all:
	pytest
