# Beta cerrada — operación

## Cuota

Ya implementada en `application/quota.py` + tests
`tests/integration/test_cuota_del_beta.py`: el primer menú es libre; el
siguiente se desbloquea calificando platos (`RATINGS_REQUIRED`).

## Invites

1. TestFlight Internal + Play Internal testing (lista de emails).
2. Código de app: `BETA_INVITE_CODE` (vacío = abierto). En prod pon uno y
   compártelo con los testers; aparece en `/registro`.
3. Empezar con 10–20 personas; subir a ~50 si es estable.
4. Canal: in-app `/feedback` + grupo WhatsApp/Telegram del equipo.

## Criterios de salida → soft launch

- [ ] Matriz de perfiles 100 % en CI (`test_matriz_de_perfiles`)
- [ ] Crash-free > 99 % en devices de prueba
- [ ] Generación P95 < 5 s (motor)
- [ ] 0 reportes de “menú vacío” sin salida accionable
- [ ] Cobertura ≥ 90 %, `ruff check` y `mypy` limpios
- [ ] SMTP real y dominio `app.nutriplan.co` estables 7 días

## Si Groq cae

Menú no depende de IA (`LLM_SELECT_FOODS=false`). Quitar `LLM_API_KEY` y las
recetas salen del YAML / sin pasos.
