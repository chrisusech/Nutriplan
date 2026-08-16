/* La rueda de valores de las hojas: edad, altura y peso.

   Teclear "72.4" en un móvil es abrir el teclado numérico, apuntar y corregir.
   Girar una rueda es un gesto. Los valores los escribe este archivo y no la
   plantilla: cambiar de kg a lbs re-genera la lista sin recargar nada. */

import { cerrar } from './sheet.js';
import { haptic } from './native.js';

const LB_POR_KG = 2.2046226;
const redondea = (n, dec = 1) => Number(n.toFixed(dec));

const valores = (wheel) => {
  const min = Number(wheel.dataset.min);
  const max = Number(wheel.dataset.max);
  const paso = Number(wheel.dataset.step || 1);
  const out = [];
  for (let v = min; v <= max + 1e-9; v += paso) out.push(redondea(v, 1));
  return out;
};

const pinta = (wheel, lista) => {
  const ul = document.createElement('ul');
  ul.className = 'wheel-list';
  for (const v of lista) {
    const li = document.createElement('li');
    li.className = 'wheel-item';
    li.dataset.value = String(v);
    li.textContent = String(v);
    ul.append(li);
  }
  wheel.replaceChildren(ul);
};

const items = (wheel) => [...wheel.querySelectorAll('.wheel-item')];

const alto = (wheel) => items(wheel)[0]?.offsetHeight || 44;

const indiceActual = (wheel) => Math.round(wheel.scrollTop / alto(wheel));

const marca = (wheel) => {
  const i = indiceActual(wheel);
  items(wheel).forEach((li, n) => li.classList.toggle('on', n === i));
};

const centra = (wheel, valor, { animado = false } = {}) => {
  const lista = items(wheel);
  const i = Math.max(0, lista.findIndex((li) => Number(li.dataset.value) === Number(valor)));
  wheel.scrollTo({ top: i * alto(wheel), behavior: animado ? 'smooth' : 'auto' });
  marca(wheel);
};

const leer = (wheel) => {
  const li = items(wheel)[indiceActual(wheel)];
  return li ? Number(li.dataset.value) : Number(wheel.dataset.value);
};

/** El valor de la hoja: entero, o entero + decimal cuando hay dos ruedas. */
const valorDe = (sheet) => {
  const ruedas = [...sheet.querySelectorAll('[data-wheel]')];
  const entero = leer(ruedas[0]);
  return ruedas.length > 1 ? redondea(entero + leer(ruedas[1]) / 10) : entero;
};

const unidadDe = (sheet) =>
  sheet.querySelector('[data-unit-seg] input:checked')?.value
  || sheet.dataset.unit
  || '';

const prepara = (wheel, { min, max, step, value }) => {
  wheel.dataset.min = String(min);
  wheel.dataset.max = String(max);
  wheel.dataset.step = String(step);
  pinta(wheel, valores(wheel));
  centra(wheel, value);
};

/** Cambiar de kg a lbs no cambia el peso: cambia con qué se mide. */
const cambiaUnidad = (sheet) => {
  const nueva = unidadDe(sheet);
  const previa = sheet.dataset.unidad || 'kg';
  if (nueva === previa) return;
  const ruedas = [...sheet.querySelectorAll('[data-wheel]')];
  const kg = previa === 'lbs' ? valorDe(sheet) / LB_POR_KG : valorDe(sheet);
  const mostrado = nueva === 'lbs' ? kg * LB_POR_KG : kg;
  // Los topes de la hoja están en kg; en libras se convierten, no se inventan.
  const min = Number(sheet.dataset.min);
  const max = Number(sheet.dataset.max);
  prepara(ruedas[0], {
    min: nueva === 'lbs' ? Math.round(min * LB_POR_KG) : min,
    max: nueva === 'lbs' ? Math.round(max * LB_POR_KG) : max,
    step: 1,
    value: Math.floor(mostrado),
  });
  if (ruedas[1]) {
    prepara(ruedas[1], { min: 0, max: 9, step: 1, value: Math.round((mostrado % 1) * 10) });
  }
  const etiqueta = sheet.querySelector('[data-unit-label]');
  if (etiqueta) etiqueta.textContent = nueva;
  sheet.dataset.unidad = nueva;
};

const acepta = (sheet) => {
  const campo = document.querySelector(`[name="${sheet.dataset.field}"]`);
  const salida = document.querySelector(`[data-display="${sheet.dataset.field}"]`);
  const unidad = unidadDe(sheet);
  const elegido = valorDe(sheet);
  // El formulario habla siempre en kg y cm: la unidad es cosa de quien mira.
  const guardado = unidad === 'lbs' ? redondea(elegido / LB_POR_KG) : elegido;
  if (campo) {
    campo.value = String(guardado);
    campo.dispatchEvent(new Event('change', { bubbles: true }));
  }
  if (salida) {
    salida.textContent = `${elegido} ${unidad || sheet.dataset.unit || ''}`.trim();
    salida.classList.remove('is-empty');
  }
  haptic('MEDIUM');
  cerrar(sheet);
};

export const initWheels = () => {
  for (const sheet of document.querySelectorAll('[data-wheel-sheet]')) {
    if (sheet.dataset.bound === '1') continue;
    sheet.dataset.bound = '1';
    const ruedas = [...sheet.querySelectorAll('[data-wheel]')];
    sheet.dataset.unidad = unidadDe(sheet) || 'kg';
    ruedas.forEach((wheel) => {
      pinta(wheel, valores(wheel));
      centra(wheel, wheel.dataset.value);
      const encaja = () => {
        const i = indiceActual(wheel);
        wheel.scrollTo({ top: i * alto(wheel), behavior: 'smooth' });
        marca(wheel);
      };
      let t;
      wheel.addEventListener('scroll', () => {
        clearTimeout(t);
        t = setTimeout(() => marca(wheel), 60);
      }, { passive: true });
      wheel.addEventListener('scrollend', encaja, { passive: true });
      wheel.addEventListener('click', (event) => {
        const li = event.target.closest('.wheel-item');
        if (!li) return;
        centra(wheel, li.dataset.value, { animado: true });
      });
    });

    sheet.addEventListener('sheet:open', () => {
      ruedas.forEach((wheel) => centra(wheel, wheel.dataset.value));
    });
    sheet.querySelector('[data-unit-seg]')?.addEventListener('change', () => {
      cambiaUnidad(sheet);
    });
    sheet.querySelector('[data-sheet-accept]')?.addEventListener('click', (event) => {
      event.preventDefault();
      acepta(sheet);
    });
  }
};
