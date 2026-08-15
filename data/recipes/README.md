# Catálogo curado de recetas (esbozo)

Aquí vivirán platos con **nombre culinario**, pasos y vínculo a alimentos del
catálogo (USDA/`curated_foods`). El motor de macros sigue siendo el código:
esta carpeta no inventa gramos; define *cómo* cocinar combos que ya cuadran.

## Objetivos

1. Menos dependencia de la IA para platos repetidos (caché + curaduría).
2. Recetas con sabor real («pollo al ajillo», no «proteína con carbo»).
3. Feedback por plato (`dish_ratings.comment`) para mejorar este catálogo.

## Archivos

| Archivo | Rol |
|---------|-----|
| `catalog.yaml` | Recetas curadas por `id` estable |
| (futuro) seed a `dish_recipes` | Prefill en arranque / CLI |

## Forma de una entrada

```yaml
- id: pollo_ajillo_arroz
  name_es: Pechuga al ajillo con arroz
  # Clases o alimentos del plan (mismo idioma que meal_templates)
  foods: [pechuga_pollo, arroz_blanco]
  steps:
    - Dora el ajo en un chorrito de aceite.
    - Cocina la pechuga a fuego medio hasta dorar.
    - Sirve con la porción de arroz.
  tips: Un toque de limón al final.
  difficulty: fácil
  prep_minutes: 20
```

La caché SQLite `dish_recipes` (por `dish_key`) sigue siendo la capa caliente;
este YAML es la fuente humana de verdad. Al resolver una receta el orden es:

1. caché SQLite
2. `catalog.yaml` (match por tokens de alimento)
3. IA (bajo demanda al abrir el plato)
4. pasos genéricos de `meal_templates.yaml`
