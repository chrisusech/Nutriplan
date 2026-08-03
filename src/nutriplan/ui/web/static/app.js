/* Lo mínimo que la app necesita en el cliente.
   Vive en un archivo y no en un <script> inline para que la CSP pueda
   prohibir el JavaScript incrustado sin excepciones. */

(() => {
  'use strict';

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

  // --- Onboarding por pasos -----------------------------------------------
  // Una pantalla, una pregunta: en un móvil, seis secciones en un scroll son
  // un muro. El formulario sigue siendo UNO solo —un POST al final, sin estado
  // a medias en la base—; esto solo decide qué se ve. Sin JavaScript se ven
  // todos los pasos seguidos, que es exactamente lo que había antes.
  const stepper = document.querySelector('[data-stepper]');
  if (stepper) {
    const steps = [...stepper.querySelectorAll('.onb-step')];
    const back = stepper.querySelector('[data-back]');
    const next = stepper.querySelector('[data-next]');
    const submit = stepper.querySelector('[data-submit]');
    const fill = stepper.querySelector('[data-progress]');
    fill.dataset.of = String(stepper.querySelectorAll('.onb-step').length);
    const count = stepper.querySelector('[data-count]');
    let at = 0;

    const show = () => {
      steps.forEach((step, i) => { step.hidden = i !== at; });
      back.hidden = at === 0;
      next.hidden = at === steps.length - 1;
      submit.hidden = at !== steps.length - 1;
      fill.dataset.at = String(at + 1);
      count.textContent = `Paso ${at + 1} de ${steps.length}`;
      // El foco al título, o un lector de pantalla se queda en el paso viejo.
      const title = steps[at].querySelector('h2');
      if (title) { title.setAttribute('tabindex', '-1'); title.focus(); }
      window.scrollTo(0, 0);
    };

    // Avanzar con un campo inválido escondería el error en un paso que ya no
    // se ve: el navegador lo señala aquí y ahora.
    const valid = () => [...steps[at].querySelectorAll('input, select, textarea')]
      .every((field) => field.reportValidity());

    next.addEventListener('click', () => { if (valid()) { at += 1; show(); } });
    back.addEventListener('click', () => { at -= 1; show(); });
    // Enter en un campo suele significar "siguiente", no "enviar a medias".
    stepper.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT'
          && at < steps.length - 1) {
        event.preventDefault();
        if (valid()) { at += 1; show(); }
      }
    });
    show();
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
