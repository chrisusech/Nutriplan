/* Hábito diario tipo Duolingo: un aviso a las 11:00, no uno por comida.

   Si abre la semana hoy, se cancela el de hoy. Los próximos siete días
   quedan programados en el teléfono; no hace falta servidor. */

const HORA = 11;
const COPIAS = [
  '¿Ya viste tu menú de hoy?',
  'Tus kcal de la semana te esperan.',
  'Un día más y tu progreso se nota.',
  'Todavía puedes marcar lo que comiste hoy.',
];

const idDelDia = (when) => (
  when.getFullYear() * 10000 + (when.getMonth() + 1) * 100 + when.getDate()
);

const aLasOnce = (when) => {
  const at = new Date(when);
  at.setHours(HORA, 0, 0, 0);
  return at;
};

const masDias = (when, n) => {
  const next = new Date(when);
  next.setDate(next.getDate() + n);
  return next;
};

const copyDe = (when) => COPIAS[when.getDay() % COPIAS.length];

export const initReminders = (locals) => {
  if (!locals || document.body?.dataset?.habitRemind !== '1') return;

  const hoy = new Date();
  hoy.setHours(0, 0, 0, 0);

  const sync = async () => {
    const perm = await locals.requestPermissions();
    if (perm.display !== 'granted') return;

    const viejos = [];
    for (let i = -14; i <= 14; i += 1) {
      viejos.push({ id: idDelDia(masDias(hoy, i)) });
    }
    await locals.cancel({ notifications: viejos }).catch(() => {});

    const proximos = [];
    for (let i = 1; i <= 7; i += 1) {
      const dia = masDias(hoy, i);
      proximos.push({
        id: idDelDia(dia),
        title: 'NutriPlan',
        body: copyDe(dia),
        schedule: { at: aLasOnce(dia), allowWhileIdle: true },
      });
    }
    await locals.schedule({ notifications: proximos }).catch(() => {});
  };

  sync().catch(() => {});
};
