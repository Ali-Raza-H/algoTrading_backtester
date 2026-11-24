# Repository Guidelines

## Project Structure & Module Organization

This repository is specification-first: `backtesting-platform-spec.md` is the normative source for scope, execution semantics, acceptance tests, and the roadmap. The Python package has not yet been scaffolded.

Follow the proposed layout: place engine code under `src/backtest/` in focused packages such as `domain/`, `data/`, `clock/`, `market/`, `broker/`, `account/`, and `reporting/`. Keep example plugins in `strategies/`, outside the engine. Organize tests under `tests/unit/`, `tests/integration/`, `tests/fixtures/`, `tests/golden/`, and `tests/performance/`. Treat `data/` and `runs/` as local artifacts; do not commit market data or generated output.

## Build, Test, and Development Commands

No executable project or dependency lock exists yet. After the planned `pyproject.toml` and lockfile are added, the intended commands are:

- `uv sync --locked` — create a reproducible development environment.
- `uv run backtest --help` — verify the CLI installation.
- `uv run pytest` — run the complete test suite.
- `uv run pytest tests/unit` — run fast domain tests while developing.
- `uv run ruff check .` and `uv run mypy src` — run lint and type checks once configured.

Do not claim these commands pass until the project is scaffolded and they have actually been executed.

## Coding Style & Naming Conventions

Target Python 3.12+ and four-space indentation. Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Type public APIs. Prefer immutable domain records, pure rules, dependency injection, exact `Decimal` or scaled-integer money, and timezone-aware UTC timestamps. Keep strategy logic out of core simulation packages. Use Ruff and mypy once configured.
Keep code simple and elegant as well as human readable. With proper indentation. DO NOT OVERCOMPLICATE CODE AND LOGIC. Simplicity is key. The code should be properly documented and easy to tweak and understand by someone new who takes over this project.

## Testing Guidelines

Use pytest, with Hypothesis for invariants and boundaries. Name files `test_<behavior>.py` and tests `test_<expected_outcome>`. Start with synthetic, hand-calculated fixtures; profitability is not a correctness test. Map coverage to specification IDs (for example, `T01` causality or `T08` long P&L). Every bug fix needs a regression fixture. Prioritize causality, deterministic ordering, ledger reconciliation, and reproducibility.

## Commit & Pull Request Guidelines

History currently contains only `First commit`, so no established convention exists. Use concise, imperative subjects such as `Add deterministic event queue`; keep each commit scoped. Pull requests should summarize behavior, cite relevant requirement/test IDs, list commands run with actual results, and disclose unresolved decisions or deviations. Include sample CLI/report output when user-visible behavior changes; never commit secrets, broker credentials, datasets, or generated reports.
