# Pendiente

Estado al 3 de agosto de 2026 — plan de lanzamiento beta en curso.

## Listo en código para probar (web / smoke local)

Flujo: `uv sync` → `uv run alembic upgrade head` → `uv run nutriplan` →
registro → consentimiento → onboarding → generar menú → calificar → feedback.

- [x] Motor determinista + cuota beta + legal público + health/Docker/Fly.
- [x] Cobertura ≥90 %; push: registro de token; splash hide; gate
      `BETA_INVITE_CODE`; tokens borrados al eliminar cuenta.
- [x] Dockerfile con extra `apple` (Sign in with Apple en prod).

## Todavía humano (fuera del repo)

- [ ] Rotar clave Groq en console.groq.com (la antigua se pegó en chat).
- [ ] Crear proyecto Supabase prod + `fly launch` / secrets / DNS `app.nutriplan.co`
      (ver `docs/ops.md`); incluir `BETA_INVITE_CODE`.
- [ ] SMTP real (Resend/Postmark) con SPF/DKIM.
- [ ] `brew install cocoapods` + `npx cap add ios` (Android: `cd mobile && npx cap sync`).
- [ ] GoogleService-Info.plist / google-services.json; capturas; TestFlight /
      Play Internal (ver `docs/store-listing.md`).
- [ ] Invites 10–50 + canal de feedback; QA en iPhone y Android reales.
