/* El puente con Capacitor: lo que solo existe cuando la app corre en un móvil.
   En el navegador todo esto se queda callado y la web funciona igual. */

import { initReminders } from './reminders.js';

const cap = window.Capacitor;
export const isNative = cap?.isNativePlatform?.() ?? false;

const plugins = cap?.Plugins ?? {};
const { Preferences: prefs, Haptics: haptics, Share: sharePlugin,
        SocialLogin: social, SplashScreen: splash,
        LocalNotifications: locals } = plugins;

/** Un golpecito al confirmar algo. Sin plugin, no pasa nada. */
export const haptic = (style = 'LIGHT') => {
  if (!isNative || !haptics) return;
  haptics.impact({ style }).catch(() => {});
};

const initSocialLogin = () => {
  const boot = document.querySelector('script[data-google-client-id]');
  const googleId = boot?.dataset.googleClientId || '';
  const appleId = boot?.dataset.appleClientId || '';
  if (social?.initialize) {
    social.initialize({
      google: googleId ? { webClientId: googleId } : undefined,
      apple: appleId ? { clientId: appleId } : undefined,
    }).catch(() => {});
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
};

const initOfflineCache = () => {
  if (!prefs) return;
  // Caché de la última semana vista: útil en el súper sin red.
  const cacheWeek = async () => {
    const main = document.querySelector('main');
    if (!main || !document.querySelector('nav.days')) return;
    await prefs.set({ key: 'week_html', value: main.innerHTML });
    await prefs.set({ key: 'week_at', value: String(Date.now()) });
  };
  document.addEventListener('htmx:afterSettle', () => { cacheWeek(); });
  cacheWeek();

  window.addEventListener('offline', async () => {
    const { value } = await prefs.get({ key: 'week_html' });
    const main = document.querySelector('main');
    if (!value || !main) return;
    main.innerHTML = value;
    const banner = document.createElement('p');
    banner.className = 'alert';
    banner.textContent = 'Sin conexión: estás viendo tu último menú guardado.';
    main.prepend(banner);
  });
};

const initShare = () => {
  for (const shareBtn of document.querySelectorAll('[data-share-day]')) {
    shareBtn.hidden = false;
  }
  document.addEventListener('click', async (event) => {
    const btn = event.target.closest('[data-share-day]');
    if (!btn || !sharePlugin) return;
    event.preventDefault();
    const title = document.querySelector('.day-macros')?.textContent
      || document.querySelector('.day-title')?.textContent
      || 'Mi menú NutriPlan';
    try {
      await sharePlugin.share({
        title: 'NutriPlan',
        text: `Mi día: ${title.trim()}`,
        dialogTitle: 'Compartir menú',
      });
    } catch { /* cancelado */ }
  });
};

/** launchAutoHide=false: si nadie lo oculta, la app se queda congelada en el
    logo. Se espera al primer pintado y no al HTML parseado, porque entre una
    cosa y otra hay un segundo de negro; el plazo es el seguro por si una imagen
    nunca llega. */
const hideSplash = () => {
  let hidden = false;
  const hide = () => {
    if (hidden) return;
    hidden = true;
    splash?.hide?.({ fadeOutDuration: 220 }).catch(() => {});
  };
  const painted = () => requestAnimationFrame(() => requestAnimationFrame(hide));
  if (document.readyState === 'complete') painted();
  else window.addEventListener('load', painted, { once: true });
  setTimeout(hide, 4000);
};

export const initIap = () => {
  document.querySelectorAll('[data-iap]').forEach((btn) => {
    btn.addEventListener('click', async (event) => {
      const form = btn.closest('form');
      const txn = form?.querySelector('[data-iap-txn]');
      if (!form || !txn) return;
      const productId = btn.dataset.iap;
      const iap = plugins.InAppPurchases || plugins.Purchases;
      if (isNative && iap?.purchaseProduct) {
        event.preventDefault();
        try {
          const result = await iap.purchaseProduct({ productIdentifier: productId, productId });
          txn.value = result.transactionId
            || result.transactionIdentifier
            || result.transaction?.transactionId
            || '';
          if (!txn.value) return;
          form.submit();
        } catch {
          // Cancelar la hoja de Apple no es un error que mostrar.
        }
        return;
      }
      if (!txn.value) txn.value = `local-${Date.now()}`;
    });
  });
};

export const initNative = () => {
  if (!isNative) return;
  document.documentElement.dataset.native = 'true';
  hideSplash();
  initSocialLogin();
  initOfflineCache();
  initShare();
  if (document.body?.dataset?.loggedIn === '1') initReminders(locals);
  // Háptico al completar generación (HX-Redirect = menú listo).
  document.addEventListener('htmx:afterRequest', (event) => {
    if (event.detail?.xhr?.getResponseHeader('HX-Redirect')) haptic('MEDIUM');
  });
};
