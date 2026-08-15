/* Punto de entrada del cliente.

   Un módulo por concepto y este archivo solo los enciende. Vive en archivos y
   no en un <script> inline para que la CSP pueda prohibir el JavaScript
   incrustado sin excepciones. */

import { initNative, initIap, haptic } from './native.js';
import { initTheme } from './theme.js';
import { initSheets } from './sheet.js';
import { initWheels } from './wheel.js';
import { initStepper } from './stepper.js';
import { initRecipes } from './recipes.js';
import { initMacros } from './macros.js';
import { initRate } from './rate.js';
import { initWait } from './wait.js';

// Al reemplazar un bloque grande, el documento se encoge, el navegador recorta
// el scroll y al reinsertar ya no vuelve: calificar un plato te mandaba al
// fondo de la página.
let savedY;
document.addEventListener('htmx:beforeSwap', (event) => {
  const target = event.detail?.target;
  if (target?.matches?.('[data-recipe-url], .meal-check')) return;
  savedY = window.scrollY;
});
document.addEventListener('htmx:afterSettle', (event) => {
  const target = event.detail?.target;
  if (target?.matches?.('[data-recipe-url], .meal-check')) return;
  if (savedY !== undefined) window.scrollTo(0, savedY);
});

// Un golpecito en lo que se toca a diario: cambiar de día y calificar un plato.
document.addEventListener('click', (event) => {
  if (event.target.closest('.day, .star, .opt, .nav a, .meal-check button')) haptic();
});

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
