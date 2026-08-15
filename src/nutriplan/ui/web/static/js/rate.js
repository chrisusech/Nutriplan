/* Un POST a la vez, sin recargar la página (HTMX cambia solo el formulario).

   En WebKit un `button[disabled]` no entra en el POST: copiamos name/value a
   un hidden en captura, antes de que htmx serialice. Desactivar lo hace
   `hx-disabled-elt` cuando hay HTMX; si no, lo hacemos al final. */

export const initRate = () => {
  document.addEventListener('submit', (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.classList.contains('rate')) return;
    if (form.dataset.enviando === '1') {
      event.preventDefault();
      return;
    }
    form.dataset.enviando = '1';
    const submitter = event.submitter;
    if (submitter instanceof HTMLButtonElement && submitter.name) {
      const hidden = document.createElement('input');
      hidden.type = 'hidden';
      hidden.name = submitter.name;
      hidden.value = submitter.value;
      form.appendChild(hidden);
    }
    if (!form.hasAttribute('hx-post')) {
      for (const btn of form.querySelectorAll('button')) btn.disabled = true;
    }
  }, true);
};
