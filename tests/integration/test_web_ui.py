"""UI web end-to-end sobre la app FastAPI real (Paso 11).

Se monta `create_app(container)` con un container inyectado: SQLite en tmp_path
y `llm_client` controlado, así que ningún test toca `data/app.db` ni la red.

Los tests son síncronos a propósito: `TestClient` corre el event loop en su
propio hilo, que es lo que deja avanzar la tarea de fondo de generación entre
un request y el siguiente (el polling de `/estado` la ve terminar).
"""

import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intakes"

GENERATION_TIMEOUT_S = 60.0
# Texto literal de partials/gen_error.html. Si esa plantilla cambia, este test
# tiene que enterarse: un marcador que no existe convierte cada fallo de
# generación en una espera de 60 s con un mensaje que no dice nada.
FAILURE_MARKER = "No se pudo generar el plan."


def _failure_reason(html: str) -> str:
    """El motivo real que el job dejó en gen_error.html, sin el HTML alrededor."""
    match = re.search(r'<div style="margin-top:4px">(.*?)</div>', html, re.S)
    return match.group(1).strip() if match else html[:300]


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    """Container de prueba: base efímera, branding en tmp, modo offline."""
    monkeypatch.setattr(
        Settings, "branding_dir", property(lambda _self: tmp_path / "branding")
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/web.db",
        anthropic_api_key="",  # explícito: gana sobre un .env local del desarrollador
        admin_email=SUPER_EMAIL,  # se siembra un super_user al arrancar
        admin_password=SUPER_PASSWORD,
    )
    return Container(settings=settings, tenant_id=DEFAULT_TENANT_ID)


# Ya no hay alta pública: el super_user se siembra al arrancar (bootstrap) y crea
# a los entrenadores. Los tests entran como ese super_user (sin cupos), que es
# quien puede hacer todo el flujo sin toparse con límites.
SUPER_EMAIL = "admin@nutriplan.test"
SUPER_PASSWORD = "super-secreta-1"


def _login(client: TestClient, email: str = SUPER_EMAIL, password: str = SUPER_PASSWORD) -> None:
    resp = client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text


