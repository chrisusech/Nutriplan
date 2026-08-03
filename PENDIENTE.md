# Pendiente

Estado al 3 de agosto de 2026. Rama `main`, sobre el commit `8d0eb76` (Fase 6).
340 tests pasando, `ruff check` y `mypy` limpios, cobertura **87 %**.

**Nada de esto está commiteado todavía** (>50 archivos sueltos). Ver §2.

---

## 1. Bloqueante: hay perfiles a los que la app no les genera menú

Es lo primero que hay que hacer. Un usuario con uno de estos perfiles ve
"No se pudo generar el menú" y se va.

### Cómo reproducirlo

```bash
# Servidor sin IA, que es el camino que falla (el motor determinista)
env LLM_API_KEY="" LLM_BASE_URL="" uv run nutriplan
uv run python /tmp/perfiles.py     # ← este script hay que rehacerlo, ver abajo
```

El script recorre 5 perfiles por HTTP (registro → consentimiento → onboarding →
generar) e informa cuáles cuadran. Última ejecución: **3 bien, 2 mal**.

| Perfil | Estado |
|---|---|
| mujer 62 kg, déficit, 5 comidas | OK en 1 s (era el que fallaba; arreglado) |
| mujer 55 kg, déficit, 5 comidas | OK en 1 s |
| mujer 62 kg, déficit, 3 comidas | OK en 1 s |
| **hombre 90 kg, volumen, 5 comidas** | **FALLA** — día 3, desayuno: proteína objetivo 35,2 / real 49,1 |
| **hombre 70 kg, mantener, 4 comidas** | **FALLA** — día 6, desayuno: proteína objetivo 25,9 / real 41,6, y carb fuera |

### Lo que ya se sabe

El fallo está en el **desayuno**, y es la misma familia de problema que ya se
arregló en los snacks: platos que no pueden cuadrar el objetivo de proteína
porque las kcal del slot fijan la porción.

Diagnóstico del caso "hombre 70 kg" (objetivo del desayuno: 659 kcal, 24,6 g de
proteína, 111,5 g de carbohidrato):

```
platos de desayuno ofrecidos: 221
  min prot ~33,0 g  lacteo_carbo_crema  (queso cottage, avena, mantequilla de maní)
  min prot ~33,0 g  lacteo_carbo_crema  (queso cottage, pan integral, mantequilla de almendras)
  min prot ~32,9 g  lacteo_carbo_crema  (yogur griego, pan integral, mantequilla de maní)
```

O sea: los mejores platos de esa plantilla entregan **como mínimo 33 g** contra
un objetivo de 24,6. Se pasan por 8,4 g.

**Por qué el filtro los deja pasar:** `_fits_by_kcal`
(`adapters/llm/template_selector.py`) compara contra
`max(objetivo * tolerancia, MIN_RELEVANT_G)` y `MIN_RELEVANT_G` son 10 g, así
que 8,4 g de exceso se admite. Pero el solver acaba aterrizando en 41,6 g —
mucho más que el límite inferior de 33 — y ahí sí falla la validación.

### Las tres salidas, y cuál parece la buena

1. **Endurecer `_fits_by_kcal`** bajando el suelo. Riesgo real: en los snacks el
   suelo de 10 g es lo que impide quedarse sin ningún plato. Un suelo distinto
   por tamaño de slot suena a parche.
2. **Mirar por qué el solver aterriza en 41,6 cuando el mínimo del plato es 33.**
   Esos 8,6 g de diferencia son la clave y no están explicados. Si el solver
   pudiera acercarse a su propio mínimo, el plato cuadraría dentro de la
   tolerancia. **Es por donde yo empezaría.**
3. **Revisar el reparto del desayuno en `config/nutrition.default.yaml`**:
   `desayuno: {kcal: 0.27, protein_g: 0.22, carb_g: 0.30}`. Un desayuno con el
   27 % de las kcal pero solo el 22 % de la proteína obliga a rellenar con
   carbohidrato y grasa; con 111,5 g de carbohidrato en un desayuno, cualquier
   lácteo o crema que lo acompañe arrastra proteína de más. Puede ser que el
   reparto sea el que no cierra, no los platos.

### Lo que ya se arregló (no volver a tocarlo)

`dish_admissible` daba por bueno **cualquier** plato cuyo ancla se porcione en
gramos, con el argumento de que "se porciona fino: siempre aterriza donde haga
falta". No es cierto: las kcal del slot fijan la porción, y con ella la
proteína. Un yogur griego en un snack de 129 kcal son ~22 g de proteína contra
un objetivo de 5. Ahora `_fits_by_kcal` calcula el mínimo real (cada alimento en
su porción mínima + las kcal que falten cubiertas por el alimento menos
proteico) y descarta el plato si ni así baja del objetivo.

