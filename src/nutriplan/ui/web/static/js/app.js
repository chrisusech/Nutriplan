/* Punto de entrada del cliente.

   Un módulo por concepto y este archivo solo los enciende. Vive en archivos y
   no en un <script> inline para que la CSP pueda prohibir el JavaScript
   incrustado sin excepciones. */

import { initNative, initIap } from './native.js';
import { initTheme } from './theme.js';
import { initSheets, initAutoSheets } from './sheet.js';
import { initWheels } from './wheel.js';
import { initStepper } from './stepper.js';
import { initRecipes } from './recipes.js';
import { initMacros } from './macros.js';
import { initRate } from './rate.js';
import { initWait } from './wait.js';
import { initShopping } from './shopping.js';
import { initPeso } from './peso.js';

// Al reemplazar un bloque grande, el documento se encoge, el navegador recorta
// el scroll y al reinsertar ya no vuelve: calificar un plato te mandaba al
// fondo de la página.
let savedY;
document.addEventListener('htmx:beforeSwap', (event) => {
  const target = event.detail?.target;
  if (target?.matches?.('[data-recipe-url], .meal-check')) return;
  if (target?.id === 'shell') return;
  savedY = window.scrollY;
});
document.addEventListener('htmx:afterSettle', (event) => {
  const target = event.detail?.target;
  if (target?.matches?.('[data-recipe-url], .meal-check')) return;
  if (target?.id === 'shell') {
    const page = target.dataset.pageClass;
    if (page !== undefined) document.body.className = page;
    bootPage();
    return;
  }
  if (savedY !== undefined) window.scrollTo(0, savedY);
});

const bootPage = () => {
  initWheels();
  initStepper();
  initMacros();
  initShopping();
  initPeso();
  initIap();
  initAutoSheets();
};

initNative();
initIap();
initTheme();
initSheets();
initWheels();
initStepper();
initRecipes();
initMacros();
initRate();
initWait();
initShopping();
initPeso();