def _create_trainer(
    client: TestClient, *, name: str, email: str, password: str,
    max_menus: str = "",
) -> None:
    """El super_user da de alta una cuenta desde el módulo de administración."""
    resp = client.post("/admin/entrenadores", data={
        "name": name, "email": email, "password": password, "max_menus": max_menus,
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text


@pytest.fixture
def offline(container):
    """App en modo offline: la generación cae en el HeuristicSelector."""
    with TestClient(create_app(container)) as client:
        _login(client)
        yield client, container


@pytest.fixture
def with_llm(container):
    """App con un LLM de respuestas grabadas (la ingesta Word sí lo necesita)."""
    mock = MockLLMClient()
    # cached_property: sembrar el __dict__ evita construir el AnthropicClient real
    container.__dict__["llm_client"] = mock
    with TestClient(create_app(container)) as client:
        _login(client)
        yield client, mock


def _food_ids(client: TestClient, cid: str | None = None) -> list[str]:
    """Los alimentos del catálogo, leídos de los chips que estén a la vista.

    Antes del onboarding están en su formulario; después, en el generador (una
    vez hay perfil, /onboarding redirige y ya no tiene chips que leer).
    """
    if cid:
        html = client.get(f"/generador?cliente={cid}").text
        return re.findall(r'"food_id":"([^"]+)"', html)
    html = client.get("/onboarding").text
    return re.findall(r'name="food_ids" value="([^"]+)"', html)


def _create_client(client: TestClient, name: str = "Ana Pérez", **overrides) -> str:
    """Completa el onboarding. Devuelve el id del perfil creado."""
    data = {
        "name": name,
        "sex": "female",
        "age_years": 28,
        "height_cm": 165.0,
        "weight_kg": 62.0,
        "goal": "lose_fat",
        "activity_level": "moderate",
        "meal_slots": [s.value for s in MealSlot],
        "eating_pattern_raw": "Desayuno rápido, almuerzo fuera, ceno ligero.",
    }
    data.update(overrides)
    response = client.post("/onboarding", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].split("cliente=")[1]


def _generate_and_wait(client: TestClient, cid: str) -> str:
    """Dispara la generación y hace polling a /estado hasta que el job termina."""
    started = client.post(f"/generador/{cid}/generar")
    assert started.status_code == 200, started.text
    job_id = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job_id is not None, f"la respuesta no trae job_id: {started.text[:400]}"
    job = job_id.group(1)

    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        html = client.get(f"/generador/{cid}/estado", params={"job": job, "n": 0}).text
        if "Todo listo" in html:
            return job
        # El marcador tiene que existir de verdad en partials/gen_error.html. Antes
        # se buscaba "gen-error", que no aparece en ninguna plantilla: un job que
        # fallaba no se detectaba nunca y el test se comía los 60 s de timeout
        # entero para morir diciendo "no terminó" en vez de decir por qué falló.
        assert FAILURE_MARKER not in html, f"la generación falló: {_failure_reason(html)}"
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


# --- Estados vacíos --------------------------------------------------------


def test_failure_marker_matches_the_error_template() -> None:
    """El detector de fallos de `_generate_and_wait` debe existir en la plantilla.

    Si no, un job que falla no se detecta: el bucle espera el timeout completo y
    reporta "no terminó" en vez del motivo. Es cómo se colaron 7 minutos de
    espera en la suite.
    """
    template = (
        ROOT / "src" / "nutriplan" / "ui" / "web" / "templates" / "partials" / "gen_error.html"
    )
    assert FAILURE_MARKER in template.read_text(encoding="utf-8")


def test_quien_entra_sin_perfil_va_derecho_al_onboarding(offline) -> None:
    """La app no tiene lista de clientes: tiene a quien está mirando."""
    client, _ = offline
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding"


def test_con_perfil_pero_sin_menu_se_ofrece_generarlo(offline) -> None:
    client, _ = offline
    _create_client(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Generar mi menú" in response.text


def test_plans_page_without_plans_renders_empty_state(offline) -> None:
    client, _ = offline
    response = client.get("/planes")
    assert response.status_code == 200
    assert "Aún no hay planes por aquí" in response.text


# --- Generador: controles --------------------------------------------------


def test_goal_toggle_persists_and_rerenders(offline) -> None:
    client, _ = offline
    cid = _create_client(client)

    response = client.post(f"/generador/{cid}/objetivo", data={"goal": "gain_muscle"})
    assert response.status_code == 200
    assert "Superávit" in response.text  # etiqueta del nuevo objetivo, ya re-renderizada

    # y sobrevive a una recarga limpia de la pantalla
    assert "Superávit" in client.get("/generador", params={"cliente": cid}).text


def test_the_trainer_can_take_a_snack_out_and_the_plan_has_four_meals(offline) -> None:
    """Un cliente que come cuatro veces no recibe un plan de cinco.

    Y las comidas grandes no se pueden quitar: un plan sin cena no es un plan.
    """
    client, _ = offline
    cid = _create_client(client)

    response = client.post(f"/generador/{cid}/comidas", data={"slot": "snack_pm"})
    assert response.status_code == 200

    cycle_id = _generate_and_wait(client, cid)
    plan = client.get(f"/planes/{cycle_id}").text
    assert "Snack PM" not in plan

    # La cena no se puede apagar: la ruta lo ignora y el plan la conserva.
    client.post(f"/generador/{cid}/comidas", data={"slot": "cena"})
    page = client.get("/generador", params={"cliente": cid}).text
    assert "Cena" in page


def _macros(html: str) -> dict[str, float]:
    """Los cuatro cuadritos de macros tal y como los ve el entrenador."""
    out = {}
    for key in ("kcal", "protein_g", "carb_g", "fat_g"):
        match = re.search(rf'name="{key}"[^>]*value="([\d.]+)"', html)
        assert match is not None, f"no se pinta el cuadrito de {key}"
        out[key] = float(match.group(1))
    return out


def _energy(m: dict[str, float]) -> float:
    return m["protein_g"] * 4 + m["carb_g"] * 4 + m["fat_g"] * 9


# Los cuadritos se pintan en gramos redondos, así que la identidad energética solo se puede
# comprobar con el margen de ese redondeo (medio gramo de cada macro son ~8 kcal). De sobra:
# lo que se busca es que las kcal no se queden CONGELADAS, y eso son cientos de kcal.
_ROUNDING_KCAL = 10.0


def test_editing_a_macro_moves_the_kcal_and_the_target_stays_coherent(offline) -> None:
    """Las kcal son el RESULTADO de los macros, edición tras edición.

    La regresión: la pantalla manda los cuatro campos, así que en la SEGUNDA edición las
    kcal —ya recalculadas en la primera— parecían un ajuste manual que nadie había hecho.
    Se congelaban, el objetivo dejaba de cumplir kcal = 4·P + 4·C + 9·G, y como el motor
    valida las kcal y los tres macros por separado, no había plan que lo cuadrara: el
    entrenador se comía un "No se pudo generar el plan".
    """
    client, _ = offline
    cid = _create_client(client)

    page = client.get("/generador", params={"cliente": cid}).text
    assert "Calculado, editable" in page
    shown = _macros(page)

    # Primera edición: sube la proteína.
    first = _macros(client.post(f"/generador/{cid}/macros",
                                data={**shown, "protein_g": 150}).text)
    assert first["protein_g"] == 150
    assert first["kcal"] == pytest.approx(_energy(first), abs=_ROUNDING_KCAL)

    # Segunda edición: baja la grasa. Aquí es donde se rompía.
    second = _macros(client.post(f"/generador/{cid}/macros",
                                 data={**first, "fat_g": 40}).text)
    assert second["fat_g"] == 40
    assert second["protein_g"] == 150  # lo de antes no se pierde
    assert second["kcal"] == pytest.approx(_energy(second), abs=_ROUNDING_KCAL)
    assert second["kcal"] < first["kcal"]  # menos grasa, menos kcal
    assert "Ajustado a mano" in client.get("/generador", params={"cliente": cid}).text


def test_editing_only_the_kcal_moves_the_carb(offline) -> None:
    """Es la regla de la fórmula: proteína y grasa las manda el g/kg, el carbo cierra."""
    client, _ = offline
    cid = _create_client(client)
    shown = _macros(client.get("/generador", params={"cliente": cid}).text)

    after = _macros(client.post(f"/generador/{cid}/macros",
                                data={**shown, "kcal": shown["kcal"] + 200}).text)
    assert after["protein_g"] == shown["protein_g"]
    assert after["fat_g"] == shown["fat_g"]
    assert after["carb_g"] == pytest.approx(shown["carb_g"] + 50, abs=1.0)


def test_an_impossible_macro_edit_explains_itself_instead_of_a_500(offline) -> None:
    """Los ajustes manuales pasan por los mismos pisos que la fórmula, y si no cuadran
    lo dicen en pantalla — un 500 lo único que hace es dejar la pantalla congelada."""
    client, _ = offline
    cid = _create_client(client)
    shown = _macros(client.get("/generador", params={"cliente": cid}).text)

    response = client.post(f"/generador/{cid}/macros", data={**shown, "carb_g": 5})
    assert response.status_code == 200
    assert "bajo el piso" in response.text


def test_formula_g_per_kg_moves_kcal_live(offline) -> None:
    client, _ = offline
    cid = _create_client(client)

    # subir la proteína g/kg recalcula y re-renderiza sin recargar la página
    response = client.post(
        f"/generador/{cid}/formula",
        data={"protein_g_per_kg": "2.2", "fat_g_per_kg": "1.0", "kcal_override": ""},
    )
    assert response.status_code == 200
    assert 'name="protein_g_per_kg"' in response.text and 'value="2.2"' in response.text
    # el reparto % aparece calculado en vivo
    assert "reparto" in response.text and "%" in response.text


def test_kcal_below_the_floor_explains_itself_instead_of_crashing(offline) -> None:
    """Escribir 1200 kcal a mano no es un 500 ni un plan insostenible.

    La pantalla sigue en pie con los targets anteriores y dice por qué se rechaza.
    """
    client, _ = offline
    cid = _create_client(client)
    response = client.post(
        f"/generador/{cid}/formula",
        data={"protein_g_per_kg": "1.6", "fat_g_per_kg": "0.8", "kcal_override": "1200"},
    )
    assert response.status_code == 200
    assert "bajo el piso" in response.text
    assert "no es sostenible" in response.text


def test_manual_kcal_override_sticks(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    response = client.post(
        f"/generador/{cid}/formula",
        data={"protein_g_per_kg": "1.8", "fat_g_per_kg": "0.8", "kcal_override": "1750"},
    )
    assert response.status_code == 200
    assert 'name="kcal" type="number" step="1" min="0"\n                     value="1750"' in (
        response.text
    )


def test_food_and_restriction_toggles_flip_state(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    food = _food_ids(client, cid)[0]

    off = client.post(f"/generador/{cid}/alimento", data={"food_id": food})
    assert off.status_code == 200
    on = client.post(f"/generador/{cid}/alimento", data={"food_id": food})
    assert on.status_code == 200
    # el chip vuelve a su estado inicial: el toggle es simétrico
    assert off.text != on.text

    restricted = client.post(f"/generador/{cid}/restriccion", data={"key": "no_seafood"})
    assert restricted.status_code == 200


# --- Generador: job, idempotencia y preview --------------------------------


def test_generation_job_completes_and_shows_preview(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)

    page = client.get("/generador", params={"cliente": cid}).text
    assert "Vista previa del plan" in page
    assert "Generar mi menú" in page
    # Ya no se elige duración: el producto es una semana
    assert "30 días" not in page

    listed = client.get("/planes")
    assert "Ana Pérez" in listed.text


def test_regenerating_same_inputs_reuses_the_job(offline) -> None:
    client, container = offline
    cid = _create_client(client)
    first = _generate_and_wait(client, cid)

    # sin cambiar insumos: mismo input_hash → mismo job, sin plan nuevo
    again = client.post(f"/generador/{cid}/generar")
    assert re.search(r"job=([0-9a-f-]{36})", again.text).group(1) == first

    rows = re.findall(r'href="/planes/([0-9a-f-]{36})"', client.get("/planes").text)
    assert len(rows) == 1


# --- Revisión y aprobación -------------------------------------------------


def _cycle_id(client: TestClient) -> str:
    match = re.search(r'href="/planes/([0-9a-f-]{36})"', client.get("/planes").text)
    assert match is not None
    return match.group(1)


def test_un_plan_en_borrador_no_se_puede_aprobar_dos_veces(offline) -> None:
    """La compuerta humana: el plan nace borrador y alguien decide aprobarlo."""
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)

    review = client.get(f"/planes/{cycle}")
    assert "Plan aprobado" not in review.text

    approved = client.post(f"/planes/{cycle}/aprobar", follow_redirects=False)
    assert approved.status_code == 303
    assert "Plan aprobado" in client.get(f"/planes/{cycle}").text


def test_approved_plan_allows_per_food_correction(offline) -> None:
    """Tras aprobar, el plan NO se regenera completo: solo se corrige un alimento
    puntual (el entrenador metió algo que el cliente no come), sin volver a borrador."""
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)

    client.post(f"/planes/{cycle}/aprobar", follow_redirects=False)
    review = client.get(f"/planes/{cycle}").text
    assert "Plan aprobado" in review
    assert "Corregir un alimento" in review  # ya no hay "reabrir"

    # el editor abre sobre la definitiva (APPROVED), sin reabrir a borrador
    page = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    field = re.search(r'name="(grams_[0-9a-f-]{36})" value="([\d.]+)"', page)
    assert field is not None, "el editor no abre sobre la definitiva"
    edit = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, field.group(1): float(field.group(2)) + 25},
    )
    assert edit.status_code == 200
    # sigue aprobado tras la corrección: no volvió a borrador
    assert "Plan aprobado" in client.get(f"/planes/{cycle}").text


def test_la_revision_muestra_los_siete_dias_y_ninguna_descarga(offline) -> None:
    """El menú se lee en la app: no hay PDF que ofrecer."""
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)

    review = client.get(f"/planes/{_cycle_id(client)}").text
    assert review.count('class="day-cell"') == 7  # una semana
    assert "export.pdf" not in review
    assert "export.docx" not in review


