"""El perfil de gusto: por qué la semana 6 se parece más a quien la come.

Las notas son aritmética pura; los comentarios los lee la IA con un enum
cerrado. Aquí se prueban las dos mitades y, sobre todo, que sin IA la semana se
cierra igual — perder matiz no puede costar el cierre.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

from nutriplan.application.taste_profile import (
    build_taste_prompt,
    build_taste_schema,
    extract_taste_signals,
    taste_profile_for,
)
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    FoodItem,
    Goal,
    Sex,
    UnitGranularity,
)
from nutriplan.domain.taste import RatedDish, build_taste_profile

PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
SEMANA = date(2026, 8, 10)


def _client() -> Client:
    return Client(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Ana",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )


def _food(nombre: str) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="USDA",
        name_es=nombre,
        category=FoodCategory.PROTEIN,
        kcal_100g=100.0,
        protein_100g=20.0,
        carb_100g=0.0,
        fat_100g=2.0,
        unit_granularity=UnitGranularity.GRAMS,
    )


class _Ratings:
    def __init__(
        self,
        dishes: list[RatedDish] | None = None,
        comments: list[tuple[str, int, str]] | None = None,
    ) -> None:
        self.dishes = dishes or []
        self.comments = comments or []

    async def rated_dishes(self, *, limit: int = 200) -> list[RatedDish]:
        return self.dishes

    async def comments_for_plan(self, plan_cycle_id: UUID) -> list[tuple[str, int, str]]:
        return self.comments


class _Signals:
    def __init__(self, avoid: list[UUID] | None = None) -> None:
        self.avoid_food_ids = avoid or []
        self.prefer_food_ids: list[UUID] = []
        self.adjustments: list[str] = ["menos fritos"]
        self.guardado: dict[str, object] | None = None

    async def accumulated_for(self, client_id: UUID, *, weeks: int = 8) -> _Signals:
        return self

    async def upsert(self, **kwargs: object) -> None:
        self.guardado = kwargs


class _LLM:
    def __init__(self, respuesta: dict[str, object] | Exception) -> None:
        self.respuesta = respuesta
        self.llamadas = 0

    async def extract(self, *, system: str, text: str, schema: type, model: str):  # type: ignore[no-untyped-def]
        self.llamadas += 1
        if isinstance(self.respuesta, Exception):
            raise self.respuesta
        return schema.model_validate(self.respuesta)

    async def select_plan(self, **kwargs: object):  # type: ignore[no-untyped-def]
        raise NotImplementedError


# --- Lo que dicen las notas -------------------------------------------------


def test_un_plato_con_cinco_estrellas_se_puede_repetir() -> None:
    perfil = build_taste_profile([RatedDish("t1", "k1", "Pollo al limón", 5)])
    assert perfil.loved_dishes == ["Pollo al limón"]
    assert perfil.rejected_dishes == []


def test_un_plato_odiado_una_sola_vez_todavia_no_se_veta() -> None:
    """Pudo ser un mal día, no el plato."""
    perfil = build_taste_profile([RatedDish("t1", "k1", "Tilapia", 1)])
    assert perfil.rejected_dishes == []


def test_un_plato_odiado_dos_veces_no_se_vuelve_a_proponer() -> None:
    perfil = build_taste_profile(
        [RatedDish("t1", "k1", "Tilapia", 1), RatedDish("t1", "k1", "Tilapia", 2)]
    )
    assert perfil.rejected_dishes == ["Tilapia"]


def test_un_perfil_sin_nada_no_ocupa_sitio_en_el_prompt() -> None:
    assert build_taste_profile([]).is_empty


def test_dos_gustos_distintos_dan_huellas_distintas() -> None:
    """Sin esto, quien pidió «no más pescado» recibiría el mismo menú del caché."""
    con_veto = build_taste_profile([], avoid_food_ids=[uuid4()])
    assert con_veto.fingerprint() != build_taste_profile([]).fingerprint()


def test_la_huella_no_depende_del_orden() -> None:
    a, b = uuid4(), uuid4()
    assert (
        build_taste_profile([], avoid_food_ids=[a, b]).fingerprint()
        == build_taste_profile([], avoid_food_ids=[b, a]).fingerprint()
    )


async def test_el_perfil_junta_las_notas_con_lo_leido_en_los_comentarios() -> None:
    vetado = uuid4()
    perfil = await taste_profile_for(
        client=_client(),
        ratings=_Ratings([RatedDish("t1", "k1", "Pollo al limón", 5)]),
        signals=_Signals(avoid=[vetado]),
    )
    assert perfil.loved_dishes == ["Pollo al limón"]
    assert perfil.avoid_food_ids == [vetado]
    assert perfil.adjustments == ["menos fritos"]


# --- Lo que dice la prosa ---------------------------------------------------


def test_la_ia_solo_puede_nombrar_alimentos_que_existen() -> None:
    """El tipo impone la regla: el enum no la deja inventar un alimento."""
    esquema = build_taste_schema([_food("pechuga de pollo")])
    campos = esquema.model_fields
    assert set(campos) == {"avoid", "prefer", "adjustments"}
    assert "f0" in str(campos["avoid"].annotation)


def test_el_prompt_le_muestra_lo_que_escribio_y_el_catalogo() -> None:
    texto = build_taste_prompt(
        weekly_comment="Comí mucho pollo, ya no lo quiero ver",
        dish_comments=[("Pollo al limón", 2, "aburrido")],
        allowed=[_food("pechuga de pollo")],
    )
    assert "ya no lo quiero ver" in texto
    assert "Pollo al limón (nota 2/5): aburrido" in texto
    assert "pechuga de pollo" in texto


async def test_la_ia_traduce_el_comentario_a_alimentos_del_catalogo() -> None:
    pollo = _food("pechuga de pollo")
    señales = _Signals()
    hecho = await extract_taste_signals(
        client=_client(),
        plan_cycle_id=uuid4(),
        week_start=SEMANA,
        weekly_comment="No quiero volver a ver pollo",
        allowed=[pollo],
        ratings=_Ratings(),
        store=señales,
        llm=_LLM({"avoid": ["f0"], "prefer": [], "adjustments": ["menos fritos"]}),
        prompts_dir=PROMPTS,
        model="modelo-de-prueba",
    )

    assert hecho
    assert señales.guardado is not None
    assert señales.guardado["avoid_food_ids"] == [pollo.id]
    assert señales.guardado["adjustments"] == ["menos fritos"]


async def test_sin_ia_la_semana_se_cierra_igual() -> None:
    """El modo offline no puede dejar a nadie sin poder cerrar su semana."""
    señales = _Signals()
    hecho = await extract_taste_signals(
        client=_client(),
        plan_cycle_id=uuid4(),
        week_start=SEMANA,
        weekly_comment="Me fue bien",
        allowed=[_food("pechuga de pollo")],
        ratings=_Ratings(),
        store=señales,
        llm=None,
        prompts_dir=PROMPTS,
        model="modelo-de-prueba",
    )
    assert hecho is False
    assert señales.guardado is None


async def test_si_la_ia_falla_no_se_pierde_el_cierre() -> None:
    señales = _Signals()
    hecho = await extract_taste_signals(
        client=_client(),
        plan_cycle_id=uuid4(),
        week_start=SEMANA,
        weekly_comment="Me fue bien",
        allowed=[_food("pechuga de pollo")],
        ratings=_Ratings(),
        store=señales,
        llm=_LLM(LLMError("sin cuota")),
        prompts_dir=PROMPTS,
        model="modelo-de-prueba",
    )
    assert hecho is False
    assert señales.guardado is None


async def test_quien_no_escribio_nada_no_gasta_una_llamada_a_la_ia() -> None:
    llm = _LLM({"avoid": [], "prefer": [], "adjustments": []})
    hecho = await extract_taste_signals(
        client=_client(),
        plan_cycle_id=None,
        week_start=SEMANA,
        weekly_comment="   ",
        allowed=[_food("pechuga de pollo")],
        ratings=_Ratings(),
        store=_Signals(),
        llm=llm,
        prompts_dir=PROMPTS,
        model="modelo-de-prueba",
    )
    assert hecho is False
    assert llm.llamadas == 0
