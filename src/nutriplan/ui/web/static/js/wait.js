/* Pantalla de espera con el plato girando: check-in y cambiar plato.

   El POST es síncrono y tarda. Sin esto el botón parece muerto. */

export const initWait = () => {
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!form?.matches?.('[data-wait-plate]')) return;
    const overlay = document.getElementById('wait-plate');
    if (!overlay) return;
    const title = overlay.querySelector('[data-wait-title]');
    const msg = overlay.querySelector('[data-wait-msg]');
    if (title) title.textContent = form.dataset.waitTitle || 'Un momento…';
    if (msg) msg.textContent = form.dataset.waitMsg || '';
    overlay.hidden = false;
  });
};
