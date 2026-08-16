# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

NutriPlan generates weekly nutrition menus (7 days, up to 5 meals/day) that the user
reads **in the app** — there is no PDF/DOCX export. It is being pivoted from a trainer
console to an end-user mobile app; the plan for that work lives in
`.claude/plans/`. The README is in Spanish and code comments/domain names are Spanish —
match that language when editing them.

## Commands

```bash
uv sync                         # install deps (Python 3.12+, uv)
cp .env.example .env            # add an LLM key (optional — see offline mode)
uv run alembic upgrade head     # create SQLite DB at data/app.db
uv run nutriplan                # run the FastAPI app on http://127.0.0.1:8000

uv run pytest                   # full suite with coverage (no real AI calls)
uv run pytest tests/unit/test_calculation.py::test_name   # a single test
uv run pytest -k pattern        # by keyword
uv run ruff check .             # lint
uv run ruff format .            # format
uv run mypy                     # strict type check (packages = ["nutriplan"])

uv run nutriplan-food import-usda <dir>   # rebuild the catalog from USDA bulk CSV.
uv run nutriplan-food filter              #   No step calls anything: Spanish names
uv run nutriplan-food name                #   come from a lexicon (adapters/food/
uv run nutriplan-food validate            #   traductor.py). See data/foods/README.md
uv run alembic revision --autogenerate -m "msg"   # new migration
uv run python scripts/fetch_fonts.py      # re-subset the self-hosted fonts
```

## Core principles (non-negotiable, from README)

1. **Code owns the numbers; AI owns language and selection.** Every kcal/macro/gram
   calculation is deterministic and testable — the LLM never produces a figure. It
   proposes *which* foods (constrained by an enum schema of allowed ids); the code
   computes *how much* (portion solver), validates against tolerances, and retries.
2. **Pure core, dirty edges.** `domain/` and `application/` import no framework.
3. **Multi-tenant from line one.** Every client-data row carries `tenant_id`.
4. **Provenance on every plan.** `config_version`, `prompt_version`, `model`, `input_hash`.
5. **Human in the loop.** Every plan is a draft the professional approves before export.

## Architecture (hexagonal)

Dependencies point inward: `domain` ← `application` ← `adapters`/`ui`. Wiring lives
only in the composition root.

- `src/nutriplan/domain/` — pure core: `models.py`, `calculation.py`, portion solver
  (`portioning.py`), meal engine (`meal_template.py`, `meal_affinity.py`,
  `macro_split.py`), `generation_rules.py`, `validation.py`, `food_matching.py`,
  `nutrition_config.py`. No I/O, no frameworks.
- `src/nutriplan/ports/` — interfaces (`LLMClient`, `ConfigProvider`, repositories,
  `FoodRepository`, `JobRepository`).
- `src/nutriplan/application/` — use cases, one file each: `parse_intake` →
  `compute_targets` → `generate_plan` → `approve_plan`, plus `edit_plan`, `recipes`,
  `auth`, `password_reset`, `jobs`, `import_form_csv`.
- `src/nutriplan/adapters/` — concrete implementations: `db/` (SQLAlchemy async
  models, repositories, seed, migrate), `llm/` (Anthropic + OpenAI-compatible +
  `heuristic` + `template` + `mock`), `food/` (USDA importer + CLI), `meals/`,
  `intake/` (docx reader).
- `src/nutriplan/ui/web/` — FastAPI + HTMX + Jinja2 (the only UI):
  `routes/`, `templates/`, `presenter.py` (view-models), `format.py` (portion and
  colour formatting), `deps.py`. Fonts are self-hosted in `static/fonts/` — never
  add a CDN `<link>`, it breaks CSP and offline use in Capacitor.
- `src/nutriplan/container.py` — **composition root**: the single place that chooses
  concrete adapters per `ENV`. Nothing else knows what's behind a port. Repositories
  are per-session (each request/job opens one via `session_factory` and builds its
  `Repos` bundle with `repos(session)`, tenant pre-filtered); everything else is
  cached. When adding a dependency, wire it here, not in the use cases.

## Key conventions

