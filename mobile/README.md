# NutriPlan móvil (Capacitor)

Envoltorio nativo. El WebView carga `https://app.nutriplan.co` (ver
`capacitor.config.js`), o el servidor de tu Mac si defines `NUTRIPLAN_URL`.
Guía de listing: `docs/store-listing.md`. Infra de prod: `docs/ops.md`
(Fly + Supabase Pro + Gemini de pago + SMTP; no Vercel).

## Capacidades nativas (Apple 4.2)

| Capacidad | Estado en código |
|---|---|
| Login Google/Apple (`SocialLogin`) | `app.js` + `/auth/oauth/{provider}` |
| Caché offline del menú (`Preferences`) | `app.js` guarda `week_html` |
| Haptics al generar | al ver “Todo listo” |
| Splash | `launchAutoHide: true` + `hide()` al pintar; fondo `#131010` |
| Recordatorio diario (`LocalNotifications`) | al abrir la semana: cancela el de hoy y programa 7 días a las 11:00 |
| IAP StoreKit (`@capgo/native-purchases`) | `/plan` abre la hoja de Apple; el servidor verifica el JWS |
| Push | **no** está en el binario; no declarar en el listing |

QA del cobro: `docs/iphone-qa.md` (iOS ≥15, Sandbox, no Cloudflare).

## Compilar

```bash
cd mobile
npm install
npx cap add ios       # una vez; requiere CocoaPods
npx cap add android   # una vez
npx @capacitor/assets generate --iconBackgroundColor '#F3EAE4' \
  --splashBackgroundColor '#F3EAE4'
# Coloca GoogleService-Info.plist / google-services.json (NO en git)
npx cap sync
npx cap open ios
npx cap open android
```

## Probar en el simulador de iPhone

```bash
uv run nutriplan                 # el servidor, en otra terminal
cd mobile
NUTRIPLAN_URL=http://localhost:8000 npx cap run ios
```

`NUTRIPLAN_URL` con `http://` marca el modo local: apaga
`limitsNavigationsToAppBoundDomains` (con él activado WebKit solo navega al
dominio de producción) y permite texto en claro. Sin la variable se compila
contra producción, que es lo que va a la store.

Tres cosas que hay que rehacer si se regenera `ios/` (está fuera de git):

1. `NSAppTransportSecurity` en `App/App/Info.plist`: `NSAllowsLocalNetworking`
   **y** `NSAllowsArbitraryLoadsInWebContent`. Lo primero no basta: el WebView
   sigue exigiendo HTTPS contra la IP del Mac (`NSURLErrorDomain -1022`) y la
   pantalla se queda en el splash. Solo para el binario de desarrollo; el de la
   Store carga `https://` y no necesita la excepción de WebContent.
2. Para un iPhone físico: firma con tu Apple ID en Xcode. El servidor tiene que
   escuchar en `0.0.0.0` (no en loopback) y `NUTRIPLAN_URL` tiene que ser la IP
   **real** del Mac, no un placeholder:

   ```bash
   IP=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)
   NUTRIPLAN_URL=http://$IP:8000 npx cap copy ios
   ```

   `http://192.168.x.x:8000` no es un host: iOS responde `-1003` (hostname
   could not be found) y el splash no arranca. `localhost` solo sirve en el
   simulador del mismo Mac.
3. `IPHONEOS_DEPLOYMENT_TARGET = 15.0` en el proyecto y el target App (Debug y
   Release). Capacitor 7 deja 14.0; `@capgo/native-purchases` exige 15. El
   Podfile ya dice `platform :ios, '15.0'`. Sin esto Xcode avisa al enlazar el
   framework de compras. La app pide iOS 15 o más (`docs/iphone-qa.md`).

Depurar: Safari del Mac → Desarrollo → Simulador → la vista de NutriPlan; da
consola e inspector sobre el WebView. Como la app carga el servidor y no un
bundle, un cambio de Python/CSS se ve al recargar: no hay que recompilar. Solo
se recompila al tocar plugins o `capacitor.config.js`.

## Consolas

| Artefacto | Dónde |
|---|---|
| `GoogleService-Info.plist` / `google-services.json` | Google Cloud |
| `GOOGLE_CLIENT_ID` (web, es el `aud`) | `.env` / Fly secrets |
| Sign in with Apple + Service ID | Apple Developer |
| TestFlight Internal / Play Internal | `docs/store-listing.md` |

## CSP

La CSP permite `capacitor:` / `ionic:` (`ui/web/security.py`). Verificar en
dispositivo real que `window.Capacitor` existe; si no, el login social no
aparece y sigue el camino correo/contraseña.
