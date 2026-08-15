"""Contarle a la app cómo eres, cuando algo sale mal.

El onboarding es la primera pantalla real de cualquiera. Que un dato raro
devuelva un mensaje entendible —y no pierda lo ya escrito— es la diferencia
entre corregirlo y desinstalar.
"""

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

BASE = {
    "name": "Ana",
    "sex": "female",
    "age_years": 28,
    "height_cm": 165,
    "weight_kg": 62,
    "goal": "lose_fat",
    "activity_level": "moderate",
}


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/onb.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        yield client


def _enviar(app: TestClient, **cambios):
    data = dict(BASE, meal_slots=[s.value for s in MealSlot])
    data.update(cambios)
    return app.post("/onboarding", data=data, follow_redirects=False)


# --- Datos que no se entienden ----------------------------------------------


def test_un_objetivo_que_no_existe_se_dice_con_palabras(app) -> None:
    resp = _enviar(app, goal="volverme_invisible")
    assert resp.status_code == 200
    assert "Objetivo no válido" in resp.text


def test_un_nivel_de_actividad_inventado_tambien(app) -> None:
    resp = _enviar(app, activity_level="hipersónico")
    assert "Nivel de actividad no válido" in resp.text


def test_equivocarse_no_borra_lo_que_ya_habia_escrito(app) -> None:
    """Rellenar diez campos otra vez por un error en uno es motivo de abandono."""
    resp = _enviar(app, goal="nada", eating_pattern_raw="Entreno de noche.")
    assert "Entreno de noche." in resp.text
    assert "165" in resp.text


def test_decir_el_sexo_en_espanol_se_entiende(app) -> None:
    """La gente escribe "mujer", no "female"."""
    assert _enviar(app, sex="mujer").status_code == 303


def test_un_sexo_que_no_se_reconoce_se_dice(app) -> None:
    assert "Sexo no válido" in _enviar(app, sex="qwerty").text


def test_una_edad_imposible_no_entra(app) -> None:
    """Sin esto el cálculo de calorías devolvería un número sin sentido."""
    resp = _enviar(app, age_years=500)
    assert resp.status_code == 200
    assert "error" in resp.text.lower() or "válid" in resp.text.lower()


def test_un_alimento_marcado_que_no_es_un_id_se_ignora_sin_romper(app) -> None:
    assert _enviar(app, food_ids=["no-soy-un-uuid", ""]).status_code == 303


def test_sin_marcar_ninguna_comida_igual_se_come_lo_principal(app, container) -> None:
    """Nadie debería quedarse sin desayuno por no tocar un checkbox."""
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import ClientRow

    assert _enviar(app, meal_slots=[]).status_code == 303

    async def _comidas() -> list[str]:
        async with container.session_factory() as session:
            fila = await session.execute(select(ClientRow.meal_slots))
            return list(fila.scalars().one())

    comidas = asyncio.run(_comidas())
    assert "desayuno" in comidas
    assert "almuerzo" in comidas
    assert "cena" in comidas


def test_lo_que_no_quiere_comer_se_separa_por_comas_o_por_lineas(app, container) -> None:
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import ClientRow

    _enviar(app, dislikes="hígado, coliflor\nremolacha")

    async def _dislikes() -> list[str]:
        async with container.session_factory() as session:
            fila = await session.execute(select(ClientRow.dislikes))
            return list(fila.scalars().one())

    assert len(asyncio.run(_dislikes())) == 3


def test_el_nombre_que_pone_aqui_pasa_a_su_cuenta(app, container) -> None:
    """Si lo corrige aquí, se corrige donde de verdad vive."""
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import UserRow

    _enviar(app, name="Ana María")

    async def _nombre() -> str:
        async with container.session_factory() as session:
            fila = await session.execute(
                select(UserRow.name).where(UserRow.email == "ana@correo.com")
            )
            return str(fila.scalars().one())

    assert asyncio.run(_nombre()) == "Ana María"


def test_si_pone_sus_macros_en_el_alta_quedan_guardados(app) -> None:
    resp = _enviar(
        app,
        conoce_macros="si",
        kcal="2000",
        protein_g="140",
        carb_g="180",
        fat_g="80",
    )
    assert resp.status_code == 303
    perfil = app.get("/perfil").text
    assert "Ajustar mis números" in perfil
    assert "2000" in perfil


def test_unos_macros_imposibles_en_el_alta_no_repiten_el_onboarding(app) -> None:
    """El perfil ya existe: el error se explica en /perfil, no se borra el alta."""
    resp = _enviar(
        app,
        conoce_macros="si",
        kcal="800",
        protein_g="10",
        carb_g="10",
        fat_g="5",
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/perfil")