Cubierto por `tests/unit/test_platos_que_no_pueden_cuadrar.py`.

### Rehacer el script de perfiles

Vivía en `/tmp/perfiles.py` y se pierde. Merece la pena convertirlo en un test
de propiedad o en un script de `scripts/`: **recorrer una matriz de perfiles
plausibles y exigir que todos generen** es exactamente la garantía que faltaba,
y es lo que habría cazado esto antes de que el usuario lo viera.

---

## 2. Higiene, antes de seguir

- [ ] **Rotar la API key de Groq.** Se pegó en el chat de la sesión anterior.
- [ ] **Commitear.** Más de 50 archivos sin commitear: toda la Fase 7 (analítica,
      métricas, dashboard), el onboarding por pasos, y los arreglos de esta
      sesión. Si se pierde el working tree, se pierde todo.

---

## 3. Calidad, por debajo de la barra acordada

- [ ] **Cobertura 87 %**, la barra son 90 %. Lo que falta cubrir, por tamaño:
      `ui/web/routes/menu.py`, `adapters/db/repositories/auth.py`,
      `ui/web/routes/auth.py`, `application/generate_plan.py`.
- [ ] **`ruff format --check` falla en 67 archivos.** Ya fallaba antes de estas
      sesiones: el repo usa un estilo compacto de llamadas que `ruff format`
      quiere expandir. Decisión pendiente: alinear el repo o sacar
      `format --check` del criterio de verificación. No es un cambio a hacer sin
      decidirlo, porque son 67 archivos de diff.
- [ ] **Archivos por encima del techo de ~300 líneas:**
      `application/generate_plan.py` (546), `domain/models.py` (505),
      `adapters/llm/template_selector.py` (473). Los cortes están descritos en
      el plan (`.claude/plans/`).

---

## 4. Decisión de producto pendiente

**¿La IA elige los alimentos, o solo pone nombres?**

Los números medidos hoy:

- Con IA (Groq, `openai/gpt-oss-120b`): **~60 s** por menú. Consume ~2.600
  tokens de entrada y ~3.270 de salida, contra un límite gratuito de 8.000 por
  minuto. Dos generaciones seguidas chocan con el límite.
- Sin IA (motor determinista): **~1 s**, y los menús son válidos.

Ahora que el motor determinista produce menús buenos al instante, la opción de
que la IA solo nombre los platos y aporte matices —en vez de elegir los
alimentos— pesa más que antes. Pero cambia lo que el producto promete, así que
la decide Christopher.

Nota técnica si se mantiene la IA eligiendo: `max_tokens_select` está en 4096 y
**no se puede bajar**. Medido contra la API real: por debajo, el modelo trunca
la respuesta (devuelve 3 días de 7) y el esquema la rechaza siempre. Los tokens
de razonamiento salen del mismo presupuesto. Bajar `reasoning_effort` a `low`
tampoco sirve: entrega los 7 días pero incumple otras restricciones del esquema.

---

## 5. Móvil

- [ ] Nada probado en un dispositivo real. Todo lo verificado es Chrome a 390 px.
- [ ] El empaquetado con Capacitor necesita Xcode, CocoaPods y cuentas de
      desarrollador de Apple y Google. Los pasos están en `mobile/README.md`.

---

## Apéndice: trampas ya pisadas, para no repetirlas

- **La CSP bloquea los estilos inline también desde JavaScript**, no solo el
  atributo `style=` del HTML. `element.style.x = …` y `setProperty` se bloquean
  igual. Lo que cambie desde JS se cambia con clases o atributos `data-`.
  Vigilado por `tests/unit/test_csp_sin_estilos_inline.py`.
- **El atributo `hidden` no puede con un `display` de la hoja de estilos.** Si un
  elemento tiene `display: flex`, hay que añadir `[hidden] { display: none }` o
  se ve igual.
- **La fuente de iconos va subseteada** a los iconos que se usan. Añadir uno sin
  regenerarla no rompe nada visible en el código: sale el *nombre* del icono
  escrito en la pantalla. Regenerar con `uv run python scripts/fetch_fonts.py`.
  Vigilado por `tests/unit/test_iconos.py`.
- **Los tests nunca llaman a una IA real.** `tests/conftest.py` tiene dos
  candados (variables de entorno limpiadas y `httpx.AsyncClient.send`
  interceptado). Al añadir un proveedor nuevo, añadir su variable al candado.
- **SQLite necesita WAL y `busy_timeout`**, o la generación de fondo bloquea las
  peticiones (`database is locked`). Está en `adapters/db/session.py`.
