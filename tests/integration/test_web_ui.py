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
        admin_email="admin@nutriplan.test",  # este correo, al registrarse, es admin
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
    assert "Duración del plan" in page
    assert "Plan 30 días" in page

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
    assert 'filename="plan_15d_Ana_Perez.pdf"' in disposition
    assert "filename*=UTF-8''plan_15d_Ana_P%C3%A9rez.pdf" in disposition

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
    assert "Plan · 15 días (1 semana)" in review
    assert review.count('class="day-cell"') == 7  # una semana
    assert "Generar plan 30 días" in review


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


# --- Portal del cliente + recetas verificadas (Workstream H) ----------------


def test_recipe_upload_pending_then_admin_verifies_and_it_becomes_a_food(offline) -> None:
    trainer, _ = offline
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

    # el trainer normal no puede entrar a la cola de admin
    assert trainer.get("/admin/recetas", follow_redirects=False).status_code == 303

    # un admin (correo designado en settings) verifica
    admin = TestClient(trainer.app)
    admin.post("/signup", data={
        "name": "Admin", "business_name": "Plataforma",
        "email": "admin@nutriplan.test", "password": "clave-admin-1",
    }, follow_redirects=False)
    queue = admin.get("/admin/recetas").text
    assert "Bowl de prueba" in queue
    rid = re.search(r"/admin/recetas/([0-9a-f-]{36})/verificar", queue).group(1)
    assert admin.post(f"/admin/recetas/{rid}/verificar", follow_redirects=False).status_code == 303

    # ahora la receta figura verificada y aparece como alimento del entrenador
    assert "Verificada" in trainer.get("/recetas").text
    assert "Bowl de prueba" in trainer.get("/clientes/nuevo").text


def test_client_portal_shows_plan_read_only_and_blocks_trainer_routes(offline) -> None:
    trainer, _ = offline
    cid = _create_client(trainer)
    _generate_and_wait(trainer, cid)
    cycle = _cycle_id(trainer)
    trainer.post(f"/planes/{cycle}/aprobar", follow_redirects=False)

    # el entrenador da acceso al cliente
    granted = trainer.post(f"/clientes/{cid}/acceso",
                           data={"email": "ana@correo.com", "password": "clave-ana-11"},
                           follow_redirects=False)
    assert granted.status_code == 303

    # el cliente entra: login lo lleva al portal
    portal = TestClient(trainer.app)
    login = portal.post("/login", data={"email": "ana@correo.com", "password": "clave-ana-11"},
                        follow_redirects=False)
    assert login.status_code == 303
    assert login.headers["location"] == "/portal"

    view = portal.get("/portal")
    assert view.status_code == 200
    assert "Tu plan de la semana" in view.text
    assert "Lunes" in view.text  # las tarjetas de día se renderizan
    # PDF descargable
    pdf = portal.get("/portal/plan.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"

    # el cliente no puede tocar rutas del entrenador: lo devuelven al portal
    assert portal.get("/", follow_redirects=False).headers["location"] == "/portal"
    assert portal.get("/planes", follow_redirects=False).headers["location"] == "/portal"


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
        f"/planes/{cycle}/dia/0/porciones?fase=first_15",
        data={"slot": "desayuno", "cliente": cid, "fase": "first_15", field: original + 30},
    )
    assert response.status_code == 200

    again = client.post(
        f"/planes/{cycle}/dia/0/porciones?fase=first_15",
        data={"slot": "desayuno", "cliente": cid, "fase": "first_15", field: original + 60},
    )
    assert again.status_code == 200

    reloaded = client.get("/generador", params={"cliente": cid, "editar": 1}).text
    assert f'name="{field}" value="{original + 60:g}"' in reloaded


def test_create_client_tolerates_bad_food_ids(offline) -> None:
    """IDs de alimento inválidos o inexistentes no deben tumbar el POST /clientes."""
    client, _ = offline
    response = client.post(
        "/clientes",
        data={
            "name": "Cliente robusto",
            "sex": "female",
            "age_years": 30,
            "height_cm": 165.0,
            "weight_kg": 60.0,
            "goal": "maintain",
            "activity_level": "moderate",
            "food_ids": ["not-a-uuid", "00000000-0000-0000-0000-000000000099"],
            "intake_id": "tampoco-es-uuid",
        },
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert "cliente=" in response.headers.get("HX-Redirect", "")


def test_create_client_invalid_sex_shows_form_error(offline) -> None:
    client, _ = offline
    response = client.post(
        "/clientes",
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
                await s.execute(select(UserRow).where(UserRow.email == "valeria@fit.com"))
            ).scalar_one()
            tenant = await s.get(TenantRow, user.tenant_id)
            if tenant is not None:
                await s.delete(tenant)
            await s.commit()

    asyncio.run(delete_tenant())

    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?sesion=expirada"


def test_password_reset_flow(offline) -> None:
    """Recuperación local: enlace en pantalla → nueva clave → login."""
    client, _ = offline
    email = "reset-me@fit.com"
    client.post(
        "/signup",
        data={
            "name": "Reset Me",
            "business_name": "Reset Fit",
            "email": email,
            "password": "original12",
        },
        follow_redirects=False,
    )
    client.post("/logout")

    page = client.post("/recuperar", data={"email": email})
    assert page.status_code == 200
    assert "Enlace listo" in page.text
    import re

    match = re.search(r'href="(/recuperar/[^"]+)"', page.text)
    assert match is not None
    reset_path = match.group(1)

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
