/* Hojas que suben desde abajo, sobre `<dialog>`.

   `showModal()` da gratis el velo, el foco atrapado y el cierre con Escape.
   Aquí solo se añade lo que el navegador no trae: la animación de salida y que
   tocar fuera cierre. */

import { haptic } from './native.js';

const abrir = (sheet) => {
  if (!sheet || sheet.open) return;
  sheet.showModal();
  sheet.dispatchEvent(new CustomEvent('sheet:open', { bubbles: true }));
  haptic();
};

export const cerrar = (sheet) => {
  if (!sheet?.open) return;
  // Sin esperar a la animación, la hoja desaparece de golpe y parece un error.
  sheet.classList.add('is-closing');
  let cerrada = false;
  const fin = () => {
    if (cerrada) return;
    cerrada = true;
    sheet.classList.remove('is-closing');
    sheet.close();
  };
  sheet.addEventListener('animationend', fin, { once: true });
  // Y si nadie animó nada (movimiento reducido, por ejemplo), no se queda
  // colgada esperando un evento que no va a llegar.
  setTimeout(fin, 400);
};

export const initSheets = () => {
  document.addEventListener('click', (event) => {
    const opener = event.target.closest('[data-sheet-open]');
    if (opener) {
      event.preventDefault();
      abrir(document.getElementById(opener.dataset.sheetOpen));
      return;
    }
    const closer = event.target.closest('[data-sheet-close]');
    if (closer) {
      event.preventDefault();
      cerrar(closer.closest('dialog'));
      return;
    }
    // Tocar el velo: el clic cae en el propio <dialog>, no en su panel.
    if (event.target.matches('dialog.sheet')) cerrar(event.target);
  });

  // Escape lo cierra el navegador de golpe; se intercepta para que salga
  // deslizando igual que cuando se toca fuera.
  document.addEventListener('cancel', (event) => {
    if (!event.target.matches?.('dialog.sheet')) return;
    event.preventDefault();
    cerrar(event.target);
  });

  // El servidor puede pedir que una hoja nazca abierta: es lo que pasa cuando
  // el login falla y hay que devolver el formulario con su error a la vista.
  for (const sheet of document.querySelectorAll('dialog.sheet[data-autoopen]')) abrir(sheet);
};
