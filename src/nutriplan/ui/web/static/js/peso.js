/* Alterna gramos crudo/cocido. Las dos medidas ya vienen en el HTML. */

const CLAVE = 'nutriplan.peso';
const CRUDO = 'crudo';
const COCIDO = 'cocido';

const leido = () => {
  try {
    return localStorage.getItem(CLAVE) === CRUDO ? CRUDO : COCIDO;
  } catch {
    return COCIDO;
  }
};

const guarda = (modo) => {
  try {
    localStorage.setItem(CLAVE, modo);
  } catch { /* modo privado */ }
};

const pinta = (modo) => {
  for (const lista of document.querySelectorAll('[data-peso]')) {
    lista.dataset.peso = modo;
    for (const qty of lista.querySelectorAll('[data-qty-cocido]')) {
      const texto = modo === CRUDO ? qty.dataset.qtyCrudo : qty.dataset.qtyCocido;
      if (texto) qty.textContent = texto;
    }
  }
  for (const boton of document.querySelectorAll('[data-peso-toggle]')) {
    boton.setAttribute('aria-pressed', modo === CRUDO ? 'true' : 'false');
    boton.textContent = modo === CRUDO ? 'Ver ya cocido' : 'Ver en crudo';
  }
};

export const initPeso = () => {
  const botones = document.querySelectorAll('[data-peso-toggle]');
  if (!botones.length) return;
  pinta(leido());
  for (const boton of botones) {
    if (boton.dataset.bound === '1') continue;
    boton.dataset.bound = '1';
    boton.addEventListener('click', () => {
      const modo = leido() === CRUDO ? COCIDO : CRUDO;
      guarda(modo);
      pinta(modo);
    });
  }
};
