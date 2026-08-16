/* El puente con Capacitor: lo que solo existe cuando la app corre en un móvil.
   En el navegador todo esto se queda callado y la web funciona igual. */

import { initReminders } from './reminders.js';

const cap = window.Capacitor;
export const isNative = cap?.isNativePlatform?.() ?? false;

const plugins = cap?.Plugins ?? {};
const { Preferences: prefs,
        SocialLogin: social, SplashScreen: splash,
        LocalNotifications: locals, Keyboard: keyboard } = plugins;

/** Apagado: un golpe en cada toque se sentía como un fallo, no como una app. */
export const haptic = (_style = 'LIGHT') => {};

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

/** Zoom a doble toque y teclado: que se sienta app, no Safari. */
const initViewport = () => {
  document.addEventListener('gesturestart', (event) => event.preventDefault());
  document.addEventListener('dblclick', (event) => {
    if (event.target.closest('input, textarea, select')) return;
    event.preventDefault();
  }, { capture: true });

  const applyKb = () => {
    const vv = window.visualViewport;
    if (!vv) return;
    const kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    document.documentElement.style.setProperty('--kb', `${kb}px`);
  };
  window.visualViewport?.addEventListener('resize', applyKb);
  window.visualViewport?.addEventListener('scroll', applyKb);
  applyKb();

  if (!isNative || !keyboard) return;
  keyboard.setResizeMode?.({ mode: 'native' }).catch(() => {});
  keyboard.setAccessoryBarVisible?.({ isVisible: false }).catch(() => {});
};

export const initIap = () => {
  const iap = plugins.NativePurchases;
  const jwsOf = (result) => {
    const txn = result?.transaction ?? result;
    return (txn?.jwsRepresentation || '').trim();
  };
  const postJws = (form, jws) => {
    const field = form?.querySelector('[data-iap-jws]');
    if (!form || !field || !jws) return;
    field.value = jws;
    form.submit();
  };

  const paintPrices = async () => {
    if (!isNative || !iap?.getProducts) return;
    const ids = [...document.querySelectorAll('[data-iap]')]
      .map((btn) => btn.dataset.iap)
      .filter(Boolean);
    if (!ids.length) return;
    try {
      const { products } = await iap.getProducts({
        productIdentifiers: ids,
        productType: 'subs',
      });
      (products || []).forEach((product) => {
        const el = document.querySelector(`[data-iap-price="${product.identifier}"]`);
        if (el && product.priceString) el.textContent = product.priceString;
      });
    } catch {
      // Sin StoreKit el copy de la ficha se queda.
    }
  };
  paintPrices();

  document.querySelectorAll('[data-iap]').forEach((btn) => {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async (event) => {
      event.preventDefault();
      if (!isNative || !iap?.purchaseProduct) return;
      const form = btn.closest('form');
      const productId = btn.dataset.iap;
      try {
        const result = await iap.purchaseProduct({
          productIdentifier: productId,
          productType: 'subs',
        });
        postJws(form, jwsOf(result));
      } catch {
        // Cancelar la hoja de Apple no es un error que mostrar.
      }
    });
  });

  const restoreBtn = document.querySelector('[data-iap-restore]');
  if (restoreBtn?.dataset.bound === '1') return;
  if (restoreBtn) restoreBtn.dataset.bound = '1';
  restoreBtn?.addEventListener('click', async () => {
    if (!isNative || !iap?.restorePurchases) return;
    const form = restoreBtn.closest('form');
    try {
      await iap.restorePurchases();
      const listed = iap.getPurchases
        ? await iap.getPurchases({ onlyCurrentEntitlements: true })
        : {};
      const purchases = listed.purchases || listed.transactions || [];
      const jws = purchases.map(jwsOf).find(Boolean) || '';
      postJws(form, jws);
    } catch {
      // Sin compras que restaurar no hay nada que mostrar.
    }
  });
};

export const initNative = () => {
  initViewport();
  if (!isNative) return;
  document.documentElement.dataset.native = 'true';
  hideSplash();
  initSocialLogin();
  initOfflineCache();
  if (document.body?.dataset?.loggedIn === '1') initReminders(locals);
  // Háptico al completar generación (HX-Redirect = menú listo).
  document.addEventListener('htmx:afterRequest', (event) => {
    if (event.detail?.xhr?.getResponseHeader('HX-Redirect')) haptic('MEDIUM');
  });
};
