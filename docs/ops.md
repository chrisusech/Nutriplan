# Operaciones de NutriPlan (beta)

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
  LLM_BASE_URL=https://api.groq.com/openai/v1 \
  LLM_API_KEY=gsk_… \
  LLM_MODEL_GENERATE=openai/gpt-oss-20b \
  SMTP_HOST=… SMTP_USER=… SMTP_PASSWORD=… \
  SMTP_FROM='NutriPlan <no-reply@nutriplan.co>' \
  GOOGLE_CLIENT_ID=… \
  APPLE_CLIENT_ID=… \
  ADMIN_EMAIL=… ADMIN_PASSWORD=…
```

## Despliegue

```bash
fly deploy
# release_command corre: alembic upgrade head con DIRECT_DATABASE_URL
```

## Rotar claves

1. Groq: https://console.groq.com/keys — revoca la vieja, `fly secrets set LLM_API_KEY=…`
2. `SESSION_SECRET`: rota solo si se filtró (cierra todas las sesiones).
3. SMTP / OAuth: desde consola del proveedor.

## Incidentes

| Síntoma | Acción |
|---|---|
| Menús fallan por cuota Groq | `fly secrets unset LLM_API_KEY` — menú sigue; sin pasos AI |
| Deploy roto | `fly releases` → `fly deploy --image …` rollback |
| DB lenta / pooler | confirma que la app usa :6543 y Alembic :5432 |

## Dominio

Capacitor apunta a `https://app.nutriplan.co` (`mobile/capacitor.config.json`).
En Fly: `fly certs add app.nutriplan.co` y DNS CNAME al app.
