# La base de alimentos

**La fuente de verdad es la tabla `foods` de la base de datos.** Se corrige desde
`/admin/alimentos` y lo que se guarde ahí se queda: el arranque no la toca.

Esto antes no era así. El catálogo vivía en `curated_foods.csv` y `seed_local` lo
volcaba en cada arranque y en todos los entornos, así que corregir un macro en la
base no servía de nada —al reiniciar volvía el valor del archivo— y
`sync_catalog_active` apagaba cualquier alimento que el CSV no listara. La tabla
existía, pero era de solo lectura en la práctica.

## Los archivos que quedan (y por qué no son la fuente de verdad)

Son **artefactos de build**: se generan con `nutriplan-food`, se cargan **una vez**
con la migración `b5c6d7e8f9a0`, y nadie los vuelve a leer en runtime ni los edita
a mano. Existen porque las filas tienen que llegar a Supabase de alguna forma.

| Archivo | Qué es |
|---|---|
| `catalogo_base.jsonl` | Los ~163 alimentos curados a mano. Mandan sobre los nombres (las plantillas de platos referencian varios por su `name_es` exacto) y conservan sus ids originales, así que no se rompe ninguna preferencia, despensa ni plato ya generado. |
| `catalogo.jsonl` | El volcado curado desde USDA. Cede ante cualquier choque de nombre o de ancla con el base. |
| `candidatos.jsonl` | Paso intermedio: lo que sobrevivió al filtro, aún sin nombre en español. |
| `sin_traducir.jsonl` | Filas cuya cabeza no está en el léxico. Quedan fuera a propósito: un catálogo con «Butterbur, (fuki), raw» dentro es peor que uno más corto. |
| `cuarentena.jsonl` | Lo que el control de calidad rechazó. **No entra a la base**: se mira a mano. |

## Los dos niveles del catálogo

No es lo mismo estar **vivo** que **listarse**:

- `engine_default = true` → el **núcleo**, unos cientos. Es lo que se ve en los
  chips del perfil, en «Mis alimentos» y en el pool por defecto, y lo que se le
  enseña a la IA.
- `engine_default = false` → el **catálogo profundo**, cientos. No se lista nunca.
  Se alcanza escribiendo su nombre: `search_deep` lo encuentra, y de ahí llega a
  un plato (ver `application/swap_pool.py`).
- `catalog_active = false` → retirado. Ni se lista ni se encuentra, pero la fila
  se queda: un plato generado hace tres semanas sigue necesitando su nombre.

## Crudo y cocido

`state` (`crudo` / `cocido` / `no_aplica`) dice en qué estado están los macros de
la fila. Antes eso vivía en el sufijo del nombre, y por eso la lista de compras
pedía gramos cocidos de arroz — que no es lo que se compra.

`yield_factor` son los gramos cocidos que salen de un gramo crudo. **Lo calcula el
código**, no una tabla copiada: al cocinar solo entra o sale agua, así que la
materia seca se conserva y

```
yield_factor = (100 − agua_crudo) / (100 − agua_cocido)
```

con el agua que publica USDA (nutriente 1051). De ahí salen el arroz a 2,8×, las
lentejas a 3,0× y la carne de res a 0,75×. Cuando USDA no publica el crudo
equivalente el factor queda vacío y **no se convierte nada**: preferimos una lista
de compra que se queda corta en un alimento a una que multiplica por un número
inventado.

## Reconstruir el catálogo desde USDA

Se descarga el bulk CSV de [FoodData Central](https://fdc.nal.usda.gov/download-datasets.html)
y se corre:

```bash
uv run nutriplan-food import-usda ~/Downloads/FoodData_Central_csv_2026-04-30
uv run nutriplan-food filter      # 13.694 → ~5.400 candidatos
uv run nutriplan-food name        # nombre de cocina en español
uv run nutriplan-food validate    # audita y colapsa variantes
```

**Ningún paso llama a nadie.** El nombre en español sale de un léxico
(`traductor.py`), no de un modelo: USDA usa vocabulario controlado —641 cabezas
distintas para 5.400 filas— y un diccionario lo cubre entero. Corre en un
segundo, cuesta cero, da el mismo resultado siempre y se lee en un test. Un
modelo aquí solo aportaría idioma, y a cambio traía 442 llamadas, cuota agotada
y una corrida de dos horas que no terminó.

`curate` sigue existiendo para lo que el léxico no traduce
(`sin_traducir.jsonl`), es opcional y tampoco toca un solo número.

El reparto es el del README del proyecto: **el código es dueño de los números.**

- `filter` decide, con reglas: qué categorías son comida de casa, si la
  fila está cruda o cocida, qué rol juega en un plato (el frijol es carbohidrato,
  el tofu es proteína, aunque USDA los ponga en el mismo estante), qué
  restricciones toca, cuánto pesa una porción natural y cuánto rinde al cocinarse.
  También descarta los productos de marca: en las descripciones genéricas de USDA
  solo se capitaliza la primera palabra, y las de marca van en Title Case.
- `name` traduce con el léxico. Dos reglas lo gobiernan: el corte y la especie
  mandan sobre la familia («salmón», no «pescado»; «pechuga de pollo», no
  «pollo»), y una familia **sin** corte ni especie reconocidos NO recibe nombre.
  Eso último importa: las 150 filas que caerían bajo «carne de res» van de 128 a
  731 kcal, y la más corta era sebo. Sin corte, a cuarentena.
- `validate` audita: que los macros reconstruyan las kcal, que no haya más fibra
  que carbohidrato, que la porción sea creíble. Y colapsa las variantes — 859
  filas de «beef» son la misma vaca despiezada de catorce maneras, no 859
  alimentos; se queda la que una persona reconocería (servible antes que cruda,
  y entre iguales la descripción más corta, que en USDA es la genérica).

`data/usda/usda.sqlite` es staging local del importador: la app nunca lo lee y no
se versiona.

## Vocabulario de tags

`mariscos`, `pescado`, `gluten`, `lacteo`, `cerdo`, `res`, `huevo`, `frutos_secos`,
`soya`, `batido`, `vegano`, `condimento`, `conserva`, `platano`. Los dos últimos no
prohíben nada: existen porque el motor de platos los usa para descartar
combinaciones (no hay sopa de atún de lata ni de plátano). El vocabulario vive en
`domain/food_filter.KNOWN_TAGS` y hay un test que lo hace cumplir.
