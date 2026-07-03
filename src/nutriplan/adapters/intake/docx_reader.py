"""Lectura de .docx de intake → texto crudo (mammoth)."""

from io import BytesIO

import mammoth

from nutriplan.domain.errors import IntakeAmbiguityError


def read_docx_text(file_bytes: bytes) -> str:
    try:
        result = mammoth.extract_raw_text(BytesIO(file_bytes))
    except Exception as exc:
        raise IntakeAmbiguityError([f"archivo .docx ilegible: {exc}"]) from exc
    text = (result.value or "").strip()
    if not text:
        raise IntakeAmbiguityError(["el documento no contiene texto"])
    return text
