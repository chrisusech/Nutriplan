/* Las recetas del día llegan una a una, al abrir el plato.

   Los ingredientes ya están en la tarjeta: este hueco solo pinta los pasos.
   No se cubre el plato. Si Gemini no responde, el servidor cae a YAML. */

const ESPERA_MAX_MS = 120000;

const pendientes = new WeakMap();

const MACRO_DS = { protein_g: 'proteinG', carb_g: 'carbG', fat_g: 'fatG' };

const cargando = (estado) => {
  if (estado === 'fail') {
    return (
      '<div class="recipe-loading recipe-loading-fail" role="status">'
      + '<span class="icon">error</span>'
      + '<div class="recipe-loading-copy">'
      + '<span class="recipe-loading-title">No fue posible cargar la receta</span>'
      + '<span class="recipe-loading-sub">Puede reintentar cuando quiera.</span>'
      + '<button type="button" class="btn btn-ghost recipe-retry" data-recipe-retry>'
      + 'Reintentar</button>'
      + '</div></div>'
    );
  }
  return (
    '<div class="recipe-loading is-active" role="status" aria-live="polite">'
    + '<span class="icon" aria-hidden="true">autorenew</span>'
    + '<div class="recipe-loading-copy">'
    + '<span class="recipe-loading-title">Estamos generando tu receta…</span>'
    + '<span class="recipe-loading-sub">Pasos, nombre del plato y tips. Suele tardar unos segundos.</span>'
    + '</div></div>'
  );
};

const abortIfLoading = (slot) => {
  if (!slot || slot.querySelector('.steps') || !window.htmx) return;
  window.htmx.trigger(slot, 'htmx:abort');
  const limpia = pendientes.get(slot);
  if (limpia) limpia();
  pendientes.delete(slot);
  delete slot.dataset.loaded;
  delete slot.dataset.loading;
};

const alInicio = (meal) => {
  requestAnimationFrame(() => {
    meal?.scrollIntoView({ block: 'start', behavior: 'instant' });
  });
};

const cargaSlot = (slot, { force = false } = {}) => {
  if (!slot || !window.htmx || !slot.dataset.recipeUrl) return Promise.resolve();
  if (!force && slot.dataset.loaded === '1') return Promise.resolve();
  if (!force && slot.querySelector('.steps')) {
    slot.dataset.loaded = '1';
    return Promise.resolve();
  }
  if (!force && slot.dataset.loading === '1') return Promise.resolve();
  abortIfLoading(slot);
  slot.dataset.loading = '1';
  delete slot.dataset.loaded;
  slot.innerHTML = cargando('active');
  return new Promise((resolve) => {
    const onSwap = (event) => {
      if (event.detail?.target !== slot) return;
      slot.dataset.loaded = '1';
      delete slot.dataset.loading;
      limpia();
      resolve();
    };
    const onFail = (event) => {
      if (event.detail?.target !== slot && event.detail?.elt !== slot) return;
      if (!event.detail?.failed) return;
      falla();
    };
    const onAbort = (event) => {
      if (event.detail?.target !== slot && event.detail?.elt !== slot) return;
      delete slot.dataset.loaded;
      delete slot.dataset.loading;
      limpia();
      resolve();
    };
    const falla = () => {
      limpia();
      delete slot.dataset.loaded;
      delete slot.dataset.loading;
      if (slot.isConnected) slot.innerHTML = cargando('fail');
      resolve();
    };
    const limpia = () => {
      clearTimeout(reloj);
      pendientes.delete(slot);
      document.body.removeEventListener('htmx:afterSwap', onSwap);
      document.body.removeEventListener('htmx:afterRequest', onFail);
      document.body.removeEventListener('htmx:abort', onAbort);
      document.body.removeEventListener('htmx:responseError', onAbort);
    };
    const reloj = setTimeout(falla, ESPERA_MAX_MS);
    pendientes.set(slot, limpia);
    document.body.addEventListener('htmx:afterSwap', onSwap);
    document.body.addEventListener('htmx:afterRequest', onFail);
    document.body.addEventListener('htmx:abort', onAbort);
    document.body.addEventListener('htmx:responseError', onAbort);
    window.htmx.ajax('GET', slot.dataset.recipeUrl, {
      target: slot,
      swap: 'innerHTML show:none',
      source: slot,
    });
  });
};

const cargaSiAbierta = (meal, opts) => (
  cargaSlot(meal?.querySelector?.('[data-recipe-url]'), opts)
);

