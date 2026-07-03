# Base de alimentos curada

`curated_foods.csv` contiene ~65 alimentos con macros por 100 g tomados de valores
estándar de USDA FoodData Central (https://fdc.nal.usda.gov/).

- `tags` separa con `;` las etiquetas que habilitan filtros de restricción:
  `mariscos`, `pescado`, `gluten`, `lacteo`, `cerdo`, `res`, `huevo`,
  `frutos_secos`, `soya`, `batido`, `vegano`.
- `default_unit_g` = gramos de una porción natural (1 huevo ≈ 50 g, 1 banano ≈ 120 g).
- `source_ref` está reservado para el `fdc_id` exacto de USDA: al curar/auditar la base,
  buscar cada alimento en FDC y registrar su id aquí. El importador lo acepta vacío.

El importador (`nutriplan.adapters.food.usda_importer`) genera UUIDs deterministas
(uuid5 sobre `name_es`), así la carga es idempotente.
