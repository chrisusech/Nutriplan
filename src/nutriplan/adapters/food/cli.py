"""CLI `nutriplan-food`: import-usda → filter → name → validate.

Ningún paso llama a nadie: el nombre en español sale de un léxico (`traductor`),
no de un modelo. USDA usa vocabulario controlado, así que un diccionario lo
cubre en un segundo y da el mismo resultado siempre.

`curate` existe aparte para lo que el léxico no traduce, y es opcional: pide
idioma a una IA y sigue sin tocar un solo número.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from nutriplan.adapters.food import catalog, curation, naming, usda_fdc
from nutriplan.application.prompts import load_prompt
from nutriplan.config.settings import Settings
from nutriplan.container import Container

DEFAULT_DB = Path("data/usda/usda.sqlite")
DEFAULT_CANDIDATES = Path("data/foods/candidatos.jsonl")
DEFAULT_CATALOG = Path("data/foods/catalogo.jsonl")
DEFAULT_QUARANTINE = Path("data/foods/cuarentena.jsonl")
DEFAULT_UNTRANSLATED = Path("data/foods/sin_traducir.jsonl")


def cmd_import(args: argparse.Namespace) -> int:
    count = usda_fdc.build_sqlite(args.csv_dir, args.db)
    print(f"{count} alimentos importados a {args.db}")
    print("(se descartaron los ~2.0M de branded_food: marcas de EE.UU.)")
    return 0


def cmd_filter(args: argparse.Namespace) -> int:
    """Del staging entero a los candidatos que merecen un nombre en español."""
    conn = usda_fdc.connect(args.db)
    foods = list(usda_fdc.stream_all(conn))
    keep = {f.fdc_id for f in foods}
    portions: dict[int, list[usda_fdc.UsdaPortion]] = {}
    for fdc_id in keep:
        found = usda_fdc.portions_for(conn, fdc_id)
        if found:
            portions[fdc_id] = found

    candidates = curation.build_candidates(foods, portions)
    _write_candidates(args.out, candidates)

    by_state = Counter(c.state.value for c in candidates)
    by_role = Counter(c.category.value for c in candidates)
    con_factor = sum(1 for c in candidates if c.yield_factor)
    print(f"{len(foods)} en staging → {len(candidates)} candidatos ({args.out})")
    print(f"  estado: {dict(by_state)}")
    print(f"  rol:    {dict(by_role)}")
    print(f"  con factor de rendimiento crudo→cocido: {con_factor}")
    return 0


def _write_candidates(path: Path, candidates: list[curation.Candidate]) -> None:
    """Los candidatos viajan como CatalogEntry a medio llenar (sin nombre)."""
    catalog.write_jsonl(path, (naming.fallback_entry(c) for c in candidates))


def _read_candidates(path: Path) -> list[curation.Candidate]:
    return [
        curation.Candidate(
            fdc_id=e.fdc_id or 0,
            description=e.name_en or e.name_es,
            category=e.category,
            usda_category="",
            kcal_100g=e.kcal_100g,
            protein_100g=e.protein_100g,
            carb_100g=e.carb_100g,
            fat_100g=e.fat_100g,
            fiber_100g=e.fiber_100g,
            sugar_100g=e.sugar_100g,
            sodium_mg_100g=e.sodium_mg_100g,
            water_100g=None,
            state=e.state,
            cooking_method=e.cooking_method,
            pair_key=e.pair_key,
            yield_factor=e.yield_factor,
            default_unit_g=e.default_unit_g,
            unit_hint=e.unit_name,
            tags=list(e.tags),
        )
        for e in catalog.read_jsonl(path)
    ]


def cmd_name(args: argparse.Namespace) -> int:
    """Nombra el catálogo con el léxico. Sin red, sin IA, sin coste."""
    candidates = _read_candidates(args.candidates)
    entries: list[catalog.CatalogEntry] = []
    sin_traducir: list[catalog.CatalogEntry] = []
    for candidate in candidates:
        entry = naming.entry_traducida(candidate)
        if entry is None:
            sin_traducir.append(naming.fallback_entry(candidate))
        else:
            entries.append(entry)

    written = catalog.write_jsonl(args.out, entries)
    catalog.write_jsonl(args.untranslated, sin_traducir)
    distintos = len({e.name_es for e in entries})
    print(f"{len(candidates)} candidatos → {written} nombrados ({args.out})")
    print(f"  nombres distintos: {distintos} (el resto son variantes del mismo alimento)")
    if sin_traducir:
        cabezas = Counter((e.name_en or "").split(",")[0].strip().lower() for e in sin_traducir)
        faltan = ", ".join(k for k, _ in cabezas.most_common(8))
        print(f"  sin traducir: {len(sin_traducir)} → {args.untranslated}")
        print(f"    cabezas que faltan en el léxico: {faltan}")
    return 0


def cmd_curate(args: argparse.Namespace) -> int:
    candidates = _read_candidates(args.candidates)
    if args.limit:
        candidates = candidates[: args.limit]
    container = Container(Settings())
    llm = container.llm_client
    if llm is None:
        print(
            "No hay proveedor de IA configurado (LLM_API_KEY). El catálogo se "
            "quedaría con nombres en inglés, así que no se genera.",
            file=sys.stderr,
        )
        return 2

    system = load_prompt(
        container.settings.prompts_dir, "food_curation", naming.FOOD_CURATION_VERSION
    ).text
    model = container.settings.llm_model_generate

    def progress(done: int, total: int, kept: int) -> None:
        print(f"  lote {done}/{total} · {kept} nombrados", end="\r", flush=True)

    entries = asyncio.run(
        naming.curate_all(
            candidates,
            llm=llm,
            system=system,
            model=model,
            batch_size=args.batch_size,
            on_progress=progress,
        )
    )
    written = catalog.write_jsonl(args.out, entries)
    nucleo = sum(1 for e in entries if e.engine_default)
    print(f"\n{len(candidates)} candidatos → {written} curados ({args.out})")
    print(f"  núcleo (se listan): {nucleo} · catálogo profundo: {written - nucleo}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """El código audita a la IA. Lo dudoso no entra a la base, se aparta."""
    entries = list(catalog.read_jsonl(args.catalog))
    ok, rejected = catalog.validate_entries(entries)

    # Un nombre repetido no es un fallo: es la misma vaca despiezada de catorce
    # maneras colapsando en el corte que la gente cocina. Se cuenta aparte para
    # que un número enorme ahí no parezca un problema de datos.
    variantes = [par for par in rejected if par[1].startswith("nombre repetido")]
    problemas = [par for par in rejected if not par[1].startswith("nombre repetido")]

    print(f"{len(entries)} revisados · {len(ok)} alimentos distintos")
    print(f"  {len(variantes)} variantes del mismo alimento (colapsadas, no es un fallo)")
    if problemas:
        # El motivo lleva el dato concreto («desvío 28 %»); para el resumen se
        # recorta a la frase, o cada fila sería su propio grupo.
        reasons = Counter(reason.split(":")[0].split(" (")[0] for _, reason in problemas)
        print(f"  {len(problemas)} en cuarentena → {args.quarantine}")
        for reason, n in reasons.most_common():
            print(f"    {n:>5}  {reason}")
    catalog.write_jsonl(args.quarantine, (entry for entry, _ in problemas))
    if args.write_back:
        catalog.write_jsonl(args.catalog, ok)
        print(f"  catálogo reescrito solo con lo apto: {args.catalog}")
    return 1 if rejected and args.strict else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nutriplan-food", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="staging SQLite de USDA")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-usda", help="construye el staging desde el bulk CSV")
    p_import.add_argument("csv_dir", type=Path, help="directorio FoodData_Central_csv_*")
    p_import.set_defaults(func=cmd_import)

    p_filter = sub.add_parser("filter", help="staging → candidatos limpios (sin IA)")
    p_filter.add_argument("--out", type=Path, default=DEFAULT_CANDIDATES)
    p_filter.set_defaults(func=cmd_filter)

    p_name = sub.add_parser("name", help="nombre en español con el léxico (sin IA)")
    p_name.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p_name.add_argument("--out", type=Path, default=DEFAULT_CATALOG)
    p_name.add_argument("--untranslated", type=Path, default=DEFAULT_UNTRANSLATED)
    p_name.set_defaults(func=cmd_name)

    p_curate = sub.add_parser("curate", help="opcional: la IA nombra lo que el léxico no cubre")
    p_curate.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    p_curate.add_argument("--out", type=Path, default=DEFAULT_CATALOG)
    p_curate.add_argument("--limit", type=int, default=0, help="solo los primeros N (pruebas)")
    p_curate.add_argument("--batch-size", type=int, default=naming.BATCH_SIZE, dest="batch_size")
    p_curate.set_defaults(func=cmd_curate)

    p_valid = sub.add_parser("validate", help="aparta lo que no puede entrar a la base")
    p_valid.add_argument("catalog", type=Path, nargs="?", default=DEFAULT_CATALOG)
    p_valid.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE)
    p_valid.add_argument("--write-back", action="store_true", help="deja solo lo apto")
    p_valid.add_argument("--strict", action="store_true", help="sale con error si hay rechazos")
    p_valid.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
