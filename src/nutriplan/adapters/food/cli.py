"""CLI de datos de alimentos: `nutriplan-food`.

Cuatro comandos, en el orden en que se usan:

    nutriplan-food import-usda ~/Downloads/FoodData_Central_csv_2026-04-30
    nutriplan-food link                 # ancla cada alimento curado a su fdc_id
    nutriplan-food audit                # compara los macros del CSV contra USDA
    nutriplan-food scaffold 171287 --name-es "huevo entero"

`link` pide confirmación humana a propósito. "chicken breast" devuelve una
decena de entradas en USDA (cruda, asada, frita, con piel, precocinada) con
macros muy distintos; elegir la primera automáticamente metería un fdc_id
equivocado y `audit` reportaría deltas falsos para siempre.
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

from nutriplan.adapters.food import usda_fdc

DEFAULT_DB = Path("data/usda/usda.sqlite")
DEFAULT_CSV = Path("data/foods/curated_foods.csv")

# Las columnas que el humano decide; `scaffold` las deja vacías para que las
# rellene a mano. El resto sale de USDA.
_JUDGEMENT_COLUMNS = (
    "category", "tags", "default_unit_g", "unit_granularity", "unit_name",
    "portion_step_g", "portion_min_g", "portion_max_g", "meal_slots",
)
_MACRO_FIELDS = ("kcal_100g", "protein_100g", "carb_100g", "fat_100g", "fiber_100g")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames or []), list(reader)


def _write_csv(path: Path, header: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def cmd_import(args: argparse.Namespace) -> int:
    count = usda_fdc.build_sqlite(args.csv_dir, args.db)
    print(f"{count} alimentos importados a {args.db}")
    print("(se descartaron los ~2.0M de branded_food: marcas de EE.UU.)")
    return 0


def _print_candidates(candidates: list[tuple[usda_fdc.UsdaFood, float]]) -> None:
    for i, (food, score) in enumerate(candidates, start=1):
        macros = (
            f"{food.kcal_100g or 0:.0f} kcal · P {food.protein_100g or 0:.1f}"
            f" · C {food.carb_100g or 0:.1f} · G {food.fat_100g or 0:.1f}"
        )
        print(f"  {i}) [{score:.2f}] {food.fdc_id:>8}  {food.description[:66]}")
        print(f"                    {macros}")


def cmd_link(args: argparse.Namespace) -> int:
    header, rows = _read_csv(args.csv)
    conn = usda_fdc.connect(args.db)
    pending = [r for r in rows if not (r.get("source_ref") or "").strip()]
    if not pending:
        print("Todos los alimentos ya tienen fdc_id. Nada que enlazar.")
        return 0

    print(f"{len(pending)} alimentos sin fdc_id.\n")
    linked = 0
    for row in pending:
        query = (row.get("name_en") or row["name_es"]).strip()
        candidates = usda_fdc.search(conn, query, limit=args.limit)
        if not candidates:
            print(f"× {row['name_es']}: sin candidatos\n")
            continue

        cur = " · ".join(f"{f}={row.get(f) or '-'}" for f in _MACRO_FIELDS)
        print(f"▸ {row['name_es']}  ({query})")
        print(f"  CSV actual: {cur}")
        _print_candidates(candidates)

        choice = input("  elige [1-N] · s=saltar · <fdc_id> · q=salir: ").strip().lower()
        print()
        if choice == "q":
            break
        if choice in ("", "s"):
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            fdc_id = candidates[int(choice) - 1][0].fdc_id
        elif choice.isdigit():
            fdc_id = int(choice)
            if usda_fdc.get(conn, fdc_id) is None:
                print(f"  ! {fdc_id} no está en el staging; salto\n")
                continue
        else:
            continue
        row["source_ref"] = str(fdc_id)
        linked += 1

    _write_csv(args.csv, header, rows)
    print(f"{linked} alimentos enlazados. Escrito {args.csv}")
    print("Ahora: nutriplan-food audit")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    _header, rows = _read_csv(args.csv)
    conn = usda_fdc.connect(args.db)

    unlinked = [r["name_es"] for r in rows if not (r.get("source_ref") or "").strip()]
    deltas: list[tuple[str, str, float, float]] = []

    for row in rows:
        ref = (row.get("source_ref") or "").strip()
        if not ref:
            continue
        food = usda_fdc.get(conn, int(ref))
        if food is None:
            print(f"! {row['name_es']}: fdc_id {ref} no existe en el staging")
            continue
        for field in _MACRO_FIELDS:
            ours = float(row.get(field) or 0.0)
            theirs = getattr(food, field)
            if theirs is None:
                continue
            # Comparación relativa, con un piso absoluto: un macro que vale 0.4
            # frente a 0.2 es un 100% de desvío y no le importa a nadie.
            scale = max(abs(theirs), 1.0)
            if abs(ours - theirs) / scale > args.tolerance:
                deltas.append((row["name_es"], field, ours, theirs))

    if unlinked:
        print(f"{len(unlinked)} alimentos SIN fdc_id (no auditables): "
              f"{', '.join(unlinked[:8])}{' …' if len(unlinked) > 8 else ''}\n")

    if not deltas:
        print(f"Sin desviaciones por encima del {args.tolerance:.0%}.")
        return 0

    print(f"{len(deltas)} desviaciones por encima del {args.tolerance:.0%}:\n")
    print(f"  {'alimento':<28} {'macro':<14} {'CSV':>9} {'USDA':>9}   desvío")
    for name, field, ours, theirs in deltas:
        pct = (ours - theirs) / max(abs(theirs), 1.0)
        print(f"  {name:<28} {field:<14} {ours:>9.1f} {theirs:>9.1f}   {pct:+.0%}")
    return 1


def cmd_scaffold(args: argparse.Namespace) -> int:
    header, _rows = _read_csv(args.csv)
    conn = usda_fdc.connect(args.db)
    food = usda_fdc.get(conn, args.fdc_id)
    if food is None:
        print(f"fdc_id {args.fdc_id} no está en el staging", file=sys.stderr)
        return 1

    row = dict.fromkeys(header, "")
    row["name_es"] = args.name_es
    row["name_en"] = food.description
    row["source"] = "USDA"
    row["source_ref"] = str(food.fdc_id)
    for field in _MACRO_FIELDS:
        value = getattr(food, field)
        row[field] = "" if value is None else f"{value:.10g}"

    print(f"# {food.description}  (fdc_id {food.fdc_id}, {food.data_type})")
    print(f"# rellena a mano: {', '.join(_JUDGEMENT_COLUMNS)}")
    writer = csv.DictWriter(sys.stdout, fieldnames=header)
    writer.writerow(row)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nutriplan-food", description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="staging SQLite de USDA")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="catálogo curado")
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import-usda", help="construye el staging desde el bulk CSV")
    p_import.add_argument("csv_dir", type=Path, help="directorio FoodData_Central_csv_*")
    p_import.set_defaults(func=cmd_import)

    p_link = sub.add_parser("link", help="ancla cada alimento curado a su fdc_id")
    p_link.add_argument("--limit", type=int, default=5, help="candidatos a proponer")
    p_link.set_defaults(func=cmd_link)

    p_audit = sub.add_parser("audit", help="compara los macros del CSV contra USDA")
    p_audit.add_argument("--tolerance", type=float, default=0.05)
    p_audit.set_defaults(func=cmd_audit)

    p_scaf = sub.add_parser("scaffold", help="emite una fila CSV con los macros de USDA")
    p_scaf.add_argument("fdc_id", type=int)
    p_scaf.add_argument("--name-es", required=True, dest="name_es")
    p_scaf.set_defaults(func=cmd_scaffold)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
