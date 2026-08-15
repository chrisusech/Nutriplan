/* Las recetas del día llegan una a una.

   El cliente pide /menu/receta por cada plato (no un batch largo: Gemini y HTMX
   cortaban la petición única y solo quedaban 1–2 recetas). De a dos, para no
   tumbar la cuota. */

const CONCURRENCIA = 2;

/* Cuánto se espera una receta antes de darla por perdida. El servidor se corta
   solo antes de esto; el techo existe porque una petición ABORTADA no dispara
   ningún evento de htmx: si la persona cambia de día a media carga, el hueco de
   la receta se reemplaza, la promesa no se resolvía nunca y el bucle de abajo
   se quedaba parado con las comidas restantes en «En cola…» para siempre. */
const ESPERA_MAX_MS = 120000;

const cargando = (estado) => {
  if (estado === 'fail') {
    return (
      '<div class="recipe-loading recipe-loading-fail" role="status">'
      + '<span class="icon">error</span>'
      + '<div class="recipe-loading-copy">'
      + '<span class="recipe-loading-title">No se pudo generar la receta</span>'
      + '<span class="recipe-loading-sub">Hubo un problema de cuota o red. Puedes reintentar.</span>'
      + '<button type="button" class="btn btn-ghost recipe-retry" data-recipe-retry>'
      + 'Reintentar</button>'
      + '</div></div>'
    );
  }
  if (estado === 'queued') {
    return (
      '<div class="recipe-loading is-queued" role="status" aria-live="polite">'
      + '<span class="icon" aria-hidden="true">schedule</span>'
      + '<div class="recipe-loading-copy">'
      + '<span class="recipe-loading-title">En cola</span>'
      + '<span class="recipe-loading-sub">Generaremos tu receta en un momento…</span>'
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

const cargaSlot = (slot, { force = false } = {}) => {
  if (!slot || !window.htmx || !slot.dataset.recipeUrl) return Promise.resolve();
  if (!force && slot.dataset.loaded === '1') return Promise.resolve();
  if (!force && slot.querySelector('.steps')) {
    slot.dataset.loaded = '1';
    return Promise.resolve();
  }
  slot.dataset.loaded = '1';
  slot.innerHTML = cargando('active');
  return new Promise((resolve) => {
    const onSwap = (event) => {
      if (event.detail?.target !== slot) return;
      limpia();
      resolve();
    };
    const onFail = (event) => {
      if (event.detail?.target !== slot && event.detail?.elt !== slot) return;
      if (!event.detail?.failed) return;
      falla();
    };
    // Abortada: ni swap ni afterRequest. Sin esto la promesa quedaba colgada.
    const onAbort = (event) => {
      if (event.detail?.target !== slot && event.detail?.elt !== slot) return;
      falla();
    };
    const falla = () => {
      limpia();
      // Si el hueco ya no está en la página, pintar en él no lo ve nadie.
      if (slot.isConnected) slot.innerHTML = cargando('fail');
      resolve();
    };
    const limpia = () => {
      clearTimeout(reloj);
      document.body.removeEventListener('htmx:afterSwap', onSwap);
      document.body.removeEventListener('htmx:afterRequest', onFail);
      document.body.removeEventListener('htmx:abort', onAbort);
      document.body.removeEventListener('htmx:responseError', onAbort);
    };
    const reloj = setTimeout(falla, ESPERA_MAX_MS);
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

const cargaSiAbierta = (meal, opts) => {
  cargaSlot(meal?.querySelector?.('[data-recipe-url]'), opts);
};

/** Todas las comidas del día, de a pocas, para no tumbar la cuota ni HTMX. */
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
  for (const key of ['protein_g', 'carb_g', 'fat_g']) {
    const bar = hero.querySelector(`.bar-${key}`);
    const n = form.dataset[key];
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

const hidrataDia = async () => {
  const slots = [...document.querySelectorAll('[data-recipe-url]')].filter(
    (slot) => !slot.querySelector('.steps'),
  );
  if (!slots.length) return;
  slots.forEach((slot) => { slot.innerHTML = cargando('queued'); });
  let next = 0;
  const workers = Array.from(
    { length: Math.min(CONCURRENCIA, slots.length) },
    async () => {
      while (next < slots.length) {
        const i = next;
        next += 1;
        // El día pudo cambiar a media hidratación: esos huecos ya no existen.
        if (!slots[i].isConnected) continue;
        delete slots[i].dataset.loaded;
        await cargaSlot(slots[i], { force: true });
      }
    },
  );
  await Promise.all(workers);
};

export const initRecipes = () => {
  document.addEventListener('toggle', (event) => {
    const meal = event.target;
    if (!meal.matches?.('details.meal') || !meal.open) return;
    for (const other of document.querySelectorAll('details.meal[open]')) {
      if (other !== meal) other.open = false;
    }
    cargaSiAbierta(meal);
    meal.querySelector('summary')?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }, true);

  document.body.addEventListener('click', (event) => {
    const retry = event.target.closest('[data-recipe-retry]');
    if (!retry) return;
    const meal = retry.closest('details.meal');
    const slot = meal?.querySelector('[data-recipe-url]');
    if (slot) delete slot.dataset.loaded;
    cargaSiAbierta(meal, { force: true });
  });

  // El nombre culinario lo escribe la IA con la receta: hasta que llega, la
  // tarjeta muestra el nombre de plantilla.
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

  if (document.querySelector('[data-hydrate-day-recipes]')) hidrataDia();
  document.body.addEventListener('htmx:afterSettle', (event) => {
    const target = event.detail?.target;
    const form = target?.matches?.('.meal-check')
      ? target
      : event.detail?.elt?.closest?.('.meal-check');
    if (form) {
      form.closest('details.meal')?.classList.toggle('eaten', !!form.querySelector('.btn.on'));
      pintaHero(form);
      return;
    }
    if (target?.id === 'dia-track' || target?.querySelector?.('[data-hydrate-day-recipes]')) {
      hidrataDia();
    }
  });
};
