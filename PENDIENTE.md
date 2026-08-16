# Pendiente

Estado al 3 de agosto de 2026 — plan de lanzamiento beta en curso.

## Listo en código para probar (web / smoke local)

Flujo: `uv sync` → `uv run alembic upgrade head` → `uv run nutriplan` →
registro → consentimiento → onboarding → generar menú → calificar → feedback.

- [x] Motor determinista + cuota beta + legal público + health/Docker/Fly.
- [x] Cobertura ≥90 %; push: registro de token; splash hide; gate
      `BETA_INVITE_CODE`; tokens borrados al eliminar cuenta.
- [x] Dockerfile con extra `apple` (Sign in with Apple en prod).
- [x] Topes de porción (máx. 3 tortillas / 2 arepas); IA elige+nombra vía
      `LLM_SELECT_FOODS` + `LLM_REFINE_NAMES` cuando hay API key.

## Mejoras de menú en curso

- [ ] Plantillas con 2 carbos cuando el slot pide mucho; matriz 2 perfiles altos
      aún fallan por variedad de avena.
- [ ] Iconos Material (`WB_SUNNY`) que se ven como texto — fuente/ligadura.
- [ ] Clave Groq nueva + regenerar menús para notar nombres personalizados.

## Todavía humano (fuera del repo)

- [ ] Cuenta Apple Developer (sin ella no se firma el iPhone).
- [ ] Proyecto Supabase Pro + `fly deploy` / secrets / `/health`
      (ver `docs/ops.md`). Dominio propio opcional: vale `*.fly.dev`.
- [ ] SMTP real (Resend) con SPF/DKIM. Recomendado si se quiere correo serio.
- [ ] `APPLE_CLIENT_ID=app.nutriplan` y webhook
      `https://<host>/internal/app-store` en App Store Connect.
- [ ] Productos IAP `nutriplan.monthly` / `nutriplan.annual`, banco, impuestos.
- [ ] QA en iPhone físico iOS ≥15 con Sandbox (`docs/iphone-qa.md`). No se
      prueba el cobro en Cloudflare.
- [ ] Titular legal de la ficha, privacidad/términos vs datos reales
      (`docs/store-listing.md`). **No declarar Push.**
- [ ] `brew install cocoapods` + `npx cap add ios`.
- [ ] GoogleService-Info.plist / google-services.json; capturas 6.7".
- [ ] Invites 10–50 + canal de feedback.
