/* El onboarding: una pregunta por pantalla.

   Es un solo formulario que se envía una vez al final; lo que cambia aquí es
   cuál de sus secciones se ve. Así el servidor sigue recibiendo el mismo POST
   de siempre y nadie pierde lo que ya escribió al retroceder. */

import { haptic } from './native.js';

export const initStepper = () => {
  const stepper = document.querySelector('[data-stepper]');
  if (!stepper) return;

  const steps = [...stepper.querySelectorAll('.onb-step')];
  const next = stepper.querySelector('[data-next]');
  const submit = stepper.querySelector('[data-submit]');
  // La flecha de volver y la barra de progreso viven en la cabecera de la app,
  // fuera del formulario: se buscan en el documento entero.
  const back = document.querySelector('[data-back]');
  const fill = document.querySelector('[data-progress]');
  const count = document.querySelector('[data-count]');
  fill.dataset.of = String(steps.length);
  let at = 0;

  /** Un paso está listo cuando sus campos obligatorios tienen algo dentro. */
  const completo = () => [...steps[at].querySelectorAll('input, select, textarea')]
    .filter((f) => f.required)
    .every((f) => (f.type === 'radio'
      ? stepper.querySelector(`input[name="${f.name}"]:checked`)
      : f.value.trim()));

  const refrescaCta = () => {
    const listo = completo();
    if (next) next.disabled = !listo;
    if (submit) submit.disabled = !listo;
  };

  const show = ({ mover = true } = {}) => {
    steps.forEach((step, i) => { step.hidden = i !== at; });
    back.hidden = at === 0;
    next.hidden = at === steps.length - 1;
    submit.hidden = at !== steps.length - 1;
    fill.dataset.at = String(at + 1);
    count.textContent = `Paso ${at + 1} de ${steps.length}`;
    refrescaCta();
    const title = steps[at].querySelector('h2');
    if (title) { title.setAttribute('tabindex', '-1'); title.focus({ preventScroll: true }); }
    if (mover) window.scrollTo(0, 0);
  };

  const valid = () => [...steps[at].querySelectorAll('input, select, textarea')]
    .every((field) => field.reportValidity());

  const avanza = () => {
    if (!valid()) return;
    at = Math.min(at + 1, steps.length - 1);
    haptic();
    show();
  };

  next.addEventListener('click', avanza);
  back.addEventListener('click', () => { at = Math.max(at - 1, 0); haptic(); show(); });
  stepper.addEventListener('input', refrescaCta);
  stepper.addEventListener('change', () => {
    const ask = stepper.querySelector('[name=conoce_macros]:checked');
    const fields = stepper.querySelector('[data-macros-fields]');
    if (fields) {
      const on = ask?.value === 'si';
      fields.hidden = !on;
      fields.querySelectorAll('input').forEach((i) => { i.required = on; });
    }
    refrescaCta();
  });
  stepper.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target.tagName === 'INPUT' && at < steps.length - 1) {
      event.preventDefault();
      avanza();
    }
  });
  show({ mover: false });
  stepper.dispatchEvent(new Event('change'));
};
