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
| Compartir día (`Share`) | botón `data-share-day` en la semana |
| Haptics al generar | al ver “Todo listo” |
| Splash | `launchAutoHide: true` + `hide()` al pintar; fondo `#131010` |
| Recordatorio diario (`LocalNotifications`) | al abrir la semana: cancela el de hoy y programa 7 días a las 11:00 |
| IAP StoreKit | botón `/plan` → hoja de Apple; el servidor verifica y concede semanas |

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

Dos cosas que hay que rehacer si se regenera `ios/` (está fuera de git):

1. `NSAppTransportSecurity` → `NSAllowsLocalNetworking` en `App/App/Info.plist`;
   sin eso iOS bloquea el `http` del Mac y la pantalla sale en blanco.
2. Para un iPhone físico: firma con tu Apple ID en Xcode, `NUTRIPLAN_URL` con la
   IP del Mac y el servidor escuchando en `0.0.0.0`, no en loopback.

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
