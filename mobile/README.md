# NutriPlan móvil (Capacitor)

Envoltorio nativo. El WebView carga `https://app.nutriplan.co` (ver
`capacitor.config.json`). Guía de listing: `docs/store-listing.md`.

## Capacidades nativas (Apple 4.2)

| Capacidad | Estado en código |
|---|---|
| Login Google/Apple (`SocialLogin`) | `app.js` + `/auth/oauth/{provider}` |
| Caché offline del menú (`Preferences`) | `app.js` guarda `week_html` |
| Compartir día (`Share`) | botón `data-share-day` en la semana |
| Haptics al generar | al ver “Todo listo” |
| Push | plugin declarado; envío server-side aún no (no declarar en listing) |

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

Desarrollo contra máquina local:

```json
"server": { "url": "http://192.168.1.10:8000", "cleartext": true }
```

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
