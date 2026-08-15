/* El interruptor de tema.

   Quien decide el color de la página es el servidor, que lee la cookie y pinta
   `data-theme` en el <html>. Aquí solo se cambia el atributo para que el cambio
   se vea en el acto, y se deja la cookie escrita para la siguiente carga. */

import { haptic } from './native.js';

const UN_ANO = 60 * 60 * 24 * 365;

const aplicar = (tema) => {
  document.documentElement.dataset.theme = tema;
  document.cookie = `tema=${tema}; path=/; max-age=${UN_ANO}; samesite=lax`;
  // La barra de estado del teléfono va aparte del CSS: si no se actualiza, en
  // la app nativa queda una franja del color del tema anterior.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = tema === 'dark' ? '#131010' : '#F4EBE5';
  for (const btn of document.querySelectorAll('[data-theme-toggle]')) {
    btn.setAttribute('aria-pressed', String(tema === 'dark'));
  }
};

export const initTheme = () => {
  document.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-theme-toggle]');
    if (!btn) return;
    event.preventDefault();
    aplicar(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
    haptic();
  });
};
