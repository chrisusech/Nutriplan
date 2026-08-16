# Protocolo de QA en iPhone físico

StoreKit y Sign in with Apple **no se prueban en Cloudflare** ni en Safari del
Mac. El cobro y el login de Apple viven en el binario. Este pase pide **iOS 15
o más** (StoreKit 2 / `@capgo/native-purchases`).

## Qué no sustituye esto

- El túnel `*.trycloudflare.com` sirve para menú, onboarding y recetas.
- Un POST `local-…` en `ENV=local` no es una compra de Apple.
- El simulador de Xcode no sustituye Sandbox en un iPhone real.

## Requisitos

| Pieza | Detalle |
|---|---|
| iPhone | iOS ≥ 15. Mira Ajustes → General → Información. |
| Cable | Modo desarrollador encendido; confiar en el Mac. |
| Firma | Xcode: Team, bundle `app.nutriplan`, **Sign in with Apple**, **In-App Purchase**. |
| Sandbox | Otro Apple ID en App Store Connect. En el iPhone: Ajustes → App Store → Cuenta de Sandbox. No uses el Apple ID con el que pagas Netflix. |
| Servidor | Contra el Mac (`NUTRIPLAN_URL=http://IP:8000`) mientras se escribe código; contra prod (`https://app.nutriplan.co` o `https://….fly.dev`) antes del Archive. |

iOS 14 abre el envoltorio viejo; **no** el cobro. iOS 13 o menos: Capacitor 7 no lo soporta.

## Checklist (una pasada)

1. Instalar desde Xcode (no TestFlight hasta que prod responda `/health`).
2. Abrir la app: splash se oculta; no queda la pantalla en blanco.
3. Registro o Sign in with Apple. Un re-login de Apple **sin correo** entra si esa cuenta ya existía.
4. Onboarding → generar menú → abrir un plato (receta YAML si Gemini no responde).
5. `/plan`: se ven **precios reales** de App Store, no un número inventado.
6. Comprar `nutriplan.monthly` con la cuenta Sandbox. La hoja es de Apple. Tras confirmar, el perfil tiene semanas.
7. **Restaurar compras** en otro arranque (o tras borrar la app) vuelve a conceder la misma transacción, no duplica.
8. Cancelar la hoja de Apple no muestra un error técnico.
9. No aparece “429”, “Gemini”, ni un dump de macros en ninguna pantalla.

## Sandbox, no dinero real

Mientras la app salga de Xcode, el cobro es Sandbox. Si usas tu Apple ID personal
y una tarjeta de verdad, o no deja comprar, o (cuando ya esté en la Store) te
cobra. El tester Sandbox es obligatorio.

El revisor de Apple también cobra en Sandbox contra el binario de **prod**. El
servidor acepta JWS `Sandbox` y `Production`.

## Tras el código: no Archive todavía

Hasta que Fly + Postgres + `/health` respondan 200, el `.ipa` es una pantalla
en blanco. Ver `docs/ops.md`. Pegar en App Store Connect el webhook
`https://<host>/internal/app-store`.