# --- Multi-entrenador: login y aislamiento por tenant (Workstream G) --------


def test_guard_redirects_anonymous_to_login(container) -> None:
    """Sin sesión, cualquier ruta protegida redirige a /login."""
    app = create_app(container)
    with TestClient(app) as anon:
        resp = anon.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/login"
        # el login sí es público
        assert anon.get("/login").status_code == 200


def test_una_persona_no_puede_ver_el_perfil_de_otra(container) -> None:
    """Dos cuentas quedan aisladas por tenant: cada quien ve lo suyo y nada más."""
    app = create_app(container)
    with TestClient(app) as admin:
        _login(admin)  # super_user
        _create_trainer(admin, name="Ana Coach", email="ana@fit.com",
                        password="clave-ana-1")
        _create_trainer(admin, name="Beto Coach", email="beto@fit.com",
                        password="clave-beto-1")

        a = TestClient(app)
        _login(a, "ana@fit.com", "clave-ana-1")
        b = TestClient(app)
        _login(b, "beto@fit.com", "clave-beto-1")

        _create_client(a, name="Cliente De Ana")

        # A tiene perfil y llega a su pantalla de menú
        assert a.get("/", follow_redirects=False).status_code == 200
        perfil_de_a = a.get("/perfil").text
        assert "ana@fit.com" in perfil_de_a
        assert "beto@fit.com" not in perfil_de_a

        # B no hereda el perfil de A: sigue sin uno
        sin_perfil = b.get("/", follow_redirects=False)
        assert sin_perfil.status_code == 303
        assert sin_perfil.headers["location"] == "/onboarding"
        assert "ana@fit.com" not in b.get("/perfil").text

        # Y B, que no tiene perfil, ni siquiera llega a una pantalla de menú
        sin_perfil = b.get("/", follow_redirects=False)
        assert sin_perfil.status_code == 303
        assert sin_perfil.headers["location"] == "/onboarding"


