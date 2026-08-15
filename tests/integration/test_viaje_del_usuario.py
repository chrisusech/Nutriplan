"""El viaje completo de quien descarga la app.

Se registra, se describe, recibe su menú, lo lee, califica un plato y opina.
Cada test es un paso de ese camino, no una ruta suelta.
"""

import asyncio
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

GENERATION_TIMEOUT_S = 60.0
# Texto literal de partials/gen_error.html. Si la plantilla cambia, este test
# tiene que enterarse: un marcador que no existe convierte cada fallo en una
# espera de 60 s con un mensaje que no dice nada.
FAILURE_MARKER = "No se pudo generar el menú."


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/app.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _registrar(client: TestClient, email: str = "ana@correo.com") -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana Pérez", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _onboarding(client: TestClient, **overrides) -> None:
    data = {
        "name": "Ana Pérez",
        "sex": "female",
        "age_years": 28,
        "height_cm": 165,
        "weight_kg": 62,
        "goal": "lose_fat",
        "activity_level": "moderate",
        "meal_slots": [s.value for s in MealSlot],
        "eating_pattern_raw": "Desayuno rápido, entreno de noche.",
    }
    data.update(overrides)
    resp = client.post("/onboarding", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    # Termina en su semana, no en la consola del entrenador que ya no existe.
    assert resp.headers["location"] == "/"


def _generar(client: TestClient) -> None:
    """Lanza la generación y espera al job, como hace la pantalla."""
    started = client.post("/menu/generar")
    assert started.status_code == 200, started.text
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, f"la respuesta no trae job: {started.text[:300]}"

    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = client.get("/menu/estado", params={"job": job.group(1), "n": 0})
        # Al terminar se aterriza en la compra, no en el menú.
        if resp.headers.get("HX-Redirect", "").startswith("/compra"):
            return
        assert FAILURE_MARKER not in resp.text, f"la generación falló: {resp.text[:400]}"
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


# --- El camino completo -----------------------------------------------------


def test_el_marcador_de_fallo_existe_en_la_plantilla() -> None:
    """Sin esto, un job que falla no se detecta y el test espera el timeout
    entero para decir "no terminó" en vez de decir por qué."""
    from pathlib import Path

    tpl = (
        Path(__file__).resolve().parents[2]
        / "src/nutriplan/ui/web/templates/partials/gen_error.html"
    )
    assert FAILURE_MARKER in tpl.read_text(encoding="utf-8")


def test_de_registrarse_a_tener_su_menu_en_pantalla(app) -> None:
    _registrar(app)
    assert app.get("/onboarding").status_code == 200

    _onboarding(app)
    assert "Generar mi plan" in app.get("/").text

    _generar(app)
    semana = app.get("/").text
    assert "Ingredientes" in semana
    assert "En cola" in semana or "Preparación" in semana or "generando tu receta" in semana.lower()
    assert semana.count('<details class="meal"') == 5
    compra = app.get("/compra")
    assert compra.status_code == 200
    assert "Compra de la semana" in compra.text
    assert "g" in compra.text


def test_quien_entra_sin_perfil_va_derecho_al_onboarding(app) -> None:
    _registrar(app)
    resp = app.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"


def test_quien_ya_tiene_perfil_no_vuelve_a_pasar_por_el_onboarding(app) -> None:
    _registrar(app)
    _onboarding(app)
    again = app.get("/onboarding", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/"


def test_sin_elegir_dia_abre_el_de_hoy(app) -> None:
    from datetime import date

    _registrar(app)
    _onboarding(app)
    _generar(app)
    html = app.get("/").text
    assert 'data-habit-remind="1"' in html
    assert "day on today" in html
    hoy = date.today().weekday()
    assert f'href="/?dia={hoy}"' in html


def test_se_puede_mirar_cualquier_dia_de_la_semana(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    for dia in range(7):
        resp = app.get("/", params={"dia": dia})
        assert resp.status_code == 200
    # Un día fuera de rango no revienta: se acota
    assert app.get("/", params={"dia": 99}).status_code == 200


# --- El dato del BETA -------------------------------------------------------


def test_calificar_un_plato_queda_guardado(app) -> None:
    """Es el dato por el que existe este lanzamiento."""
    _registrar(app)
    _onboarding(app)
    _generar(app)

    resp = app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno", "rating": 5},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert 'class="star on"' in app.get("/", params={"dia": 0}).text


def test_al_poner_la_nota_la_app_pregunta_por_que(app) -> None:
    """La estrella es media respuesta. Si la caja del «por qué» no vuelve abierta
    y delante, nadie la escribe: 27 notas y ni un comentario en el beta."""
    _registrar(app)
    _onboarding(app)
    _generar(app)

    resp = app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno", "rating": 2},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/?dia=0&nota=desayuno#nota-desayuno"

    html = app.get("/", params={"dia": 0, "nota": "desayuno"}).text
    assert "¿Qué falló?" in html
    assert '<details class="meal" id="nota-desayuno"\n               open>' in html


def test_calificar_con_htmx_no_recarga_la_semana(app) -> None:
    """En iOS un POST clásico dejaba un fogonazo negro entre páginas."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    resp = app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno", "rating": 4},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:400]
    assert "<html" not in resp.text.lower()
    assert 'class="star on"' in resp.text
    assert "¿Qué te gustó?" in resp.text
    assert 'hx-post="/calificar"' in resp.text


def test_a_quien_le_gusto_se_le_pregunta_otra_cosa(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    app.post("/calificar", data={"dia": 0, "slot": "desayuno", "rating": 5})

    assert "¿Qué te gustó?" in app.get("/", params={"dia": 0, "nota": "desayuno"}).text


def test_escribir_el_porque_no_cambia_la_nota_que_habia_puesto(app) -> None:
    """El botón de guardar mandaba un 5 fijo: escribir «estaba salado» se
    registraba como cinco estrellas y ensuciaba el único dato que tenemos."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    app.post("/calificar", data={"dia": 0, "slot": "desayuno", "rating": 2})
    app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno", "rating": 2, "comment": "Estaba muy salado"},
    )

    html = app.get("/", params={"dia": 0, "nota": "desayuno"}).text
    assert html.count('class="star on"') == 2
    assert "Estaba muy salado" in html


def test_volver_a_calificar_corrige_en_vez_de_duplicar(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    for nota in (2, 5):
        app.post(
            "/calificar",
            data={"dia": 0, "slot": "desayuno", "rating": nota},
            follow_redirects=False,
        )
    assert app.get("/", params={"dia": 0}).text.count('class="star on"') == 5


def test_calificar_sin_estrella_vuelve_a_la_semana_y_no_pinta_json(app) -> None:
    """WebKit a veces manda el form sin `rating`. Un 422 en la app nativa es
    una pantalla blanca con JSON: hay que volver a la semana, no reventar."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    resp = app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/")
    assert "detail" not in resp.text


def test_dos_calificaciones_a_la_vez_no_tiran_500(app) -> None:
    """Estrella y comentario son el mismo POST: dos toques seguidos no pueden
    pelearse el unique de SQLite ni devolver un 500."""
    _registrar(app)
    _onboarding(app)
    _generar(app)

    def _una(nota: int):
        return app.post(
            "/calificar",
            data={"dia": 0, "slot": "desayuno", "rating": nota, "comment": f"nota {nota}"},
            follow_redirects=False,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futuros = [pool.submit(_una, n) for n in (4, 5)]
        respuestas = [f.result() for f in as_completed(futuros)]

    assert all(r.status_code != 500 for r in respuestas), [r.text[:200] for r in respuestas]
    assert all(r.status_code == 303 for r in respuestas)
    html = app.get("/", params={"dia": 0, "nota": "desayuno"}).text
    assert 'class="star on"' in html


def test_calificar_con_receta_en_vuelo_no_tira_locked(app, container) -> None:
    """Abrir el plato pide IA; calificar al mismo tiempo no puede morir con
    `database is locked` porque la receta ya no retiene el writer durante Gemini."""
    from nutriplan.domain.errors import LLMError

    class _Lento:
        async def extract(self, **_kwargs: object) -> object:
            await asyncio.sleep(0.8)
            raise LLMError("lento a propósito")

        async def select_plan(self, **_kwargs: object) -> object:
            raise LLMError("no")

        def pop_usage(self) -> dict[str, int]:
            return {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    _registrar(app)
    _onboarding(app)
    _generar(app)
    container.__dict__["llm_client"] = _Lento()

    receta: list[int] = []

    def _pedir_receta() -> None:
        receta.append(app.get("/menu/receta", params={"dia": 0, "slot": "desayuno"}).status_code)

    hilo = threading.Thread(target=_pedir_receta)
    hilo.start()
    time.sleep(0.15)
    resp = app.post(
        "/calificar",
        data={"dia": 0, "slot": "desayuno", "rating": 5},
        follow_redirects=False,
    )
    hilo.join()
    assert resp.status_code == 303, resp.text
    assert receta == [200]
    assert "database is locked" not in (resp.text or "")


def test_opinar_guarda_el_comentario_y_lo_agradece(app) -> None:
    _registrar(app)
    resp = app.post(
        "/feedback",
        data={"category": "idea", "message": "Me faltó variedad."},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Gracias" in app.get("/feedback?gracias=1").text


def test_un_comentario_vacio_no_se_guarda(app) -> None:
    _registrar(app)
    resp = app.post("/feedback", data={"category": "idea", "message": "   "})
    assert resp.status_code == 200
    assert "Escríbenos algo" in resp.text


# --- Las reglas del menú ----------------------------------------------------


def test_quien_no_marca_ningun_alimento_igual_recibe_un_menu_completo(app) -> None:
    """ "Sorpréndeme" es una respuesta válida: no marcar nada abre el catálogo."""
    _registrar(app)
    _onboarding(app, food_ids=[])
    _generar(app)
    assert app.get("/").text.count('<details class="meal"') == 5


def test_lo_que_alguien_dice_que_no_quiere_ver_no_aparece_en_su_menu(app) -> None:
    """Antes los dislikes se guardaban en `notes` y no los leía nadie."""
    _registrar(app)
    _onboarding(app, dislikes="huevo, atún")
    _generar(app)
    semana = " ".join(app.get("/", params={"dia": d}).text.lower() for d in range(7))
    assert "huevo" not in semana
    assert "atún" not in semana


def test_el_perfil_muestra_lo_que_la_persona_conto(app) -> None:
    _registrar(app)
    _onboarding(app, city="Medellín")
    perfil = app.get("/perfil").text
    assert "Medellín" in perfil
    assert "Desayuno rápido" in perfil
    assert "Déficit" in perfil


# --- Aislamiento ------------------------------------------------------------


def test_una_persona_no_puede_ver_el_perfil_de_otra(container) -> None:
    app = create_app(container)
    with TestClient(app) as a:
        _registrar(a, "ana@correo.com")
        _onboarding(a)
        b = TestClient(app)
        _registrar(b, "beto@correo.com")

        assert "ana@correo.com" in a.get("/perfil").text
        perfil_de_b = b.get("/perfil").text
        assert "ana@correo.com" not in perfil_de_b
        assert "Medellín" not in perfil_de_b

        # B no hereda el menú de A: sigue sin perfil
        sin_perfil = b.get("/", follow_redirects=False)
        assert sin_perfil.status_code == 303
        assert sin_perfil.headers["location"] == "/onboarding"


def test_comer_fuera_abre_el_menu_en_el_restaurante(app) -> None:
    """Crepes se abre ahí mismo: no hay que bajar a otra lista."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    pagina = app.get("/menu/fuera", params={"dia": 1, "slot": "cena"})
    assert pagina.status_code == 200
    assert '<details class="resto"' in pagina.text
    assert "Crepes" in pagina.text
    assert 'name="resto"' in pagina.text
    assert "Lomito" in pagina.text or "Pollo Thai" in pagina.text


def test_marcar_comido_con_htmx_no_recarga_el_dia(app) -> None:
    """En iOS reemplazar el día entero dejaba un fogonazo negro."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    resp = app.post(
        "/menu/comi",
        data={"dia": "0", "slot": "desayuno", "eaten": "1"},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert resp.status_code == 200, resp.text[:400]
    assert "<html" not in resp.text.lower()
    assert "day-hero" not in resp.text
    assert "meal-check" in resp.text
    assert "Comido" in resp.text
    assert 'hx-post="/menu/comi"' in resp.text


def test_comer_fuera_en_la_app_recuadra_el_martes(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    semana = app.get("/").text
    cabeza, _, _cuerpo = semana.partition("<details")
    assert "Voy a comer fuera" in cabeza
    pagina = app.get("/menu/fuera", params={"dia": 1, "slot": "cena", "q": "pepperoni"})
    assert pagina.status_code == 200
    assert "Voy a comer fuera" in pagina.text
    assert "pepperoni" in pagina.text.lower()
    resp = app.post(
        "/menu/fuera",
        data={
            "dia": 1,
            "slot": "cena",
            "restaurant_id": "dominos",
            "dish_id": "pepperoni",
            "servings": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    where = resp.headers.get("location") or ""
    assert "nota=cena" in where
    martes = app.get("/", params={"dia": 1, "nota": "cena"}).text
    assert "pepperoni" in martes.lower() or "domin" in martes.lower()
    assert "Cambiar de restaurante" in martes


def test_el_anillo_empieza_vacio_y_sube_al_marcar_tres_comidas(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    vacio = app.get("/").text
    assert 'class="ring-value">0</span>' in vacio or ">0</span>" in vacio
    assert "Quedan" in vacio
    assert "0 de 5 comidas" in vacio
    for slot in ("desayuno", "snack_am", "almuerzo"):
        resp = app.post(
            "/menu/comi",
            data={"dia": "0", "slot": slot, "eaten": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
    lleno = app.get("/", params={"dia": 0}).text
    assert "3 de 5 comidas" in lleno
    assert 'class="ring-value">0</span>' not in lleno


def test_cambiar_con_una_frase_larga_interpreta_los_alimentos(app) -> None:
    """Si no hay plantilla, igual se arma el plato con lo que nombró."""
    _registrar(app)
    _onboarding(app)
    _generar(app)
    resp = app.post(
        "/menu/cambiar",
        data={
            "dia": 0,
            "slot": "almuerzo",
            "nota": "Quisiera comer pechuga de pollo con aguacate y papa",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    where = resp.headers.get("location") or ""
    assert "error=" not in where
    assert "nota=almuerzo" in where
    despues = app.get("/", params={"dia": 0, "nota": "almuerzo"}).text.lower()
    assert "pechuga" in despues or "pollo" in despues
    assert "aguacate" in despues
    assert "papa" in despues


def test_cambiar_un_plato_desde_la_semana(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    pagina = app.get("/menu/cambiar", params={"dia": 0, "slot": "cena"})
    assert pagina.status_code == 200
    assert "Cambiar este plato" in pagina.text
    antes = app.get("/", params={"dia": 0}).text
    resp = app.post(
        "/menu/cambiar",
        data={"dia": 0, "slot": "cena", "nota": "pollo"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    despues = app.get("/", params={"dia": 0}).text
    assert "Ingredientes" in despues
    assert despues != antes or "error" not in (resp.headers.get("location") or "")


def test_cambiar_sin_elegir_comida_no_manda_a_restaurantes(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    resp = app.get("/menu/cambiar", follow_redirects=False)
    assert resp.status_code == 303
    where = resp.headers.get("location") or ""
    assert "/menu/fuera" not in where
    assert "error=" in where


def test_el_paywall_concede_semanas_con_una_compra_local(app) -> None:
    _registrar(app)
    pagina = app.get("/plan")
    assert pagina.status_code == 200
    assert "Activar mi plan" in pagina.text
    assert "nutriplan.monthly" in pagina.text
    resp = app.post(
        "/plan/activar",
        data={
            "product_id": "nutriplan.monthly",
            "transaction_id": "txn-test-1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/perfil"
    # La misma transacción no duplica.
    otra = app.post(
        "/plan/activar",
        data={
            "product_id": "nutriplan.monthly",
            "transaction_id": "txn-test-1",
        },
        follow_redirects=False,
    )
    assert otra.status_code == 303


def test_en_el_perfil_puede_poner_sus_macros(app) -> None:
    _registrar(app)
    _onboarding(app)
    perfil = app.get("/perfil").text
    assert "Ajustar mis números" in perfil
    assert "data-macros" in perfil
    assert 'data-macro="protein"' in perfil
    assert "data-ppk" in perfil
    assert "g × kg" in perfil
    resp = app.post(
        "/perfil/macros",
        data={
            "kcal": "2000",
            "protein_g": "140",
            "carb_g": "180",
            "fat_g": "80",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/perfil")
    guardado = app.get("/perfil").text
    assert 'value="2000"' in guardado or "2000" in guardado


def test_sin_menu_comer_fuera_vuelve_a_casa(app) -> None:
    _registrar(app)
    _onboarding(app)
    resp = app.get("/menu/fuera", follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
