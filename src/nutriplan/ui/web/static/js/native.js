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
  const iap = plugins.NativePurchases
    || (typeof cap?.registerPlugin === 'function' ? cap.registerPlugin('NativePurchases') : null);
  const jwsOf = (result) => {
    const raw = result?.jwsRepresentation
      || result?.transaction?.jwsRepresentation
      || result?.purchase?.jwsRepresentation
      || '';
    return String(raw).trim();
  };
  const purchasesOf = (listed) => {
    if (!listed) return [];
    if (Array.isArray(listed)) return listed;
    return listed.purchases || listed.transactions || listed.results || [];
  };
  const postJws = (form, jws) => {
    const field = form?.querySelector('[data-iap-jws]');
    if (!form || !field || !jws) return false;
    field.value = jws;
    form.submit();
    return true;
  };
  const showError = (text) => {
    const box = document.querySelector('[data-iap-error]');
    if (!box) return;
    box.hidden = false;
    box.textContent = text;
  };
  const cancelled = (err) => {
    const code = String(err?.code || err?.errorCode || '');
    const msg = String(err?.message || err || '').toLowerCase();
    return /cancel|cancelled|canceled|user.?denied/i.test(`${code} ${msg}`);
  };

  const paintPrices = async () => {
    const nodes = [...document.querySelectorAll('[data-iap-price]')];
    if (!nodes.length) return;
    const fallback = (el) => el.getAttribute('data-iap-fallback') || el.textContent;
    if (!isNative || !iap?.getProducts) return;
    try {
      const ids = [...document.querySelectorAll('[data-iap]')]
        .map((btn) => btn.dataset.iap)
        .filter(Boolean);
      const { products } = await iap.getProducts({
        productIdentifiers: ids,
        productType: 'subs',
      });
      const byId = Object.fromEntries(
        (products || []).map((product) => [product.identifier, product]),
      );
      nodes.forEach((el) => {
        const product = byId[el.getAttribute('data-iap-price') || ''];
        el.textContent = product?.priceString || fallback(el);
      });
    } catch {
      nodes.forEach((el) => { el.textContent = fallback(el); });
    }
  };
  paintPrices();

  document.querySelectorAll('[data-iap]').forEach((btn) => {
    if (btn.dataset.bound === '1') return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', async () => {
      const form = btn.closest('form');
      const productId = btn.dataset.iap;
      if (!isNative || !iap?.purchaseProduct) {
        showError('El cobro se abre en el iPhone, con Apple.');
        return;
      }
      btn.disabled = true;
      try {
        const result = await iap.purchaseProduct({
          productIdentifier: productId,
          productType: 'subs',
          quantity: 1,
        });
        if (!postJws(form, jwsOf(result))) {
          showError('El pago se confirmó, pero no se pudo activar. Prueba Restaurar compras.');
        }
      } catch (err) {
        if (!cancelled(err)) {
          const detail = String(err?.message || err?.code || '').trim();
          showError(detail
            ? `No se pudo abrir el pago. ${detail}`
            : 'No se pudo abrir el pago. Vuelve a intentarlo.');
        }
      } finally {
        btn.disabled = false;
      }
    });
  });

  const restoreBtn = document.querySelector('[data-iap-restore]');
  if (restoreBtn?.dataset.bound === '1') return;
  if (restoreBtn) restoreBtn.dataset.bound = '1';
  restoreBtn?.addEventListener('click', async () => {
    if (!isNative || !iap?.restorePurchases) {
      showError('Restaurar compras solo funciona en el iPhone.');
      return;
    }
    restoreBtn.disabled = true;
    try {
      const restored = await iap.restorePurchases();
      const listed = iap.getPurchases
        ? await iap.getPurchases({ onlyCurrentEntitlements: true })
        : restored;
      const jws = purchasesOf(listed).map(jwsOf).find(Boolean)
        || jwsOf(restored)
        || '';
      const form = restoreBtn.closest('form');
      if (!postJws(form, jws)) {
        showError('No hay una compra de Apple para restaurar en este iPhone.');
      }
    } catch (err) {
      if (!cancelled(err)) {
        showError('No se pudo restaurar la compra. Vuelve a intentarlo.');
      }
    } finally {
      restoreBtn.disabled = false;
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
