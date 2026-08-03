"""Prueba de humo: ¿le genera menú la app a perfiles plausibles?

No es un test de la suite —levanta el servidor de verdad y habla con él por
HTTP— porque comprueba justo lo que los tests no vieron: que el motor sepa
cuadrar un plan para gente distinta. Un fallo aquí es alguien que se registra,
pide su menú y se queda sin nada.

    env LLM_API_KEY="" LLM_BASE_URL="" uv run nutriplan   # en otra terminal
    uv run python scripts/perfiles_smoke.py

Sin claves de IA se ejerce el motor determinista, que es donde han salido los
fallos. Con claves puestas, además se ve cuánto tarda la selección con IA.
"""

from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass, field

import httpx

BASE = "http://127.0.0.1:8000"
TIMEOUT_S = 120.0
CINCO = ["desayuno", "snack_am", "almuerzo", "snack_pm", "cena"]


@dataclass(frozen=True)
class Perfil:
    nombre: str
    sex: str
    age_years: int
    height_cm: int
    weight_kg: int
    goal: str
    meal_slots: list[str] = field(default_factory=lambda: list(CINCO))

    def como_formulario(self) -> dict[str, object]:
        return {
            "name": "Prueba", "sex": self.sex, "age_years": self.age_years,
            "height_cm": self.height_cm, "weight_kg": self.weight_kg,
            "goal": self.goal, "activity_level": "moderate",
            "meal_slots": self.meal_slots,
        }


PERFILES = [
    Perfil("mujer 62 kg, déficit, 5 comidas", "female", 28, 165, 62, "lose_fat"),
    Perfil("mujer 55 kg, déficit, 5 comidas", "female", 35, 158, 55, "lose_fat"),
    Perfil("hombre 90 kg, volumen, 5 comidas", "male", 30, 180, 90, "gain_muscle"),
    Perfil("hombre 70 kg, mantener, 4 comidas", "male", 45, 175, 70, "maintain",
           ["desayuno", "almuerzo", "snack_pm", "cena"]),
    Perfil("mujer 62 kg, déficit, 3 comidas", "female", 28, 165, 62, "lose_fat",
           ["desayuno", "almuerzo", "cena"]),
    Perfil("hombre 60 kg, volumen, 3 comidas", "male", 22, 170, 60, "gain_muscle",
           ["desayuno", "almuerzo", "cena"]),
]


class NoLlegoAlFormulario(Exception):
    """Ni siquiera se pudo intentar: servidor caído, o el limitador de peticiones
    cortando tras varias altas seguidas desde la misma IP."""


def _csrf(cliente: httpx.Client, ruta: str) -> str:
    encontrado = re.search(r'name="_csrf" value="([^"]+)"', cliente.get(ruta).text)
    if encontrado is None:
        raise NoLlegoAlFormulario(ruta)
    return encontrado.group(1)


def _texto(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _alta(cliente: httpx.Client, perfil: Perfil) -> None:
    """Registro, consentimiento y onboarding: el camino de cualquiera."""
    email = f"smoke{time.time_ns()}@correo.com"
    cliente.post(
        "/registro", headers={"X-CSRF-Token": _csrf(cliente, "/registro")},
        data={"name": "Prueba", "email": email, "password": "clave-segura-1"},
    )
    cliente.post(
        "/consentimiento", headers={"X-CSRF-Token": _csrf(cliente, "/consentimiento")},
        data={"acepta": "1"},
    )
    cliente.post(
        "/onboarding", headers={"X-CSRF-Token": _csrf(cliente, "/onboarding")},
        data=perfil.como_formulario(),
    )


def _generar(cliente: httpx.Client) -> tuple[bool, str, float]:
    """Pide el menú y espera como espera la pantalla."""
    inicio = time.monotonic()
    respuesta = cliente.post("/menu/generar", headers={"X-CSRF-Token": _csrf(cliente, "/")})
    job = re.search(r"job=([0-9a-f-]{36})", respuesta.text)
    if job is None:
        return False, "no arrancó: " + _texto(respuesta.text)[:160], 0.0

    while time.monotonic() - inicio < TIMEOUT_S:
        html = cliente.get("/menu/estado", params={"job": job.group(1), "n": 0}).text
        if "Todo listo" in html:
            return True, "", time.monotonic() - inicio
        if "No se pudo" in html or "interrump" in html:
            return False, _texto(html)[:200], time.monotonic() - inicio
        time.sleep(1)
    return False, f"seguía generando tras {TIMEOUT_S:.0f} s", TIMEOUT_S


def main() -> int:
    fallos = []
    for perfil in PERFILES:
        with httpx.Client(base_url=BASE, follow_redirects=False, timeout=90) as cliente:
            _alta(cliente, perfil)
            cuadro, detalle, segundos = _generar(cliente)
        if cuadro:
            print(f"  OK    {perfil.nombre}  ({segundos:.0f} s)")
        else:
            print(f"  FALLA {perfil.nombre}\n        {detalle}")
            fallos.append(perfil.nombre)

    print(f"\n{len(PERFILES) - len(fallos)} de {len(PERFILES)} generaron su menú.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
