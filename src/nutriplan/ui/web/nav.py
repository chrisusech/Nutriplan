"""Rutas internas que un formulario puede devolver sin abrir la puerta a phishing."""


def safe_return(raw: str | None, *, fallback: str = "/") -> str:
    """Solo paths de esta app. `//evil` o `https:` no pasan."""
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return fallback
    if any(ch in raw for ch in (":", "\\", "\n", "\r", "\0")):
        return fallback
    return raw
