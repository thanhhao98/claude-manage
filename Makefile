.DEFAULT_GOAL := help
.PHONY: help install sync fmt lint typecheck test test-live check clean

help: ## Show targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

install: ## Install `claude-manage` into ~/.local/bin (editable)
	uv tool install --force --editable .

sync: ## Create .venv with dev tools
	uv sync

fmt: ## Format and autofix
	uv run ruff format .
	uv run ruff check --fix .

lint: ## Ruff lint + format check + mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

typecheck: ## mypy only
	uv run mypy

test: ## Hermetic tests (never touch real sessions or iTerm2)
	uv run pytest

test-live: ## Also drive a real, throwaway iTerm2 window (macOS)
	CM_LIVE=1 uv run pytest -m live -v

check: lint test ## Everything CI runs

clean: ## Remove caches and build output
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache build dist src/*.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
