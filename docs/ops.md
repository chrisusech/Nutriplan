# Operaciones de NutriPlan (beta y tienda)

Apple **no hospeda el backend**. El `.ipa` es un WebView (Capacitor) que carga
`https://app.nutriplan.co`. Si ese servidor se cae, la app instalada muestra
una pantalla en blanco. Por eso hace falta un proceso 24/7, Postgres, correo y
una API de IA — aparte de la cuenta de desarrollador.

## Dónde vive cada pieza

| Pieza | Dónde | Por qué |
|---|---|---|
| App FastAPI | **Fly.io** (`fly.toml`, región `mia`, 512 MB, `min_machines_running = 1`) | Proceso largo: jobs, HTMX, recetas. No es una función de 10 s. |
| Postgres | **Supabase Pro** (pooler `:6543`, migraciones `:5432`) | El Free se pausa; un tester a las 8 am espera 30 s o error. Apple lo ve como app rota. |
| IA | **Google Gemini** (AI Studio / Vertex, de pago) | El menú lo arma `engine-v1`; Gemini escribe recetas y lee el check-in. |
| Correo | **Resend** (SMTP) | Verificar cuenta y recuperar clave. El adaptador de consola no existe en el iPhone. |
| DNS | `app.nutriplan.co` → Fly | `fly certs add app.nutriplan.co` |
| Cobro iOS | App Store IAP (`nutriplan.monthly` / `nutriplan.annual`) | Guía 3.1.1: no Stripe dentro del WebView. |

**No uses Vercel como API de esta app.** Timeouts, frío, sin proceso para jobs.
**No hace falta GCP para hospedar FastAPI** solo porque Gemini vive en Google.
Render o Railway sirven si Fly molesta; no ahorran el cambio.

## Secrets en Fly

```bash
fly secrets set \
  ENV=prod \
  DATABASE_URL='postgresql+asyncpg://…@….pooler.supabase.com:6543/postgres' \
  DIRECT_DATABASE_URL='postgresql+asyncpg://…@db.…supabase.co:5432/postgres' \
  SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  BASE_URL=https://app.nutriplan.co \
  ALLOWED_HOSTS=app.nutriplan.co \
  BETA_INVITE_CODE='…' \
  RUN_MIGRATIONS_ON_START=false \
  LLM_SELECT_FOODS=false \
  LLM_REFINE_NAMES=false \
  LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai \
  LLM_API_KEY=… \
  LLM_MODEL_GENERATE=gemini-2.0-flash \
  SMTP_HOST=smtp.resend.com SMTP_PORT=587 SMTP_USER=resend SMTP_PASSWORD=… \
  SMTP_FROM='NutriPlan <no-reply@nutriplan.co>' \
  GOOGLE_CLIENT_ID=… \
  APPLE_CLIENT_ID=app.nutriplan \
  ADMIN_EMAIL=… ADMIN_PASSWORD=…
```

`APPLE_CLIENT_ID` es el **bundle** `app.nutriplan` (el `aud` del identity token
nativo). No uses un Services ID de web.

Webhook de App Store Server Notifications V2 (sin secreto compartido; Apple
firma el JWS):

```
https://app.nutriplan.co/internal/app-store
```

Si aún no hay dominio propio, vale `https://<app>.fly.dev/internal/app-store`.

Nunca en git. Techo de gasto en Google (20–50 USD/mes al empezar). No actives
`LLM_SELECT_FOODS` en prod.

## Despliegue

```bash
fly deploy
# release_command corre: alembic upgrade head con DIRECT_DATABASE_URL
```

512 MB basta hasta que el menú o las recetas se queden sin RAM. No pases a 1 GB
«por si acaso». Una sola región (`mia`). No apagues la máquina de prod.

## Rotar claves

1. Gemini / Groq: revoca la vieja, `fly secrets set LLM_API_KEY=…`
2. `SESSION_SECRET`: rota solo si se filtró (cierra todas las sesiones).
3. SMTP / OAuth: desde consola del proveedor.

## Incidentes

| Síntoma | Acción |
|---|---|
| Recetas 429 Gemini | techo de cuota; el menú sigue (engine-v1); receta cae a YAML |
| Menús fallan por selección IA | `LLM_SELECT_FOODS=false` — menú sigue |
| Deploy roto | `fly releases` → `fly deploy --image …` rollback |
| DB lenta / pooler | confirma que la app usa :6543 y Alembic :5432 |
| «too many connections» en pgbouncer | cada instancia abre hasta `POOL_SIZE + POOL_MAX_OVERFLOW` (10, en `adapters/db/session.py`). Multiplícalo por las instancias vivas antes de escalar |
| App en blanco en el iPhone | Fly caído o DNS; `min_machines_running` tiene que ser 1 |

## Dominio

Capacitor apunta a `https://app.nutriplan.co` (`mobile/capacitor.config.js`).
**No es obligatorio para Apple**: el WebView puede cargar `https://<app>.fly.dev`
(HTTPS lo da Fly) y el usuario nunca escribe esa URL. Sí es casi obligatorio en
la práctica: el hostname se **compila dentro del .ipa**; cambiarlo después
exige un update en la Store, y sin dominio propio el correo de verificación se
ve spam. Se puede comprar en paralelo al código. En Fly:
`fly certs add app.nutriplan.co` y DNS CNAME al app.

## Primer arranque (humano, tras el merge)

1. Proyecto Supabase Pro + `DATABASE_URL` (pooler 6543) y `DIRECT_DATABASE_URL` (5432)
2. `fly deploy` con el Dockerfile (incluye extra `apple`)
3. Secrets de la lista de arriba. `BASE_URL` y `ALLOWED_HOSTS` = el hostname
   real (`app.nutriplan.co` o `*.fly.dev`)
4. Desde Safari: `/health` debe responder `{"status":"ok"}`. Luego `/login`,
   `/privacidad`, `/terminos`, `/soporte` y un registro real (el correo tiene
   que llegar)
5. Pegar el webhook IAP en App Store Connect
6. Recién entonces: productos `nutriplan.monthly` / `nutriplan.annual`, banco,
   Archive. QA en iPhone: `docs/iphone-qa.md`

Hasta que `/health` no responda 200, **no** Archive.

## Backups

Los de Supabase Pro. Un `pg_dump` periódico no está de más. SQLite
(`data/app.db`) es solo local.

## Presupuesto de beta (orden)

Fly 512 MB ~5–15 USD, Supabase Pro ~25 USD, Gemini de pago (pocos USD con 50
testers; techo 20–50), SMTP free o ~10, dominio ~15/año, Apple Developer 99/año.
Total ~40–90 USD/mes + Apple. El riesgo no es el precio: es publicar con
Supabase Free + Gemini Free y que el revisor vea 429 y DB dormida.
