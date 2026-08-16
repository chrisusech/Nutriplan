/* Pantalla de espera con el plato girando: check-in y cambiar plato.

   El POST es síncrono y tarda. Sin esto el botón parece muerto. */

const overlayOf = () => document.getElementById('wait-plate');

const hideWait = () => {
  const overlay = overlayOf();
  if (overlay) overlay.hidden = true;
};

export const initWait = () => {
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form?.matches?.('[data-wait-plate]')) return;
    const overlay = overlayOf();
    if (!overlay) return;
    const title = overlay.querySelector('[data-wait-title]');
    const msg = overlay.querySelector('[data-wait-msg]');
    if (title) title.textContent = form.dataset.waitTitle || 'Un momento…';
    if (msg) msg.textContent = form.dataset.waitMsg || '';
    overlay.hidden = false;
  });
  window.addEventListener('pageshow', hideWait);
  document.body.addEventListener('htmx:afterSwap', hideWait);
  document.body.addEventListener('htmx:sendError', hideWait);
  document.body.addEventListener('htmx:responseError', hideWait);
};
