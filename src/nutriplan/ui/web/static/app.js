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

  const native = window.Capacitor?.isNativePlatform?.() ?? false;
  const prefs = window.Capacitor?.Plugins?.Preferences;
  const haptics = window.Capacitor?.Plugins?.Haptics;
  const sharePlugin = window.Capacitor?.Plugins?.Share;
  const social = window.Capacitor?.Plugins?.SocialLogin;
  const push = window.Capacitor?.Plugins?.PushNotifications;
  const splash = window.Capacitor?.Plugins?.SplashScreen;
  const bootScript = document.querySelector('script[data-google-client-id]');
  const loggedIn = document.body?.dataset?.loggedIn === '1';

  const csrf = () =>
    document.querySelector('meta[name="csrf-token"]')?.content
    || document.querySelector('input[name="_csrf"]')?.value
    || '';

  // --- Puente nativo ------------------------------------------------------
  if (native) {
    document.documentElement.dataset.native = 'true';
    // launchAutoHide=false: hay que ocultarlo a mano o la app se queda congelada.
    splash?.hide?.().catch(() => {});

    const googleId = bootScript?.dataset.googleClientId || '';
    const appleId = bootScript?.dataset.appleClientId || '';
    if (social?.initialize) {
      social.initialize({
        google: googleId ? { webClientId: googleId } : undefined,
        apple: appleId ? { clientId: appleId } : undefined,
      }).catch(() => {});
    }

    // Push solo con sesión: sin cookie, POST /device-tokens redirige al login.
    if (push && loggedIn) {
      push.requestPermissions().then((perm) => {
        if (perm.receive !== 'granted') return;
        push.register();
      }).catch(() => {});
      push.addListener('registration', (ev) => {
        const body = new URLSearchParams({
          platform: (window.Capacitor.getPlatform?.() || 'ios'),
          token: ev.value,
          _csrf: csrf(),
        });
        fetch('/device-tokens', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-CSRF-Token': csrf(),
          },
          body,
          credentials: 'same-origin',
        }).catch(() => {});
      });
    }

    for (const shareBtn of document.querySelectorAll('[data-share-day]')) {
      shareBtn.hidden = false;
    }

    for (const form of document.querySelectorAll('form.auth-social')) {
      form.hidden = false;
      form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const provider = form.action.endsWith('apple') ? 'apple' : 'google';
        const inviteField = form.querySelector('input[name=invite]');
        const visibleInvite = document.querySelector('form input[name=invite]');
        if (inviteField && visibleInvite) inviteField.value = visibleInvite.value;
        try {
          const { result } = await social.login({
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
    // Caché de la última semana vista: útil en el súper sin red.
    const cacheWeek = async () => {
      if (!prefs) return;
      const main = document.querySelector('main');
      if (!main || !document.querySelector('nav.days')) return;
      await prefs.set({ key: 'week_html', value: main.innerHTML });
      await prefs.set({ key: 'week_at', value: String(Date.now()) });
    };
    document.addEventListener('htmx:afterSettle', () => { cacheWeek(); });
    cacheWeek();

    // Si el servidor no responde, mostrar la semana en caché.
    window.addEventListener('offline', async () => {
      if (!prefs) return;
      const { value } = await prefs.get({ key: 'week_html' });
      const main = document.querySelector('main');
      if (value && main) {
        main.innerHTML = value;
        const banner = document.createElement('p');
        banner.className = 'alert';
        banner.textContent = 'Sin conexión: estás viendo tu último menú guardado.';
        main.prepend(banner);
      }
    });

    // Compartir el día visible.
    document.addEventListener('click', async (event) => {
      const btn = event.target.closest('[data-share-day]');
      if (!btn || !sharePlugin) return;
      event.preventDefault();
      const title = document.querySelector('.day-summary .kcal')?.textContent
        || 'Mi menú NutriPlan';
      try {
        await sharePlugin.share({
          title: 'NutriPlan',
          text: `Mi día: ${title.trim()}`,
          dialogTitle: 'Compartir menú',
        });
      } catch { /* cancelado */ }
    });

    // Haptico al completar generación.
    document.addEventListener('htmx:afterSettle', (event) => {
      if (!haptics) return;
      const t = event.detail?.target;
      if (t && /Todo listo/.test(t.textContent || '')) {
        haptics.impact({ style: 'MEDIUM' }).catch(() => {});
      }
    });
  }

  // --- Onboarding por pasos -----------------------------------------------
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
      const title = steps[at].querySelector('h2');
      if (title) { title.setAttribute('tabindex', '-1'); title.focus(); }
      window.scrollTo(0, 0);
    };

    const valid = () => [...steps[at].querySelectorAll('input, select, textarea')]
      .every((field) => field.reportValidity());

    next.addEventListener('click', () => { if (valid()) { at += 1; show(); } });
    back.addEventListener('click', () => { at -= 1; show(); });
    stepper.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target.tagName === 'INPUT'
          && at < steps.length - 1) {
        event.preventDefault();
        if (valid()) { at += 1; show(); }
      }
    });
    show();
  }

  document.addEventListener('toggle', (event) => {
    const meal = event.target;
    if (!meal.matches('details.meal') || !meal.open) return;
    for (const other of document.querySelectorAll('details.meal[open]')) {
      if (other !== meal) other.open = false;
    }
  });
})();