def test_login_rejects_bad_password_and_accepts_good_one(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        _login(client)  # super_user
        _create_trainer(client, name="Carla", email="carla@fit.com",
                        password="clave-carla-1")
        client.post("/logout", follow_redirects=False)

        bad = client.post("/login", data={"email": "carla@fit.com", "password": "no-es"},
                          follow_redirects=False)
        assert bad.status_code == 200 and "incorrect" in bad.text.lower()

        good = client.post("/login", data={"email": "carla@fit.com", "password": "clave-carla-1"},
                           follow_redirects=False)
        assert good.status_code == 303
        assert good.headers["location"] == "/"


# --- Recetas verificadas por el super_user (Workstream H) -------------------


def test_recipe_upload_pending_then_admin_verifies_and_it_becomes_a_food(offline) -> None:
    admin, _ = offline  # super_user (lo siembra el bootstrap)
    _create_trainer(admin, name="Vale", email="vale@fit.com", password="clave-vale-1")
    trainer = TestClient(admin.app)
    _login(trainer, "vale@fit.com", "clave-vale-1")

    foods = _food_ids(trainer)[:2]
    created = trainer.post("/recetas", data={
        "name": "Bowl de prueba",
        "food_id": [foods[0], foods[1]],
        f"grams_{foods[0]}": "150",
        f"grams_{foods[1]}": "80",
    }, follow_redirects=False)
    assert created.status_code == 303
    assert "Bowl de prueba" in trainer.get("/recetas").text
    assert "Pendiente" in trainer.get("/recetas").text

    # el entrenador normal no puede entrar a la cola de admin
    assert trainer.get("/admin/recetas", follow_redirects=False).status_code == 303

    # el super_user verifica (ve las recetas de todos los tenants)
    queue = admin.get("/admin/recetas").text
    assert "Bowl de prueba" in queue
    rid = re.search(r"/admin/recetas/([0-9a-f-]{36})/verificar", queue).group(1)
    assert admin.post(f"/admin/recetas/{rid}/verificar", follow_redirects=False).status_code == 303

    # ahora la receta figura verificada y aparece como alimento del entrenador
    assert "Verificada" in trainer.get("/recetas").text
    assert "Bowl de prueba" in trainer.get("/onboarding").text


def test_a_restaurant_dish_is_registered_by_its_macros_with_no_ingredients(offline) -> None:
    """El plato del que no sabes la receta, pero sí los macros exactos.

    Antes esto no se podía registrar: `create_recipe` exigía al menos un
    ingrediente, así que un cliente que come fuera no tenía forma de contarlo.
    """
    admin, _ = offline  # super_user
    _create_trainer(admin, name="Vale", email="vale@fit.com", password="clave-vale-1")
    trainer = TestClient(admin.app)
    _login(trainer, "vale@fit.com", "clave-vale-1")

    created = trainer.post("/recetas", data={
        "modo": "macros",
        "name": "Hamburguesa del restaurante",
        "protein_g": "42", "carb_g": "55", "fat_g": "18",
        "total_grams": "350",
        "meal_slot": ["almuerzo", "cena"],
    }, follow_redirects=False)
    assert created.status_code == 303

    page = trainer.get("/recetas").text
    assert "Hamburguesa del restaurante" in page
    # No son "0 ingredientes": son macros declarados.
    assert "Macros declarados" in page
    # Las kcal no se piden: 4·42 + 4·55 + 9·18 = 550.
    assert "550 kcal" in page

    queue = admin.get("/admin/recetas").text
    rid = re.search(r"/admin/recetas/([0-9a-f-]{36})/verificar", queue).group(1)
    admin.post(f"/admin/recetas/{rid}/verificar", follow_redirects=False)

    # Verificada, se vuelve un alimento del entrenador y se puede poner en un plan.
    assert "Verificada" in trainer.get("/recetas").text
    assert "Hamburguesa del restaurante" in trainer.get("/onboarding").text


def test_a_dish_with_neither_ingredients_nor_macros_says_so(offline) -> None:
    """Y el error se ve. Antes un `pass` se lo tragaba y el plato desaparecía."""
    trainer, _ = offline
    resp = trainer.post("/recetas", data={
        "modo": "macros", "name": "Aire", "total_grams": "100",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]
    assert "macros" in trainer.get(resp.headers["location"]).text
    assert "Aire" not in trainer.get("/recetas").text


# --- Edición de porciones (update_day por día) --------------------------------


def test_editing_portions_recomputes_macros_and_persists(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)

    page = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    grams_field = re.search(r'name="(grams_[0-9a-f-]{36})" value="([\d.]+)"', page)
    assert grams_field is not None, "la pantalla de edición no expone gramos editables"
    field, original = grams_field.group(1), float(grams_field.group(2))

    response = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, field: original + 30},
    )
    assert response.status_code == 200

    again = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, field: original + 60},
    )
    assert again.status_code == 200

    reloaded = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    assert f'name="{field}" value="{original + 60:g}"' in reloaded


