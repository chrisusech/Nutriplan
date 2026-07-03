# NutriPlan

Backend generador de planes nutricionales de 30 días (2 ciclos de 15 días, 5 comidas/día)
a partir de un intake en Word, exportable a PDF/DOCX con la marca del entrenador.
Arquitectura hexagonal: el dominio es puro; Streamlit/SQLite/Claude/WeasyPrint son adaptadores.

Especificación completa: `ESPECIFICACION_BACKEND_planes_nutricionales.md.pdf`.

## Requisitos

- Python 3.12+ y [uv](https://docs.astral.sh/uv/)
- macOS: `brew install pango` (requerido por WeasyPrint para el render PDF)

## Arranque rápido

```bash
uv sync                       # instala dependencias
cp .env.example .env          # agrega tu ANTHROPIC_API_KEY
uv run alembic upgrade head   # crea la base SQLite en data/app.db
uv run pytest                 # todos los tests (sin llamadas reales a la IA)
uv run streamlit run src/nutriplan/ui/app.py   # UI Nivel 1
```

## Principios (no negociables)

1. **El código es dueño de los números; la IA es dueña del lenguaje y la selección.**
   Todo cálculo de kcal/macros/gramos es determinista y testeable; la IA nunca produce una cifra.
2. **Núcleo puro, bordes sucios** — `domain/` y `application/` no conocen ningún framework.
3. **Multi-tenant desde la primera línea** — toda fila de datos de cliente lleva `tenant_id`.
4. **Procedencia en cada plan** — `config_version`, `prompt_version`, `model`, `input_hash`.
5. **Humano en el bucle** — todo plan es un borrador que el profesional aprueba antes de exportar.

## Estructura

```
src/nutriplan/
├── domain/        núcleo puro: modelos, cálculo, portion solver, reglas, validación
├── ports/         interfaces (Repository, LLMClient, Renderer, ConfigProvider, ...)
├── application/   casos de uso: ParseIntake, ComputeTargets, GeneratePlan, ...
├── adapters/      db (SQLAlchemy), llm (Anthropic), food (importador), render (PDF/DOCX)
├── ui/            Streamlit (Nivel 1)
└── container.py   composition root — el único lugar que elige adaptadores
```

La estrategia nutricional (factores, deltas, reparto por comida, tolerancias) vive en
`config/nutrition.default.yaml`, versionada — nunca incrustada en Python.
