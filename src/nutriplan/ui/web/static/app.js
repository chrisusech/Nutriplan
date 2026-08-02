/* Lo mínimo que la app necesita en el cliente.
   Vive en un archivo y no en un <script> inline para que la CSP pueda
   prohibir el JavaScript incrustado sin excepciones. */

(() => {
  'use strict';

  // El color de marca llega como atributo del <body>: es el único estilo que
  // depende del servidor, y así no hace falta un <style> inline.
  const body = document.body;
  if (body.dataset.brand) {
    document.documentElement.style.setProperty('--brand', body.dataset.brand);
    document.documentElement.style.setProperty('--brand-soft', body.dataset.brandSoft);
  }

  // Al reemplazar un bloque grande, el documento se encoge, el navegador
  // recorta el scroll y al reinsertar ya no vuelve: calificar un plato te
  // mandaba al fondo de la página.
  let savedY;
  document.addEventListener('htmx:beforeSwap', () => { savedY = window.scrollY; });
  document.addEventListener('htmx:afterSettle', () => {
    if (savedY !== undefined) window.scrollTo(0, savedY);
  });

  // Un solo plato abierto a la vez: en un móvil, dos acordeones abiertos
  // convierten la lista en un scroll interminable.
  document.addEventListener('toggle', (event) => {
    const meal = event.target;
    if (!meal.matches('details.meal') || !meal.open) return;
    for (const other of document.querySelectorAll('details.meal[open]')) {
      if (other !== meal) other.open = false;
    }
  }, true);
})();