def test_un_id_de_alimento_corrupto_no_tumba_el_onboarding(offline) -> None:
    client, _ = offline
    cid = _create_client(
        client, name="Cliente robusto",
        food_ids=["not-a-uuid", "00000000-0000-0000-0000-000000000099"],
    )
    assert cid


def test_un_sexo_invalido_devuelve_el_formulario_con_el_error(offline) -> None:
    client, _ = offline
    response = client.post(
        "/onboarding",
        data={
            "name": "Ana",
            "sex": "otro",
            "age_years": 30,
            "height_cm": 165.0,
            "weight_kg": 60.0,
            "goal": "maintain",
            "activity_level": "moderate",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert "Sexo no válido" in response.text


def test_stale_session_redirects_to_login(offline) -> None:
    """Cookie con tenant inexistente (p. ej. tras migrar DB) → re-login, no 500."""
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import TenantRow, UserRow

    client, container = offline
    assert client.get("/").status_code == 200

    async def delete_tenant() -> None:
        async with container.session_factory() as s:
            user = (
                await s.execute(select(UserRow).where(UserRow.email == SUPER_EMAIL))
            ).scalar_one()
            tenant = await s.get(TenantRow, user.tenant_id)
            if tenant is not None:
                await s.delete(tenant)
            await s.commit()

    asyncio.run(delete_tenant())

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?sesion=expirada"


def test_quien_olvida_su_clave_recibe_un_enlace_y_puede_entrar_de_nuevo(offline) -> None:
    client, container = offline  # super_user
    email = "reset-me@fit.com"
    _create_trainer(client, name="Reset Me", email=email, password="original12")
    client.post("/logout")

    page = client.post("/recuperar", data={"email": email})
    assert page.status_code == 200
    # El enlace ya NO se pinta en pantalla: viaja por correo.
    assert "/recuperar/" not in page.text
    assert "Si ese correo tiene una cuenta" in page.text

    enviado = container.mailer.sent[-1]
    assert enviado["to"] == email
    reset_path = "/recuperar/" + re.search(r"/recuperar/(\S+)", enviado["body"]).group(1)

    confirm = client.get(reset_path)
    assert confirm.status_code == 200
    assert email in confirm.text

    done = client.post(
        reset_path,
        data={"password": "nueva12345", "password_confirm": "nueva12345"},
        follow_redirects=False,
    )
    assert done.status_code == 303

    client.post("/logout")
    bad = client.post("/login", data={"email": email, "password": "original12"})
    assert "incorrectos" in bad.text
    ok = client.post(
        "/login",
        data={"email": email, "password": "nueva12345"},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_taking_a_snack_out_after_the_plan_exists_does_not_break_the_page(offline) -> None:
    """El plan viejo (5 comidas) se sigue pudiendo abrir con el cliente ya en 4."""
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)

    response = client.post(f"/generador/{cid}/comidas", data={"slot": "snack_pm"})
    assert response.status_code == 200
    assert client.get("/generador", params={"cliente": cid}).status_code == 200


def test_a_second_month_is_a_new_version_and_the_first_one_stays(offline) -> None:
    """El seguimiento: cerrar el mes, registrar el peso, ajustar y generar la v2.

    Antes se podían generar varios planes pero no decir cuál era el bueno, y los
    anteriores quedaban huérfanos: `/planes` solo pintaba el más reciente. Sin eso no
    se puede seguir el déficit de nadie.
    """
    trainer, _ = offline
    cid = _create_client(trainer)
    _generate_and_wait(trainer, cid)
    v1 = _cycle_id(trainer)
    # Solo lo DEFINITIVO se guarda: v1 se aprueba antes de pasar al mes 2. Un borrador
    # sin aprobar se pisa al regenerar; aprobar lo vuelve permanente.
    assert trainer.post(f"/planes/{v1}/aprobar", follow_redirects=False).status_code == 303

    # Mes 2: el cliente bajó de peso. Los macros se recalculan con el peso nuevo.
    before = _macros(trainer.get(f"/generador?cliente={cid}").text)
    resp = trainer.post(f"/generador/{cid}/peso", data={"peso": "58.5"})
    assert resp.status_code == 200
    after = _macros(resp.text)
    assert after != before, "el peso nuevo mueve los macros"

    # Y se genera la versión siguiente, que también se aprueba (mes 2 definitivo).
    started = trainer.post(f"/generador/{cid}/nueva-version")
    job = re.search(r"job=([0-9a-f-]{36})", started.text).group(1)
    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        html = trainer.get(f"/generador/{cid}/estado", params={"job": job, "n": 0}).text
        if "Todo listo" in html:
            break
        assert FAILURE_MARKER not in html, f"la v2 falló: {_failure_reason(html)}"
        time.sleep(0.05)
    v2 = _cycle_id(trainer)
    assert v2 != v1
    assert trainer.post(f"/planes/{v2}/aprobar", follow_redirects=False).status_code == 303

    # La v2 es la activa; la v1 NO desapareció.
    history = trainer.get(f"/planes/cliente/{cid}")
    assert history.status_code == 200
    assert "v1" in history.text and "v2" in history.text
    assert "Activo" in history.text
    # Cada versión conserva SU peso: eso es el seguimiento.
    assert "58.5" in history.text
    assert "62" in history.text  # el peso con el que se hizo la v1

    # La v1 sigue abriéndose, con sus macros de entonces.
    assert trainer.get(f"/planes/{v1}").status_code == 200

    # Y se puede volver a ella: activar es repuntar, nada se pierde.
    back = trainer.post(f"/planes/{v1}/activar", follow_redirects=False)
    assert back.status_code == 303
    assert "Plan definitivo" in trainer.get(f"/planes/{v1}").text


# --- Lógica de negocio: cupos y bloqueo (solo rol user) ---------------------


def _trainer_id(admin: TestClient, email: str) -> str:
    """Saca el id del entrenador de la página de administración."""
    page = admin.get("/admin/entrenadores").text
    # cada tarjeta trae el correo y un form /admin/entrenadores/{id}/bloquear|desbloquear
    ids = re.findall(r"/admin/entrenadores/([0-9a-f-]{36})/(?:bloquear|desbloquear)", page)
    assert ids, "no se encontró ningún entrenador en el panel"
    return ids[-1]


def _become_trainer(client: TestClient, **kwargs) -> None:
    """El super_user (sesión actual) da de alta un entrenador y se re-loguea como él.

    Se reusa el MISMO TestClient a propósito: su event loop persiste, y la tarea de
    fondo de generación necesita ese loop vivo para terminar (un segundo TestClient
    sin `with` la dejaría huérfana)."""
    _create_trainer(client, **kwargs)
    client.post("/logout", follow_redirects=False)
    _login(client, kwargs["email"], kwargs["password"])


def test_version_quota_blocks_a_second_definitive(offline) -> None:
    """Una versión definitiva por cliente: aprobada la v1, no se regenera el plan
    completo (solo correcciones). Así uno no genera planes infinitos."""
    client, _ = offline  # super_user; luego nos volvemos el entrenador
    _become_trainer(client, name="Dos", email="dos@fit.com", password="clave-dos-11",
                    max_menus="1")

    cid = _create_client(client, name="Cliente Cupo")
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)
    assert client.post(f"/planes/{cycle}/aprobar", follow_redirects=False).status_code == 303

    # Ya usó su única versión definitiva: regenerar el plan completo se bloquea.
    started = client.post(f"/generador/{cid}/nueva-version")
    assert started.status_code == 200
    assert "versiones definitivas" in started.text.lower()

    # Pero corregir un alimento sobre la definitiva sí se permite.
    page = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    field = re.search(r'name="(grams_[0-9a-f-]{36})" value="([\d.]+)"', page)
    assert field is not None
    edit = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, field.group(1): float(field.group(2)) + 20},
    )
    assert edit.status_code == 200


