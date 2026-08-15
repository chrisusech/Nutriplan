# NutriPlan

Generador de menús nutricionales semanales (7 días, hasta 5 comidas/día) que la persona
lee **en la app** — sin PDF ni DOCX. La semana se precalcula con el motor (gramos y
macros) y trae lista de compra; la IA solo mejora recetas al abrir un día.
Arquitectura hexagonal: el dominio es puro; FastAPI, SQLite y el LLM son adaptadores.

Especificación original: `ESPECIFICACION_BACKEND_planes_nutricionales.md.pdf`.

## Requisitos

- Python 3.12+ y [uv](https://docs.astral.sh/uv/)

## Arranque rápido

```bash
uv sync                       # instala dependencias
cp .env.example .env          # agrega tu clave del LLM
uv run alembic upgrade head   # crea la base SQLite en data/app.db
uv run pytest                 # todos los tests con cobertura (sin llamadas reales a la IA)
uv run nutriplan              # UI Nivel 1 en http://127.0.0.1:8000
```

**Modo offline.** Sin `ANTHROPIC_API_KEY` la app arranca igual: la generación usa el
`HeuristicSelector` determinista en vez de Claude. Lo único que queda deshabilitado es la
ingesta del Word (entender un documento libre sí requiere el LLM); el alta de cliente manual
sigue disponible.

## Principios (no negociables)

1. **El código es dueño de los números; la IA es dueña del lenguaje y la selección.**
   Todo cálculo de kcal/macros/gramos es determinista y testeable; la IA nunca produce una cifra.
2. **Núcleo puro, bordes sucios** — `domain/` y `application/` no conocen ningún framework.
3. **Multi-tenant desde la primera línea** — toda fila de datos de cliente lleva `tenant_id`.
4. **Procedencia en cada plan** — `config_version`, `prompt_version`, `model`, `input_hash`.
5. **Humano en el bucle** — todo plan nace como borrador y alguien lo aprueba.

## Estructura

```
src/nutriplan/
├── domain/        núcleo puro: modelos, cálculo, portion solver, reglas, validación
├── ports/         interfaces (Repository, LLMClient, ConfigProvider, ...)
├── application/   casos de uso: ParseIntake, ComputeTargets, GeneratePlan, ...
├── adapters/      db (SQLAlchemy), llm (Anthropic / compatible OpenAI), food (importador)
├── ui/web/        FastAPI + HTMX + Jinja2 — rutas, plantillas y view-models
└── container.py   composition root — el único lugar que elige adaptadores
```

La estrategia nutricional (factores, deltas, reparto por comida, tolerancias) vive en
`config/nutrition.default.yaml`, versionada — nunca incrustada en Python.
