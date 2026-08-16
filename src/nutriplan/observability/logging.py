"""Logging estructurado (structlog, JSON).

Regla de la spec (sección 18): sin PII en logs — nombres y medidas se
referencian por id. Cada línea lleva correlation_id / tenant_id / client_id /
job_id cuando se bind()ean al contexto.
"""

import logging

import structlog


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(message)s")
    # httpx a INFO imprime la URL completa. Un GET a tokeninfo con el JWT en
    # query (o un POST mal logueado) dejaría el id_token en la consola.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