- **The menu is one week. Always.** `DAYS_PER_WEEK = 7` in `domain/models.py` is the
  product, not a setting: there is no `duration_days`, no `PlanPhase`, no 15/30 choice.
  Removing that axis is what let the plan tree collapse to `PlanCycle → 7 DayPlan`.

- **Nutrition strategy is data, never code.** Factors, deltas, per-meal distribution,
  and tolerances live in `config/nutrition.default.yaml` (versioned), read through the
  `ConfigProvider` port. `container.nutrition_config(client)` trims the strategy to the
  client's actual meal slots (`.for_slots(...)`) — the one place that happens, so the
  whole plan comes out with the client's meals without any other component knowing.
- **The meal engine is data too:** `data/meals/food_classes.yaml` and
  `data/meals/meal_templates.yaml`. A malformed YAML fails fast at startup in the
  container, and `validate_catalog` runs at boot so a selector pointing at a food name
  that no longer exists fails loudly instead of silently dropping the template.
- **The food catalog lives in the DB, not in a file.** The `foods` table is the source
  of truth; it is edited at `/admin/alimentos` and startup never touches it. The JSONL
  files under `data/foods/` are build artifacts produced by `nutriplan-food` and loaded
  once by a data migration — never hand-edited, never read at runtime. Two flags split
  it: `engine_default` marks the few hundred foods that are *listed* (profile chips,
  "mis alimentos", the LLM shortlist), while the rest stay *alive but unlisted* and are
  reached only by `search_deep` when someone names them. Never build a `Literal` schema
  from the full catalog — see `application/swap_pool.py` for the retrieve-then-constrain
  pattern. Foods carry `state` (crudo/cocido) and `yield_factor`, derived from USDA water
  content, so the shopping list can talk in raw grams (see `domain/cocina.py`).
- **Offline mode:** with no `ANTHROPIC_API_KEY`, `container.llm_client` is `None` and
  generation uses the deterministic `HeuristicSelector`. Only Word intake (which needs
  the LLM to understand a free document) is disabled; manual client creation still works.
  Tests never make real AI calls — use the heuristic/mock LLM fixtures.
- **Database:** SQLite locally (`data/app.db`); Postgres/Supabase in prod. Prod uses the
  Supabase transaction pooler (6543), which forces asyncpg prepared-statement caching
  off in `create_engine`. Alembic migrations need a session connection — run them via
  the direct connection (5432), not the pooler. `RUN_MIGRATIONS_ON_START` (default true)
  runs `alembic upgrade head` at boot; set false once migrated to speed startup.
- **Type strictness:** mypy runs in strict mode and is currently clean — keep it that
  way. `mammoth` is untyped and used behind a typed boundary; keep the `Any` from
  leaking into the domain.

## Tests

`tests/unit/` (pure domain + presenter), `tests/property/` (Hypothesis over
calculations), `tests/integration/` (e2e flow, migrations, repositories,
parse-intake, web UI). Async tests run under `asyncio_mode = "auto"`. Intake docx
fixtures are generated by `tests/fixtures/make_intake_docs.py`.

**Name tests as user stories**, in Spanish, describing actor + action + outcome —
`test_un_huevo_de_codorniz_no_se_confunde_con_un_huevo_entero`, not `test_portion_ok`.
A test should read like something a person does, so a failure names the broken promise.

Coverage runs on every `pytest` (`--cov=nutriplan --cov-branch`). The target is ≥90%;
raise the bar as the pivot lands rather than lowering it.

## Code quality

- **No god files.** Soft ceiling of ~300 lines per module, ~120 per function. One file =
  one concept: whoever orchestrates does not calculate, whoever calculates does no I/O.
  Check with `find src -name '*.py' -exec wc -l {} + | sort -rn | head`.
- **Comments explain the *why*, never the *what*.** The code says what it does. Keep a
  comment when it records a non-obvious decision (why the Supabase pooler disables the
  prepared-statement cache, why weights are cooked and not raw); delete the ones that
  narrate the next line. Docstrings on modules and ports.
- **Typed errors, not `except Exception`.** Extend the hierarchy in `domain/errors.py`
  instead of returning `None` or a string.
- **Let the type enforce the rule** — e.g. `Literal[allowed_ids]` in the LLM schemas, so
  the AI cannot even name a forbidden food.
