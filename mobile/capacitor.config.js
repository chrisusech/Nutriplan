// El WebView carga la app servida, no un bundle: por defecto producción, y
// contra el Mac cuando se prueba en el simulador o en un teléfono de la wifi:
//   NUTRIPLAN_URL=http://localhost:8000 npx cap run ios
const url = process.env.NUTRIPLAN_URL || 'https://app.nutriplan.co';
const local = !url.startsWith('https://');

module.exports = {
  appId: 'app.nutriplan',
  appName: 'NutriPlan',
  webDir: 'www',
  server: { url, cleartext: local, androidScheme: local ? 'http' : 'https' },
  ios: {
    // La app ya pinta sus propios márgenes con env(safe-area-inset-*): si el
    // WebView además inserta los suyos, el pie se despega y asoma el fondo.
    contentInset: 'never',
    backgroundColor: '#131010',
    zoomEnabled: false,
    // Con esto activado WebKit solo navega a los dominios de WKAppBoundDomains,
    // que es el de producción: en local bloquearía la primera carga.
    limitsNavigationsToAppBoundDomains: !local,
  },
  android: { allowMixedContent: local },
  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 800,
      backgroundColor: '#131010',
      androidSplashResourceName: 'splash',
    },
    LocalNotifications: {
      smallIcon: 'ic_stat_icon',
      iconColor: '#FF7A62',
    },
    Keyboard: {
      resize: 'native',
      resizeOnFullScreen: true,
    },
    SocialLogin: {
      providers: {
        google: true,
        apple: true,
        facebook: false,
        twitter: false,
      },
    },
  },
};
