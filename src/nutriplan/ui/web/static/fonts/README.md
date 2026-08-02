# Fuentes auto-alojadas

No se sirven desde el CDN de Google: una CSP estricta lo bloquea, dentro de
Capacitor no hay red garantizada, y cada carga filtraría la IP del usuario a un
tercero.

| Archivo | Fuente | Licencia |
|---|---|---|
| `poppins-{400,500,600,700,800}.woff2` | Poppins, subset `latin` | SIL OFL 1.1 — ver `OFL-Poppins.txt` |
| `material-symbols-rounded.woff2` | Material Symbols Rounded, subset a los iconos en uso | Apache 2.0 |

Regenerar tras agregar o quitar iconos en las plantillas:

```bash
uv run python scripts/fetch_fonts.py
```
