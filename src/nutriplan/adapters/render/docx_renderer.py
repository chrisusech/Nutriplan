"""Renderer DOCX editable (python-docx) detrás del mismo puerto Renderer."""

from io import BytesIO
from uuid import UUID

from nutriplan.adapters.render.view import (
    ANOTACIONES_IMPORTANTES,
    DAY_LABELS,
    PHASE_LABELS,
    SLOT_LABELS,
    build_grid,
)
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import Branding, FoodItem, PlanCycle


class DocxRenderer:
    """Implementa el puerto Renderer para fmt='docx'."""

    async def render(
        self,
        plan: PlanCycle,
        branding: Branding,
        foods: dict[UUID, FoodItem],
        fmt: str = "docx",
    ) -> bytes:
        if fmt != "docx":
            raise RenderError(f"DocxRenderer solo produce docx, no {fmt}")
        try:
            return self._build(plan, branding, foods)
        except RenderError:
            raise
        except Exception as exc:
            raise RenderError(f"python-docx falló: {exc}") from exc

    def _build(self, plan: PlanCycle, branding: Branding, foods: dict[UUID, FoodItem]) -> bytes:
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.shared import Pt

        doc = Document()
        section = doc.sections[0]
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

        doc.add_heading(f"{branding.tenant_name} — Plan nutricional", level=1)
        subtitle = PHASE_LABELS[plan.phase]
        if branding.handle:
            subtitle += f" · {branding.handle}"
        doc.add_paragraph(subtitle)

        grid = build_grid(plan, foods)
        table = doc.add_table(rows=1 + len(SLOT_LABELS), cols=8)
        table.style = "Table Grid"

        header = table.rows[0].cells
        header[0].text = ""
        for i, day in enumerate(DAY_LABELS, start=1):
            header[i].text = day

        for r, (slot, cells) in enumerate(grid.items(), start=1):
            row = table.rows[r].cells
            row[0].text = SLOT_LABELS[slot]
            for c, cell in enumerate(cells, start=1):
                lines = [p.text for p in cell.portions] + cell.extras
                row[c].text = "\n".join(lines)

        doc.add_heading("Anotaciones Importantes", level=2)
        for nota in ANOTACIONES_IMPORTANTES:
            doc.add_paragraph(nota, style="List Number")

        prov = doc.add_paragraph(
            f"Config {plan.config_version} · Prompt {plan.prompt_version} · "
            f"Modelo {plan.model} · Hash {plan.input_hash[:12]}"
        )
        prov.runs[0].font.size = Pt(6)

        buffer = BytesIO()
        doc.save(buffer)
        return buffer.getvalue()
