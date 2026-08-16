/* Los cuatro números no pueden contradecirse.

   Las kcal son 4·P + 4·C + 9·G, así que solo tres son libres. Se escribe por
   donde se quiera y el cuarto se recoloca aquí mismo. En gramos, P/C/G mandan
   las kcal; al tocar kcal (o al usar g/kg) el carbohidrato cierra, igual que
   en la fórmula del dominio. Al guardar, el servidor vuelve a calcularlo. */

export const initMacros = () => {
  const macros = document.querySelector('[data-macros]');
  if (!macros || macros.dataset.bound === '1') return;
  macros.dataset.bound = '1';

  const kcalField = macros.querySelector('[data-kcal]');
  const named = (key) => macros.querySelector(`[data-macro="${key}"]`);
  const all = [...macros.querySelectorAll('[data-macro]')];
  const protein = named('protein') || all[0];
  const carb = named('carb') || all[1];
  const fat = named('fat') || all[2];
  if (!kcalField || !protein || !carb || !fat) return;

  const hint = macros.querySelector('[data-gkg]');
  const ppkField = macros.querySelector('[data-ppk]');
  const fpkField = macros.querySelector('[data-fpk]');
  const peso = Number(macros.dataset.peso) || 0;
  const g = (field) => Number(field?.value) || 0;

  const modo = () => (
    macros.querySelector('[data-macro-modo] input:checked')?.value || 'gramos'
  );

  const showPanels = () => {
    const formula = modo() === 'formula';
    macros.querySelectorAll('[data-panel-gramos]').forEach((el) => {
      el.hidden = formula;
    });
    macros.querySelectorAll('[data-panel-formula]').forEach((el) => {
      el.hidden = !formula;
    });
  };

  const setGramsLabels = () => {
    const pg = macros.querySelector('[data-ppk-g]');
    const fg = macros.querySelector('[data-fpk-g]');
    const cg = macros.querySelector('[data-carb-g]');
    if (pg) pg.textContent = String(Math.round(g(protein)));
    if (fg) fg.textContent = String(Math.round(g(fat)));
    if (cg) cg.textContent = String(Math.round(g(carb)));
  };

  const showHint = () => {
    setGramsLabels();
    if (!hint || !peso) return;
    const porKg = (grams) => (grams / peso).toFixed(2);
    hint.textContent = `Sobre ${peso} kg: ${porKg(g(protein))} g/kg de proteína `
      + `· ${porKg(g(fat))} g/kg de grasa.`;
  };

  const setKgFromGrams = () => {
    if (!peso) return;
    if (ppkField) ppkField.value = (g(protein) / peso).toFixed(1);
    if (fpkField) fpkField.value = (g(fat) / peso).toFixed(1);
  };

  const fromMacros = () => {
    kcalField.value = String(Math.round(4 * g(protein) + 4 * g(carb) + 9 * g(fat)));
    setKgFromGrams();
    showHint();
  };

  const fromKcal = () => {
    const rest = (Number(kcalField.value) - 4 * g(protein) - 9 * g(fat)) / 4;
    if (rest < 0) {
      carb.value = '0';
      kcalField.value = String(Math.round(4 * g(protein) + 9 * g(fat)));
    } else {
      carb.value = String(Math.round(rest));
    }
    showHint();
  };

  const fromFormula = () => {
    if (!peso || !ppkField || !fpkField) return;
    protein.value = String(Math.max(1, Math.round(g(ppkField) * peso)));
    fat.value = String(Math.max(1, Math.round(g(fpkField) * peso)));
    fromKcal();
  };

  const nudge = (field, step) => {
    const next = Math.round((g(field) + step) * 10) / 10;
    const min = Number(field.min);
    const max = Number(field.max);
    const lo = Number.isFinite(min) ? min : 0;
    const hi = Number.isFinite(max) ? max : next;
    field.value = String(Math.min(hi, Math.max(lo, next)));
    fromFormula();
  };

  protein.addEventListener('input', fromMacros);
  carb.addEventListener('input', fromMacros);
  fat.addEventListener('input', fromMacros);
  kcalField.addEventListener('input', fromKcal);
  ppkField?.addEventListener('input', fromFormula);
  fpkField?.addEventListener('input', fromFormula);

  macros.querySelector('[data-macro-modo]')?.addEventListener('change', () => {
    if (modo() === 'formula') setKgFromGrams();
    showPanels();
  });

  macros.querySelectorAll('[data-gkg-delta]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.gkgDelta;
      if (key !== 'ppk' && key !== 'fpk') return;
      const target = macros.querySelector(`[data-${key}]`);
      if (!target) return;
      nudge(target, Number(btn.dataset.step) || 0.1);
    });
  });

  fromMacros();
  showPanels();
};
