"""Ingesta de USDA FoodData Central (bulk CSV) a un staging SQLite local.

Este staging NO es parte del runtime: la app nunca lo lee. Sirve al CLI
`nutriplan-food` para construir el catálogo una vez (`filter` → `curate` →
`validate`), y de ahí en adelante la fuente de verdad es la tabla `foods`. Por
eso vive en SQLite y no en Postgres: 13.694 filas de staging no tienen por qué
viajar a Supabase en cada despliegue.

Del bulk se descartan los ~2.0M de `branded_food` (productos de marca de EE.UU.,
inútiles para un plan latino y el 90% del peso de la descarga).
"""

import csv
import sqlite3
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

# Ids verificados contra nutrient.csv del bulk 2026-04-30.
USDA_NUTRIENTS: dict[int, str] = {
    1008: "kcal_100g",
    1003: "protein_100g",
    1005: "carb_100g",
    1004: "fat_100g",
    1079: "fiber_100g",
    2000: "sugar_100g",
    1093: "sodium_mg_100g",
    # El agua es lo que permite calcular el factor crudo→cocido sin opinar:
    # la materia seca se conserva al cocinar, solo cambia cuánta agua hay.
    1051: "water_100g",
}
# Varios `foundation_food` no traen el 1008 (Energy, KCAL) sino la energía
# derivada por factores de Atwater. Sin este fallback se quedarían sin kcal.
ATWATER_KCAL_IDS = (2047, 2048)

WANTED_TYPES = frozenset({"sr_legacy_food", "foundation_food", "survey_fndds_food"})

# Sufijos de preparación que USDA cuelga de casi toda descripción y que sólo
# añaden ruido al fuzzy matching contra nombres cortos ("chicken breast").
_NOISE = (
    ", raw",
    ", cooked",
    ", boiled",
    ", drained",
    ", without salt",
    ", with salt",
    ", unprepared",
    ", nfs",
    ", ns as to form",
)


@dataclass(frozen=True)
class UsdaFood:
    fdc_id: int
    data_type: str
    description: str
    description_norm: str
    food_category: str | None
    kcal_100g: float | None
    protein_100g: float | None
    carb_100g: float | None
    fat_100g: float | None
    fiber_100g: float
    sugar_100g: float
    sodium_mg_100g: float
    water_100g: float | None = None
    category_desc: str | None = None  # "Vegetables and Vegetable Products"


@dataclass(frozen=True)
class UsdaPortion:
    """Una medida casera de USDA: «1 cup» = 158 g."""

    fdc_id: int
    amount: float
    unit: str  # "cup", "piece", "serving"…
    modifier: str
    gram_weight: float


