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
from tests.integration.test_parse_intake import ANA

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intakes"

GENERATION_TIMEOUT_S = 60.0


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    """Container de prueba: base efímera, branding/exports en tmp, modo offline."""
    monkeypatch.setattr(
        Settings, "exports_dir", property(lambda _self: tmp_path / "exports")
    )  # sin esto los tests dejan PDFs en data/exports del repo
    monkeypatch.setattr(
        Settings, "branding_dir", property(lambda _self: tmp_path / "branding")
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/web.db",
        anthropic_api_key="",  # explícito: gana sobre un .env local del desarrollador
    )
    return Container(settings=settings, tenant_id=DEFAULT_TENANT_ID)


def _signup(client: TestClient) -> None:
    """Registra e inicia sesión como entrenador (el guard exige login)."""
    resp = client.post("/signup", data={
        "name": "Valeria Vega", "business_name": "Valeria Fit",
        "email": "valeria@fit.com", "password": "supersecreta",
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text


@pytest.fixture
def offline(container):
    """App en modo offline: la generación cae en el HeuristicSelector."""
    with TestClient(create_app(container)) as client:
        _signup(client)
        yield client, container


@pytest.fixture
def with_llm(container):
    """App con un LLM de respuestas grabadas (la ingesta Word sí lo necesita)."""
    mock = MockLLMClient()
    # cached_property: sembrar el __dict__ evita construir el AnthropicClient real
    container.__dict__["llm_client"] = mock
    with TestClient(create_app(container)) as client:
        _signup(client)
        yield client, mock


def _food_ids(client: TestClient) -> list[str]:
    """Todos los alimentos del catálogo sembrado, leídos de los chips del form."""
    html = client.get("/clientes/nuevo").text
    return re.findall(r'name="food_ids" value="([^"]+)"', html)


def _create_client(client: TestClient, name: str = "Ana Pérez") -> str:
    """Alta manual (no requiere LLM). Devuelve el id del cliente creado."""
    response = client.post(
        "/clientes",
        data={
            "name": name,
            "sex": "female",
            "age_years": 28,
            "height_cm": 165.0,
            "weight_kg": 62.0,
            "goal": "lose_fat",
            "activity_level": "moderate",
            "food_ids": _food_ids(client),
        },
        follow_redirects=False,
    )
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
        assert "gen-error" not in html, f"la generación falló: {html[:400]}"
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


# --- Estados vacíos --------------------------------------------------------


def test_dashboard_without_clients_renders_empty_state(offline) -> None:
    client, _ = offline
    response = client.get("/")
    assert response.status_code == 200
    assert "Aún no hay" in response.text or "Nuevo cliente" in response.text


def test_plans_page_without_plans_renders_empty_state(offline) -> None:
    client, _ = offline
    response = client.get("/planes")
    assert response.status_code == 200
    assert "Aún no hay planes por aquí" in response.text


# --- Ingesta ---------------------------------------------------------------


def test_intake_word_upload_renders_review(with_llm) -> None:
    client, mock = with_llm
    mock.enqueue(ANA)
    response = client.post(
        "/intake",
        files={"archivo": ("intake_limpio.docx", (FIXTURES / "intake_limpio.docx").read_bytes())},
    )
    assert response.status_code == 200
    assert "Ana Pérez" in response.text  # el form de revisión viene precargado
    assert mock.calls and mock.calls[0]["kind"] == "extract"


def test_intake_offline_explains_instead_of_crashing(offline) -> None:
    client, _ = offline
    response = client.post(
        "/intake",
        files={"archivo": ("intake_limpio.docx", (FIXTURES / "intake_limpio.docx").read_bytes())},
    )
    assert response.status_code == 200
    assert "ANTHROPIC_API_KEY" in response.text


# --- Generador: controles --------------------------------------------------


def test_goal_toggle_persists_and_rerenders(offline) -> None:
    client, _ = offline
    cid = _create_client(client)

    response = client.post(f"/generador/{cid}/objetivo", data={"goal": "gain_muscle"})
    assert response.status_code == 200
    assert "Superávit" in response.text  # etiqueta del nuevo objetivo, ya re-renderizada

    # y sobrevive a una recarga limpia de la pantalla
    assert "Superávit" in client.get("/generador", params={"cliente": cid}).text


def test_macro_override_marks_targets_as_overridden(offline) -> None:
    client, _ = offline
    cid = _create_client(client)

    page = client.get("/generador", params={"cliente": cid}).text
    assert "Calculado, editable" in page

    response = client.post(
        f"/generador/{cid}/macros",
        data={"kcal": 2000, "protein_g": 150, "carb_g": 200, "fat_g": 60},
    )
    assert response.status_code == 200
    assert 'name="kcal" type="number" step="1" min="0"\n                     value="2000"' in (
        response.text
    )
    # solo lo que difiere del cálculo es override, y la UI lo dice
    assert "Ajustado a mano" in response.text


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
    food = _food_ids(client)[0]

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


# --- Revisión, aprobación y export -----------------------------------------


def _cycle_id(client: TestClient) -> str:
    match = re.search(r'href="/planes/([0-9a-f-]{36})"', client.get("/planes").text)
    assert match is not None
    return match.group(1)


def test_draft_cannot_be_exported_until_approved(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)

    # compuerta humana: el borrador redirige a la revisión en vez de descargar
    blocked = client.get(f"/planes/{cycle}/export.pdf", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == f"/planes/{cycle}"

    approved = client.post(f"/planes/{cycle}/aprobar", follow_redirects=False)
    assert approved.status_code == 303

    review = client.get(f"/planes/{cycle}")
    assert "Plan aprobado" in review.text

    pdf = client.get(f"/planes/{cycle}/export.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content[:5] == b"%PDF-"
    # "Ana Pérez" no cabe cruda en un header HTTP: ASCII + parámetro RFC 6266
    disposition = pdf.headers["content-disposition"]
    assert 'filename="plan_semanal_Ana_Perez.pdf"' in disposition
    assert "filename*=UTF-8''plan_semanal_Ana_P%C3%A9rez.pdf" in disposition

    docx = client.get(f"/planes/{cycle}/export.docx")
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"  # zip → docx


def test_approved_plan_can_be_reopened_and_corrected(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)
    cycle = _cycle_id(client)

    client.post(f"/planes/{cycle}/aprobar", follow_redirects=False)
    assert "Reabrir para corregir" in client.get(f"/planes/{cycle}").text

    # reabrir → vuelve a borrador y lleva al editor
    reopened = client.post(f"/planes/{cycle}/reabrir", follow_redirects=False)
    assert reopened.status_code == 303
    assert "editar=1" in reopened.headers["location"]

    # ahora sí se puede editar (estado draft) y volver a aprobar
    page = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    field = re.search(r'name="(grams_[0-9a-f-]{36})" value="([\d.]+)"', page)
    assert field is not None
    edit = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, field.group(1): float(field.group(2)) + 25},
    )
    assert edit.status_code == 200
    assert client.post(f"/planes/{cycle}/aprobar", follow_redirects=False).status_code == 303


def test_review_page_shows_the_week_grid(offline) -> None:
    client, _ = offline
    cid = _create_client(client)
    _generate_and_wait(client, cid)

    review = client.get(f"/planes/{_cycle_id(client)}").text
    assert "Plan de la semana" in review
    assert review.count('class="day-cell"') == 7  # una semana


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


def test_two_trainers_do_not_see_each_others_clients(container) -> None:
    """Dos entrenadores registrados quedan aislados por tenant, y cada uno
    conserva su propia marca."""
    app = create_app(container)
    with TestClient(app) as a:
        b = TestClient(app)  # segunda sesión (cookie jar propio), misma app

        assert a.post("/signup", data={
            "name": "Ana Coach", "business_name": "Estudio Ana",
            "email": "ana@fit.com", "password": "clave-ana-1",
        }, follow_redirects=False).status_code == 303
        assert b.post("/signup", data={
            "name": "Beto Coach", "business_name": "Estudio Beto",
            "email": "beto@fit.com", "password": "clave-beto-1",
        }, follow_redirects=False).status_code == 303

        _create_client(a, name="Cliente De Ana")

        # B no ve al cliente de A; A sí lo ve
        assert "Cliente De Ana" not in b.get("/").text
        assert "Cliente De Ana" in a.get("/").text

        # cada entrenador lleva su propia marca
        assert "Estudio Ana" in a.get("/").text
        assert "Estudio Beto" in b.get("/").text
        assert "Estudio Beto" not in a.get("/").text


def test_login_rejects_bad_password_and_accepts_good_one(container) -> None:
    app = create_app(container)
    with TestClient(app) as client:
        client.post("/signup", data={
            "name": "Carla", "business_name": "Estudio Carla",
            "email": "carla@fit.com", "password": "clave-carla-1",
        }, follow_redirects=False)
        client.post("/logout", follow_redirects=False)

        bad = client.post("/login", data={"email": "carla@fit.com", "password": "no-es"},
                          follow_redirects=False)
        assert bad.status_code == 200 and "incorrect" in bad.text.lower()

        good = client.post("/login", data={"email": "carla@fit.com", "password": "clave-carla-1"},
                           follow_redirects=False)
        assert good.status_code == 303
        assert good.headers["location"] == "/"


# --- Edición de porciones (ejercita SqlPlanRepository.update_days) ----------


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
        data={"slot": "desayuno", "cliente": cid, "fase": "0", field: original + 30},
    )
    assert response.status_code == 200

    # update_days borra los días viejos antes de reinsertar: sin esto, el unique
    # (plan_cycle_id, day_index) hace fallar el segundo guardado del mismo día.
    again = client.post(
        f"/planes/{cycle}/dia/0/porciones",
        data={"slot": "desayuno", "cliente": cid, "fase": "0", field: original + 60},
    )
    assert again.status_code == 200

    reloaded = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    assert f'name="{field}" value="{original + 60:g}"' in reloaded
