# Listing tiendas — NutriPlan beta cerrada

## Identidad

- **Nombre:** NutriPlan
- **Bundle / applicationId:** `app.nutriplan`
- **Categoría:** Health & Fitness / Salud y bienestar
- **Edad:** 13+
- **Precio:** Gratis con prueba de 1 semana; suscripción IAP mensual/anual
  (`nutriplan.monthly`, `nutriplan.annual`). El precio se pone en App Store
  Connect, no en el código.

## URLs (prod)

- Privacy: `https://app.nutriplan.co/privacidad`
- Terms: `https://app.nutriplan.co/terminos`
- Support: `https://app.nutriplan.co/soporte`
- Marketing: `https://app.nutriplan.co`

## Textos cortos (ES)

**Subtítulo (30):** Menú semanal a tu medida

**Descripción corta:**
Genera tu menú de la semana (kcal y macros cuadrados) con platos reales
y recetas. Beta cerrada.

**Descripción larga:**
NutriPlan arma siete días de comidas según tu objetivo, peso y cuántas veces
comes. Los números los calcula el código; la IA solo escribe los pasos de
cocina. Califica platos, regenera con cuota de beta, y consulta tu menú
aunque no haya red (caché en el móvil).

## Capturas a preparar

1. Onboarding (paso “¿Qué quieres lograr?”)
2. Semana con días
3. Plato abierto (ingredientes + pasos)
4. Perfil / privacidad
5. Pantalla de generación (“Todo listo”)

Tamaños: iPhone 6.7" + Pixel 6. Feature graphic Play 1024×500.

## Data safety / Privacy Nutrition Labels

| Dato | Recogido | Vinculado al usuario | Tracking |
|---|---|---|---|
| Correo / nombre | Sí | Sí | No |
| Salud (peso, altura, objetivo) | Sí | Sí | No |
| Identificadores (sesión, OAuth sub) | Sí | Sí | No |
| Uso de la app (eventos) | Sí (opt-in consentimiento) | No (agregado) | No |
| Compra / publicidad | Sí (IAP Apple) | Sí (user_id + transaction_id) | No |

No se venden datos. IA de terceros recibe solo alimentos del plato (ver privacidad).

## Apple

1. Apple Developer Program
2. App Store Connect → app `app.nutriplan`
3. Sign in with Apple capability
4. Productos de suscripción `nutriplan.monthly` y `nutriplan.annual` **antes**
   del review si el paywall ya está. Sandbox tester para el revisor.
5. TestFlight **Internal** puede salir **sin** IAP (una semana + activación a mano).
6. Cuenta demo para revisión (correo + clave en las notas de review).
7. Export compliance: HTTPS only / Exempt encryption if applicable
8. **No declarar Push** en el listing: el plugin no está en el binario.
9. In-App Purchase capability + productos de suscripción auto-renovables.
10. App Privacy: compras, salud, identificadores; sin tracking.
11. Copyright: el titular de la cuenta de desarrollador (persona o sociedad).
    Licencias de terceros: ver `pyproject.toml` / `mobile/package.json`.
12. Edad 13+. Cuenta demo en las notas de review.

## Titular que publica

Apple cobra a la **entidad de la cuenta de desarrollador** (individuo o
empresa). Esa misma entidad aparece como vendedor en la ficha y es quien firma
privacidad y términos (`/privacidad`, `/terminos`). Si todavía es una persona
física, los textos legales deben decirlo; no prometas una sociedad que no
existe. El Paid Apps Agreement, banco e impuestos van **antes** del Submit con
IAP.

## Google Play

1. Play Console ($25)
2. Internal testing track
3. Data safety form (tabla arriba)
4. Content rating questionnaire

## Builds

```bash
cd mobile
npm install
npx cap add ios      # una vez
npx cap add android  # una vez
npx @capacitor/assets generate --iconBackgroundColor '#F3EAE4' --splashBackgroundColor '#F3EAE4'
# Coloca GoogleService-Info.plist / google-services.json (no en git)
npx cap sync
npx cap open ios
npx cap open android
```

## Checklist device (antes de invites)

- [ ] Registro + consentimiento + onboarding
- [ ] Generar menú (~1 s)
- [ ] Ver semana offline (modo avión tras cache)
- [ ] Login Google / Apple (mismo correo no duplica cuenta)
- [ ] Paywall `/plan` + restaurar compras (sandbox; **no** en Cloudflare)
- [ ] Eliminar cuenta
- [ ] `/privacidad` `/terminos` `/soporte` abren
- [ ] Splash no se queda en negro
