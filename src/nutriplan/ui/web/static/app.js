/* Lo mínimo que la app necesita en el cliente.
   Vive en un archivo y no en un <script> inline para que la CSP pueda
   prohibir el JavaScript incrustado sin excepciones. */

(() => {
  'use strict';

  // El color de marca llega como atributo del <body>: es el único estilo que
  // depende del servidor, y así no hace falta un <style> inline.
  // Solo el color base: el tinte lo deriva el CSS con color-mix() para que
  // siga al tema claro/oscuro. Fijarlo aquí lo congelaba en su versión clara.
  const brand = document.body.dataset.brand;
  if (brand) document.documentElement.style.setProperty('--brand', brand);

  // Al reemplazar un bloque grande, el documento se encoge, el navegador
  // recorta el scroll y al reinsertar ya no vuelve: calificar un plato te
  // mandaba al fondo de la página.
  let savedY;
  document.addEventListener('htmx:beforeSwap', () => { savedY = window.scrollY; });
  document.addEventListener('htmx:afterSettle', () => {
    if (savedY !== undefined) window.scrollTo(0, savedY);
  });

  // --- Puente nativo ------------------------------------------------------
  // La app se sirve desde el servidor también dentro de Capacitor, así que
  // esta misma página corre en el navegador y en el móvil. Lo nativo solo
  // aparece cuando de verdad hay un envoltorio debajo.
  const native = window.Capacitor?.isNativePlatform?.() ?? false;
  if (native) {
    document.documentElement.dataset.native = 'true';
    // Google bloquea su login dentro de un WebView plano: en móvil el ID token
    // sale del plugin nativo y se canjea en el mismo endpoint de siempre.
    for (const form of document.querySelectorAll('form.auth-social')) {
      form.hidden = false;
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const provider = form.action.endsWith('apple') ? 'apple' : 'google';
        try {
          const { result } = await window.Capacitor.Plugins.SocialLogin.login({
            provider,
            options: { scopes: ['email', 'profile'] },
          });
          form.querySelector('input[name=id_token]').value =
            result.idToken ?? result.identityToken ?? '';
          form.submit();
        } catch {
          // Cancelar el diálogo nativo no es un error que mostrar.
        }
      });
    }
  }

  // Un solo plato abierto a la vez: en un móvil, dos acordeones abiertos
  // convierten la lista en un scroll interminable.
  document.addEventListener('toggle', (event) => {
    const meal = event.target;
    if (!meal.matches('details.meal') || !meal.open) return;
    for (const other of document.querySelectorAll('details.meal[open]')) {
      if (other !== meal) other.open = false;
    }
  }, true);
})();