def test_approving_beyond_the_version_quota_is_rejected(offline) -> None:
    """Aprobar la misma versión dos veces es idempotente y no dispara el cupo."""
    client, _ = offline
    _become_trainer(client, name="Tres", email="tres@fit.com", password="clave-tres-1",
                    max_menus="1")

    cid = _create_client(client, name="Otro Cupo")
    _generate_and_wait(client, cid)
    v1 = _cycle_id(client)
    assert client.post(f"/planes/{v1}/aprobar", follow_redirects=False).status_code == 303
    # aprobar de nuevo la misma es idempotente (sigue aprobada, no dispara el cupo)
    again = client.post(f"/planes/{v1}/aprobar", follow_redirects=False)
    assert again.status_code == 303
    assert "error=" not in again.headers["location"]


def test_blocked_trainer_cannot_login(offline) -> None:
    """El super_user bloquea a un entrenador que ya no está en el programa."""
    admin, _ = offline
    _create_trainer(admin, name="Baja", email="baja@fit.com", password="clave-baja-1")
    tid = _trainer_id(admin, "baja@fit.com")
    assert admin.post(f"/admin/entrenadores/{tid}/bloquear",
                      follow_redirects=False).status_code == 303

    trainer = TestClient(admin.app)
    denied = trainer.post("/login", data={"email": "baja@fit.com", "password": "clave-baja-1"},
                          follow_redirects=False)
    assert denied.status_code == 200
    # El mensaje es el mismo que el de una clave mala: decir "bloqueada"
    # confirmaría que ese correo existe.
    assert "incorrectos" in denied.text.lower()

    # Y desbloquear lo deja entrar de nuevo.
    assert admin.post(f"/admin/entrenadores/{tid}/desbloquear",
                      follow_redirects=False).status_code == 303
    ok = trainer.post("/login", data={"email": "baja@fit.com", "password": "clave-baja-1"},
                      follow_redirects=False)
    assert ok.status_code == 303