const attrOf = (form, kebab, camel) => (
  form.dataset[camel] ?? form.getAttribute(`data-${kebab}`)
);

const pintaHero = (form) => {
  const hero = document.getElementById('day-hero');
  if (!hero || form.dataset.pct == null) return;
  const ring = hero.querySelector('.ring');
  if (ring) {
    ring.dataset.pct = form.dataset.pct;
    ring.dataset.estado = form.dataset.estado || 'ok';
  }
  const value = hero.querySelector('.ring-value');
  if (value && form.dataset.kcal != null) value.textContent = form.dataset.kcal;
  const copy = hero.querySelector('.day-hero-text .muted');
  if (copy && form.dataset.eatenN != null) {
    copy.textContent = `${form.dataset.eatenN} de ${form.dataset.mealsN} comidas · Quedan ${form.dataset.restante} kcal`;
  }
  for (const [key, camel] of Object.entries(MACRO_DS)) {
    const bar = hero.querySelector(`.bar-${key}`);
    const n = attrOf(form, key.replace('_', '-'), camel);
    if (!bar || n == null) continue;
    bar.value = n;
    const bold = bar.closest('.macro')?.querySelector('.macro-val b');
    if (bold) bold.textContent = n;
  }
  const racha = hero.querySelector('[data-racha]');
  if (racha && form.dataset.racha != null) {
    const n = Number(form.dataset.racha);
    racha.hidden = n < 1;
    racha.textContent = n === 1 ? 'Racha: 1 día' : `Racha: ${n} días`;
  }
  const eatenN = Number(form.dataset.eatenN);
  const mealsN = Number(form.dataset.mealsN);
  if (eatenN > 0 && eatenN === mealsN) celebra(form.dataset.racha);
};

const celebra = (racha) => {
  const key = `day-done-${new Date().toDateString()}`;
  try {
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, '1');
  } catch { /* modo privado */ }
  const overlay = document.getElementById('day-done');
  if (!overlay) return;
  const n = Number(racha || 1);
  const num = overlay.querySelector('[data-racha-n]');
  const plural = overlay.querySelector('[data-racha-s]');
  if (num) num.textContent = String(n);
  if (plural) plural.hidden = n === 1;
  overlay.hidden = false;
  overlay.addEventListener('click', () => { overlay.hidden = true; }, { once: true });
};

const abre = (meal) => {
  cargaSiAbierta(meal).then(() => alInicio(meal));
};

export const initRecipes = () => {
  document.addEventListener('toggle', (event) => {
    const meal = event.target;
    if (!meal.matches?.('details.meal')) return;
    if (!meal.open) {
      abortIfLoading(meal.querySelector('[data-recipe-url]'));
      return;
    }
    for (const other of document.querySelectorAll('details.meal[open]')) {
      if (other !== meal) {
        other.open = false;
        abortIfLoading(other.querySelector('[data-recipe-url]'));
      }
    }
    abre(meal);
  }, true);

  document.body.addEventListener('click', (event) => {
    const retry = event.target.closest('[data-recipe-retry]');
    if (!retry) return;
    const meal = retry.closest('details.meal');
    const slot = meal?.querySelector('[data-recipe-url]');
    if (slot) {
      delete slot.dataset.loaded;
      delete slot.dataset.loading;
    }
    cargaSiAbierta(meal, { force: true }).then(() => alInicio(meal));
  });

  document.body.addEventListener('htmx:afterSwap', (event) => {
    const target = event.detail?.target;
    const block = target?.matches?.('[data-culinary-title]')
      ? target
      : target?.querySelector?.('[data-culinary-title]');
    const title = block?.getAttribute?.('data-culinary-title');
    if (!title) return;
    const name = block.closest('details.meal')?.querySelector('.meal-name');
    if (name) name.textContent = title;
  });

  document.body.addEventListener('htmx:afterSettle', (event) => {
    const target = event.detail?.target;
    if (target?.id === 'shell') {
      for (const meal of document.querySelectorAll('details.meal[open]')) {
        abre(meal);
      }
      return;
    }
    const form = target?.matches?.('.meal-check')
      ? target
      : event.detail?.elt?.closest?.('.meal-check');
    if (form) {
      form.closest('details.meal')?.classList.toggle('eaten', !!form.querySelector('.btn.on'));
      pintaHero(form);
    }
  });

  for (const meal of document.querySelectorAll('details.meal[open]')) {
    abre(meal);
  }
};
