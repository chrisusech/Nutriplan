"""Tests de humo del andamiaje: settings, logging, container y DB."""

from sqlalchemy import text

from nutriplan.config.settings import Environment, Settings
from nutriplan.container import Container


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.env == Environment.LOCAL
    assert settings.database_url.startswith("sqlite+aiosqlite")
    assert settings.llm_model_ingest
    assert settings.llm_model_generate


def test_container_builds() -> None:
    container = Container(settings=Settings(_env_file=None))
    assert container.settings.env == Environment.LOCAL
    assert container.session_factory is not None


async def test_db_session_roundtrip(tmp_path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db")
    container = Container(settings=settings)
    async with container.session_factory() as session:
        result = await session.execute(text("select 1"))
        assert result.scalar_one() == 1
    await container.engine.dispose()