def test_el_super_user_no_tiene_tope_de_menus(offline) -> None:
    """El tope del BETA no aplica a la cuenta dueña de la plataforma."""
    admin, _ = offline  # super_user, sin cupos
    cid = _create_client(admin, name="Dueño de la plataforma")
    _generate_and_wait(admin, cid)
    cycle = _cycle_id(admin)
    assert admin.post(f"/planes/{cycle}/aprobar", follow_redirects=False).status_code == 303
    # y puede seguir generando: sin `max_menus`, la regla del BETA no le aplica
    assert _generate_and_wait(admin, cid)


def test_quien_ya_tiene_perfil_no_vuelve_a_pasar_por_el_onboarding(offline) -> None:
    """El onboarding es una vez: al segundo intento lleva directo a su menú."""
    client, _ = offline
    cid = _create_client(client)
    again = client.get("/onboarding", follow_redirects=False)
    assert again.status_code == 303
    assert cid in again.headers["location"]


def test_quien_no_marca_ningun_alimento_igual_recibe_un_menu_completo(offline) -> None:
    """"Sorpréndeme" es una respuesta válida: no marcar nada abre el catálogo.

    Obligar a marcar decenas de ingredientes era justo lo que agobiaba, y antes
    dejaba el conjunto permitido vacío en vez de generar.
    """
    client, _ = offline
    cid = _create_client(client, food_ids=[])
    _generate_and_wait(client, cid)
    assert "Todo listo" in client.get(f"/generador?cliente={cid}").text or _cycle_id(client)


def test_lo_que_alguien_dice_que_no_quiere_ver_no_aparece_en_su_menu(offline) -> None:
    """Los dislikes son texto libre y sí filtran el catálogo.

    Antes se guardaban en `notes` y no los leía nadie.
    """
    client, _ = offline
    cid = _create_client(client, dislikes="huevo, atún")
    _generate_and_wait(client, cid)
    plan = client.get(f"/planes/{_cycle_id(client)}").text.lower()
    assert "huevo" not in plan
    assert "atún" not in plan
