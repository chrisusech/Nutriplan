# NutriPlan móvil (Capacitor)

Envoltorio nativo de la app. El WebView carga la app **desde el servidor**
(`server.url`), no un paquete local: la app es HTML renderizado en servidor con
HTMX, así que empaquetarla exigiría reescribirla como SPA con API JSON.

Que sea remota tiene una consecuencia que hay que tener presente: **Apple
rechaza los envoltorios sin funcionalidad propia** (guía 4.2). Por eso la app
nativa aporta lo que un navegador no puede:

- Login con Google y con Apple **nativos**. No es un adorno: Google bloquea su
  login dentro de un WebView plano, así que sin esto no hay login social en el
  móvil.
- Notificaciones push (recordar el menú de la semana).
- Caché del menú para consultarlo sin conexión, que es justo cuando hace falta:
  en el supermercado.
- Compartir y haptics.

## Antes de compilar

```bash
cd mobile
npm install
npx cap add ios       # requiere CocoaPods: brew install cocoapods
npx cap add android   # requiere Android Studio
npx cap sync
```

Apunta `server.url` en `capacitor.config.json` a tu dominio real antes de
compilar. En desarrollo puedes usar tu IP local:

```json
"server": { "url": "http://192.168.1.10:8000", "cleartext": true }
```

## Lo que falta y necesita una máquina con Xcode

| Paso | Dónde |
|---|---|
| `GoogleService-Info.plist` / `google-services.json` | consola de Google Cloud |
| Client ID de Google (iOS, Android y Web) | `.env` del servidor → `GOOGLE_CLIENT_ID` |
| Capacidad "Sign in with Apple" y Service ID | portal de Apple Developer |
| Iconos y splash nativos | `npx capacitor-assets generate` |
| Firma y perfiles | Xcode / Play Console |

## Riesgo conocido, sin verificar en dispositivo

La app sirve una CSP estricta sin `unsafe-inline`. Capacitor inyecta su puente
en la página; si el WebView lo bloqueara, `window.Capacitor` no existiría y el
login nativo no aparecería (la app seguiría funcionando con correo y
contraseña). Hay que comprobarlo en un dispositivo real: si pasa, se añaden los
orígenes de Capacitor a la CSP en `ui/web/security.py`.