def normalize_en(text: str) -> str:
    """Normaliza una descripción USDA para el matching.

    No se reusa `domain.food_matching.normalize`: ese singulariza en ESPAÑOL y
    convierte "cheese" → "chees" (len>3, acaba en 's', penúltima es vocal). Aquí
    sólo hace falta minúsculas, sin acentos, sin puntuación y sin los sufijos de
    preparación de USDA.
    """
    text = text.lower()
    for suffix in _NOISE:
        text = text.replace(suffix, " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = "".join(c if c.isalnum() else " " for c in text)
    return " ".join(text.split())


def stream_foods(csv_dir: Path) -> Iterator[UsdaFood]:
    """Recorre el bulk y emite los alimentos útiles con sus macros.

    `food.csv` (209 MB) se filtra a memoria — tras quitar los branded quedan
    ~13.7k filas, unos pocos MB. `food_nutrient.csv` (1.7 GB) se recorre en
    STREAMING descartando cada fila cuyo fdc_id no interese: nunca se carga
    entero en RAM.
    """
    food_csv = csv_dir / "food.csv"
    nutrient_csv = csv_dir / "food_nutrient.csv"
    for path in (food_csv, nutrient_csv):
        if not path.is_file():
            raise FileNotFoundError(f"No encuentro {path}. ¿Es el directorio del bulk CSV?")

    categories = _load_categories(csv_dir)

    meta: dict[int, tuple[str, str, str | None]] = {}
    with food_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if row["data_type"] not in WANTED_TYPES:
                continue
            meta[int(row["fdc_id"])] = (
                row["data_type"],
                row["description"],
                row["food_category_id"] or None,
            )

    nutrients: dict[int, dict[str, float]] = {fdc_id: {} for fdc_id in meta}
    with nutrient_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            fdc_id = int(row["fdc_id"])
            bucket = nutrients.get(fdc_id)
            if bucket is None:
                continue
            nutrient_id = int(row["nutrient_id"])
            amount = row["amount"]
            if not amount:
                continue
            field = USDA_NUTRIENTS.get(nutrient_id)
            if field is not None:
                bucket[field] = float(amount)
            elif nutrient_id in ATWATER_KCAL_IDS:
                bucket.setdefault("_atwater", float(amount))

    for fdc_id, (data_type, description, category) in meta.items():
        n = nutrients[fdc_id]
        kcal = n.get("kcal_100g")
        if kcal is None:
            kcal = n.get("_atwater")
        yield UsdaFood(
            fdc_id=fdc_id,
            data_type=data_type,
            description=description,
            description_norm=normalize_en(description),
            food_category=category,
            kcal_100g=kcal,
            protein_100g=n.get("protein_100g"),
            carb_100g=n.get("carb_100g"),
            fat_100g=n.get("fat_100g"),
            fiber_100g=n.get("fiber_100g", 0.0),
            sugar_100g=n.get("sugar_100g", 0.0),
            sodium_mg_100g=n.get("sodium_mg_100g", 0.0),
            water_100g=n.get("water_100g"),
            category_desc=categories.get(category or ""),
        )


def _load_categories(csv_dir: Path) -> dict[str, str]:
    """id de categoría → nombre legible, para las dos taxonomías de USDA.

    SR Legacy y Foundation usan `food_category.csv`; los de encuesta (FNDDS)
    guardan en el mismo campo un código WWEIA, que vive en otro archivo. Los dos
    son opcionales: sin ellos el importador sigue, solo que sin categoría.
    """
    out: dict[str, str] = {}
    for name, key, value in (
        ("food_category.csv", "id", "description"),
        ("wweia_food_category.csv", "wweia_food_category", "wweia_food_category_description"),
    ):
        path = csv_dir / name
        if not path.is_file():
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                out[row[key]] = row[value]
    return out


def stream_portions(csv_dir: Path, keep: set[int]) -> Iterator[UsdaPortion]:
    """Medidas caseras de los alimentos que interesan. Opcional: si no está el
    archivo, no se emite nada y el catálogo se queda sin `default_unit_g`."""
    portion_csv = csv_dir / "food_portion.csv"
    if not portion_csv.is_file():
        return
    units: dict[str, str] = {}
    unit_csv = csv_dir / "measure_unit.csv"
    if unit_csv.is_file():
        with unit_csv.open(newline="", encoding="utf-8") as fh:
            units = {row["id"]: row["name"] for row in csv.DictReader(fh)}
    with portion_csv.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            # El bulk trae filas con el fdc_id vacío; no son de nadie.
            if not row["fdc_id"] or not row["gram_weight"]:
                continue
            fdc_id = int(row["fdc_id"])
            if fdc_id not in keep:
                continue
            unit = units.get(row["measure_unit_id"], "")
            # 9999 = "undetermined": la unidad de verdad está en el modificador
            # ("serving 1 roll with icing"), no en la tabla de unidades.
            if unit in ("", "undetermined"):
                unit = (row["portion_description"] or row["modifier"] or "").strip()
            yield UsdaPortion(
                fdc_id=fdc_id,
                amount=float(row["amount"] or 1.0),
                unit=unit,
                modifier=(row["modifier"] or "").strip(),
                gram_weight=float(row["gram_weight"]),
            )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS usda_foods (
    fdc_id           INTEGER PRIMARY KEY,
    data_type        TEXT NOT NULL,
    description      TEXT NOT NULL,
    description_norm TEXT NOT NULL,
    food_category    TEXT,
    kcal_100g        REAL,
    protein_100g     REAL,
    carb_100g        REAL,
    fat_100g         REAL,
    fiber_100g       REAL NOT NULL DEFAULT 0,
    sugar_100g       REAL NOT NULL DEFAULT 0,
    sodium_mg_100g   REAL NOT NULL DEFAULT 0,
    water_100g       REAL,
    category_desc    TEXT
);
CREATE INDEX IF NOT EXISTS ix_usda_description_norm ON usda_foods (description_norm);

CREATE TABLE IF NOT EXISTS usda_portions (
    fdc_id      INTEGER NOT NULL,
    amount      REAL NOT NULL,
    unit        TEXT NOT NULL,
    modifier    TEXT NOT NULL,
    gram_weight REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usda_portions_fdc ON usda_portions (fdc_id);
"""

_FOOD_COLUMNS = (
    "fdc_id",
    "data_type",
    "description",
    "description_norm",
    "food_category",
    "kcal_100g",
    "protein_100g",
    "carb_100g",
    "fat_100g",
    "fiber_100g",
    "sugar_100g",
    "sodium_mg_100g",
    "water_100g",
    "category_desc",
)


def build_sqlite(csv_dir: Path, db_path: Path) -> int:
    """(Re)construye el staging. Devuelve cuántos alimentos quedaron."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.unlink(missing_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(_SCHEMA)
        keep: set[int] = set()

        def _rows() -> Iterator[tuple[object, ...]]:
            for f in stream_foods(csv_dir):
                keep.add(f.fdc_id)
                yield tuple(getattr(f, column) for column in _FOOD_COLUMNS)

        placeholders = ",".join("?" * len(_FOOD_COLUMNS))
        conn.executemany(
            f"INSERT INTO usda_foods ({','.join(_FOOD_COLUMNS)}) VALUES ({placeholders})",
            _rows(),
        )
        conn.executemany(
            "INSERT INTO usda_portions VALUES (?,?,?,?,?)",
            (
                (p.fdc_id, p.amount, p.unit, p.modifier, p.gram_weight)
                for p in stream_portions(csv_dir, keep)
            ),
        )
        return int(conn.execute("SELECT count(*) FROM usda_foods").fetchone()[0])


def portions_for(conn: sqlite3.Connection, fdc_id: int) -> list[UsdaPortion]:
    rows = conn.execute(
        "SELECT * FROM usda_portions WHERE fdc_id = ? ORDER BY gram_weight", (fdc_id,)
    ).fetchall()
    return [UsdaPortion(**dict(r)) for r in rows]


def stream_all(conn: sqlite3.Connection) -> Iterator[UsdaFood]:
    """Todo el staging, fila a fila. La curación recorre esto."""
    for row in conn.execute("SELECT * FROM usda_foods ORDER BY fdc_id"):
        yield UsdaFood(**dict(row))


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(
            f"No existe el staging USDA en {db_path}. "
            f"Córrelo primero: nutriplan-food import-usda <dir-del-bulk-csv>"
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get(conn: sqlite3.Connection, fdc_id: int) -> UsdaFood | None:
    row = conn.execute("SELECT * FROM usda_foods WHERE fdc_id = ?", (fdc_id,)).fetchone()
    return UsdaFood(**dict(row)) if row else None


def search(conn: sqlite3.Connection, query: str, *, limit: int = 5) -> list[tuple[UsdaFood, float]]:
    """Candidatos por similitud contra description_norm.

    Puntuar 13.694 descripciones con SequenceMatcher por cada alimento es lento
    (~1 min para el catálogo entero), así que se pre-filtra por LIKE sobre la
    primera palabra del query y sólo se puntúa lo que sobrevive. Si el prefiltro
    no deja nada, se cae al barrido completo antes que devolver vacío.
    """
    norm = normalize_en(query)
    if not norm:
        return []
    head = norm.split()[0]
    rows = conn.execute(
        "SELECT * FROM usda_foods WHERE description_norm LIKE ?", (f"%{head}%",)
    ).fetchall()
    if not rows:
        rows = conn.execute("SELECT * FROM usda_foods").fetchall()

    words = set(norm.split())
    scored: list[tuple[UsdaFood, float]] = []
    for row in rows:
        food = UsdaFood(**dict(row))
        ratio = SequenceMatcher(None, norm, food.description_norm).ratio()
        # El nombre curado suele ser un subconjunto del USDA ("chicken breast" ⊂
        # "chicken, broilers, breast, meat only, cooked, roasted"): sin un premio
        # por contener todas las palabras, el ratio crudo hunde al candidato
        # bueno bajo cualquier descripción corta y parecida. Se remapea a
        # [0.6, 1.0] en vez de aplastar a un 0.85 plano, que empataba a decenas
        # de candidatos y destruía el orden.
        if words and words <= set(food.description_norm.split()):
            score = 0.6 + 0.4 * ratio
        else:
            score = ratio
        scored.append((food, score))
    scored.sort(key=lambda pair: (-pair[1], pair[0].fdc_id))
    return scored[:limit]


def main() -> int:  # pragma: no cover - atajo de depuración
    if len(sys.argv) < 2:
        print("uso: python -m nutriplan.adapters.food.usda_fdc <dir-bulk-csv>")
        return 2
    count = build_sqlite(Path(sys.argv[1]), Path("data/usda/usda.sqlite"))
    print(f"{count} alimentos importados")
    return 0
