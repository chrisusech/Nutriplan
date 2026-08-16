/* Checks de la compra: se recuerdan en este aparato, por semana. */

const clave = (week) => `nutriplan.compra.${week}`;

const leidos = (week) => {
  try {
    const raw = localStorage.getItem(clave(week));
    const data = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(data) ? data : []);
  } catch {
    return new Set();
  }
};

const guarda = (week, ids) => {
  try {
    localStorage.setItem(clave(week), JSON.stringify([...ids]));
  } catch { /* modo privado */ }
};

export const initShopping = () => {
  const intro = document.querySelector('[data-shop-week]');
  if (!intro || intro.dataset.bound === '1') return;
  intro.dataset.bound = '1';
  const week = intro.dataset.shopWeek;
  if (!week) return;
  const marked = leidos(week);
  for (const box of document.querySelectorAll('[data-shop-id]')) {
    box.checked = marked.has(box.dataset.shopId);
    box.addEventListener('change', () => {
      if (box.checked) marked.add(box.dataset.shopId);
      else marked.delete(box.dataset.shopId);
      guarda(week, marked);
    });
  }
};
