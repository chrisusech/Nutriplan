"""Consola maestra (solo super_user).

Una pantalla por tarea, un archivo por pantalla: el listado de usuarios, la
ficha de uno, las métricas del producto, la biblioteca de recetas, los
restaurantes de calle, el catálogo de alimentos y el laboratorio para probar.

El guard de `/admin` en `app.py` revalida el rol contra la base antes de que
nada de esto se ejecute; ningún módulo de aquí vuelve a fiarse de la cookie.
"""

from fastapi import APIRouter

from nutriplan.ui.web.routes.admin import (
    alimentos,
    laboratorio,
    metricas,
    recetas,
    restaurantes,
    usuario,
    usuarios,
)

router = APIRouter()
router.include_router(usuarios.router)
router.include_router(alimentos.router)
router.include_router(usuario.router)
router.include_router(metricas.router)
router.include_router(recetas.router)
router.include_router(restaurantes.router)
router.include_router(laboratorio.router)

__all__ = ["router"]
