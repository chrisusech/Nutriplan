"""El job como semáforo: lo que anuncia tiene que ser cierto para quien lo lee."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.ports.job_repository import Job, JobStatus


class _SinkQueRegistra:
    """Anota el orden de los anuncios, que es justo lo que se prueba."""

    def __init__(self) -> None:
        self.pasos: list[str] = []

    async def update(self, job: Job) -> None:
        self.pasos.append(job.status.value)


class _ClienteVacio:
    async def get(self, client_id: object) -> None:
        return None


@pytest.fixture
def job() -> Job:
    return new_job(tenant_id=uuid4(), idempotency_key="k")


async def _correr(job: Job, sink: _SinkQueRegistra, **extra: object) -> Job:
    return await run_generation_job(
        job=job, job_repo=sink, client_id=uuid4(),
        client_repo=_ClienteVacio(),  # type: ignore[arg-type]
        targets_repo=None, food_repo=None, plan_repo=None,  # type: ignore[arg-type]
        config=None, llm=None, prompts_dir=Path("."), model="m",  # type: ignore[arg-type]
        **extra,  # type: ignore[arg-type]
    )


async def test_el_job_avisa_que_empezo_antes_de_terminar(job) -> None:
    """Sin esto nadie sabe que hay algo en marcha hasta que ya acabó."""
    sink = _SinkQueRegistra()
    await _correr(job, sink)
    assert sink.pasos[0] == JobStatus.RUNNING.value


async def test_si_la_generacion_falla_el_job_lo_dice_y_no_revienta(job) -> None:
    sink = _SinkQueRegistra()
    resultado = await _correr(job, sink)
    assert resultado.status is JobStatus.FAILED
    assert "no existe" in (resultado.error or "")


async def test_un_fallo_no_confirma_nada(job) -> None:
    """El plan a medias no se guarda: si falló, no hay nada que leer."""
    confirmaciones: list[datetime] = []

    async def _commit() -> None:
        confirmaciones.append(datetime.now(UTC))

    await _correr(job, _SinkQueRegistra(), commit=_commit)
    assert confirmaciones == []


# --- Jobs que se quedaron a medias ------------------------------------------


def _corriendo(minutos: int) -> Job:
    from datetime import timedelta

    hace = datetime.now(UTC) - timedelta(minutes=minutos)
    return Job(
        id=uuid4(), tenant_id=uuid4(), status=JobStatus.RUNNING,
        idempotency_key="k", created_at=hace, updated_at=hace,
    )


def test_un_menu_que_lleva_generandose_media_hora_se_da_por_muerto() -> None:
    """Si el proceso se cayó a mitad, nadie va a tocar esa fila nunca más.
    Sin esto la persona se queda mirando la rueda para siempre."""
    from nutriplan.application.jobs import is_stale

    assert is_stale(_corriendo(30))


def test_un_menu_que_acaba_de_arrancar_no_se_da_por_muerto() -> None:
    from nutriplan.application.jobs import is_stale

    assert not is_stale(_corriendo(1))


def test_un_menu_terminado_nunca_se_da_por_muerto() -> None:
    """`done` es definitivo, por viejo que sea."""
    from datetime import timedelta

    from nutriplan.application.jobs import is_stale

    viejo = _corriendo(500).model_copy(update={"status": JobStatus.DONE})
    assert not is_stale(viejo, now=datetime.now(UTC) + timedelta(days=1))
