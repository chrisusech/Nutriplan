# Pendiente

Estado al 3 de agosto de 2026 — plan de lanzamiento beta en curso.

## Hecho en esta pasada

- [x] Working tree commitado; `.env` local en SQLite; clave Groq vaciada (rotar en
      consola si aún estaba viva).
- [x] Filtro de desayuno alineado al solver + margen; matriz de perfiles en CI
      (`tests/integration/test_matriz_de_perfiles.py`).
- [x] `LLM_SELECT_FOODS=false` / `LLM_REFINE_NAMES=false`; Groq 20b solo recetas.
- [x] Dockerfile + `fly.toml` + `/health` + `docs/ops.md`.
- [x] `/terminos`, `/soporte`, OAuth UI + offline/share/haptics en `app.js`.
- [x] Docs de listing y beta; ruff format NO es gate de CI.

## Todavía humano (fuera del repo)

- [ ] Rotar clave Groq en console.groq.com (la antigua se pegó en chat).
- [ ] Crear proyecto Supabase prod + `fly launch` / secrets / DNS `app.nutriplan.co`.
- [ ] SMTP real (Resend/Postmark) con SPF/DKIM.
- [ ] Cuentas Apple Developer + Play Console; `cap add ios/android` en máquina con Xcode.
- [ ] GoogleService-Info.plist / google-services.json; capturas; TestFlight / Internal Testing.
- [ ] Invites 10–50 + canal de feedback; QA en iPhone y Android reales.
