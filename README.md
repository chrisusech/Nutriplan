# NutriPlan

[![CI](https://github.com/chrisusech/Nutriplan/actions/workflows/ci.yml/badge.svg)](https://github.com/chrisusech/Nutriplan/actions/workflows/ci.yml)

Generator of weekly nutrition menus (7 days, up to 5 meals/day) that the person reads **in the
app** — no PDF, no DOCX. The engine precomputes the whole week (grams and macros) and ships a
shopping list; the AI only improves recipes when a day is opened.
Hexagonal architecture: the domain is pure; FastAPI, SQLite and the LLM are adapters.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)

## Quick start

```bash
uv sync                       # install dependencies
cp .env.example .env          # add your LLM key (optional — see offline mode)
uv run alembic upgrade head   # create the SQLite database at data/app.db
uv run pytest                 # full suite with coverage (no real AI calls)
uv run nutriplan              # run the app on http://127.0.0.1:8000
```

## Quality gates

CI runs these on every push and pull request, plus a build of the production image:

```bash
uv run ruff check .   # lint
uv run mypy           # strict type check, currently clean
uv run pytest         # full suite; CI fails under 90% branch coverage
```

**Offline mode.** With no LLM key the app still boots: generation falls back to the
deterministic `HeuristicSelector`. The only thing disabled is Word intake — understanding a free
document does require the LLM — while manual client creation keeps working. The engine talks to
open-weight models over an OpenAI-compatible API; Claude is optional and loads only when
`ANTHROPIC_API_KEY` is present.

## Principles (non-negotiable)

1. **Code owns the numbers; AI owns language and selection.**
   Every kcal/macro/gram calculation is deterministic and testable; the LLM never produces a
   figure. It proposes *which* foods; the code computes *how much*.
2. **Pure core, dirty edges** — `domain/` and `application/` import no framework.
3. **Multi-tenant from line one** — every client-data row carries `tenant_id`.
4. **Provenance on every plan** — `config_version`, `prompt_version`, `model`, `input_hash`.
5. **Human in the loop** — every plan is born a draft and someone approves it.

## Layout

```
src/nutriplan/
├── domain/          pure core: models, calculation, portion solver, rules, validation
├── ports/           interfaces (Repository, LLMClient, ConfigProvider, ...)
├── application/     use cases: ParseIntake, ComputeTargets, GeneratePlan, ...
├── adapters/        db (SQLAlchemy), llm (Anthropic / OpenAI-compatible), food (importer)
├── ui/web/          FastAPI + HTMX + Jinja2 — routes, templates and view-models
├── observability/   structured logging and metrics
├── config/          settings and the nutrition config provider
└── container.py     composition root — the only place that picks adapters
```

The nutrition strategy (factors, deltas, per-meal distribution, tolerances) lives in
`config/nutrition.default.yaml`, versioned — never hardcoded in Python.

## Docs

Mostly Spanish, as are code comments and domain names.

| File | What it covers |
| --- | --- |
| [`PENDIENTE.md`](PENDIENTE.md) | Honest status board: what is ready, what is in progress, what is still manual |
| [`CLAUDE.md`](CLAUDE.md) | Architecture, conventions and code-quality rules in depth (English) |
| [`docs/ops.md`](docs/ops.md) | Production and beta operations: Fly.io, Supabase, secrets, deploy |
| [`docs/beta.md`](docs/beta.md) | Closed-beta mechanics: invites, calendar, exit criteria |
| [`docs/iphone-qa.md`](docs/iphone-qa.md) | Physical-iPhone QA protocol for Sign in with Apple |
| [`docs/store-listing.md`](docs/store-listing.md) | App Store / Play listing kit and privacy labels |
| [`mobile/README.md`](mobile/README.md) | Capacitor wrapper: native capabilities and build |
