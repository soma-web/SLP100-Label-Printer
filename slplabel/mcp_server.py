"""MCP server: lets an AI assistant design, preview and print labels."""

from typing import Literal

from anyio import to_thread
from mcp.server import MCPServer
from mcp.server.mcpserver import Image

from . import __version__, render
from .frames import FRAMES
from .render import LabelSpec
from .service import Service

Frame = Literal["none", *FRAMES]
INSTRUCTIONS = """Prints labels on a SII Smart Label Printer 100 (black/white thermal, 203 dpi).
Labels are 24 mm high; text runs along the length. The usual label is 83 mm long.
Call preview_label before print_label and look at the picture: printing uses up real
labels, so only print what the user asked for."""


def build_mcp(service: Service) -> MCPServer:
    mcp = MCPServer("slp-label-studio", title="SLP Label Studio", version=__version__,
                    instructions=INSTRUCTIONS)
    run = to_thread.run_sync

    def make_spec(lines, icon, qr, font, bold, size_pt, align, frame, invert, length_mm, margin_mm,
                  graphic_pos, graphic_scale) -> LabelSpec:
        return LabelSpec(lines=lines or [], icon=icon, qr=qr, font=font, bold=bold, size_pt=size_pt,
                         align=align, frame=frame, invert=invert, length_mm=length_mm,
                         margin_mm=margin_mm, graphic_pos=graphic_pos, graphic_scale=graphic_scale)

    def preview(spec: LabelSpec):
        img, warnings = service.render(spec)
        text = f"{render.dots_to_mm(img.width):.0f} x 24 mm. " + " ".join(warnings)
        return [text.strip(), Image(data=render.png_bytes(img), format="png")]

    @mcp.tool()
    async def get_printer_status() -> dict:
        """Check whether the label printer is connected and ready."""
        return await run(service.printer.status)

    @mcp.tool()
    async def list_resources_for_labels() -> dict:
        """List the available templates, icons (by category) and fonts."""
        icons = {}
        for i in service.icon_list():
            icons.setdefault(i["category"], []).append(i["name"])
        return {"templates": sorted(service.templates()), "icons": icons,
                "fonts": render.available_fonts(),
                "label_sizes_mm": [{"name": n, "height": w, "length": ln} for n, w, ln in render.MEDIA]}

    @mcp.tool(structured_output=False)
    async def preview_label(
        lines: list[str] | None = None, icon: str = "", qr: str = "", font: str = "", bold: bool = True,
        size_pt: float = 0, align: Literal["left", "center", "right"] = "center", frame: Frame = "none",
        invert: bool = False, length_mm: float = 83, margin_mm: float = 2,
        graphic_pos: Literal["left", "right"] = "left", graphic_scale: float = 0.9,
    ) -> list:
        """Render a label and return it as a picture without printing.

        lines: text lines. icon: name of an icon. qr: QR code content (wins over icon).
        size_pt 0 = largest size that fits. length_mm 0 = as long as the content.
        frame: border style around the label, e.g. rounded, dashed, stars, hazard.
        graphic_scale: height of icon/QR code as a fraction of the label (0.3-1.0).
        """
        spec = make_spec(lines, icon, qr, font, bold, size_pt, align, frame, invert, length_mm,
                         margin_mm, graphic_pos, graphic_scale)
        return await run(preview, spec)

    @mcp.tool()
    async def print_label(
        lines: list[str] | None = None, icon: str = "", qr: str = "", font: str = "", bold: bool = True,
        size_pt: float = 0, align: Literal["left", "center", "right"] = "center", frame: Frame = "none",
        invert: bool = False, length_mm: float = 83, margin_mm: float = 2,
        graphic_pos: Literal["left", "right"] = "left", graphic_scale: float = 0.9, copies: int = 1,
    ) -> dict:
        """Print a label on paper. Parameters as in preview_label; preview first."""
        spec = make_spec(lines, icon, qr, font, bold, size_pt, align, frame, invert, length_mm,
                         margin_mm, graphic_pos, graphic_scale)
        return await run(lambda: service.print_label(spec, copies, limit=service.config.mcp_max_copies))

    @mcp.tool(structured_output=False)
    async def preview_template(name: str) -> list:
        """Show a saved template as a picture without printing."""
        return await run(lambda: preview(service.get_template(name)))

    @mcp.tool()
    async def print_template(name: str, copies: int = 1) -> dict:
        """Print a saved template on paper."""
        return await run(lambda: service.print_label(service.get_template(name), copies,
                                                     limit=service.config.mcp_max_copies))

    @mcp.tool()
    async def save_template(
        name: str, lines: list[str] | None = None, icon: str = "", qr: str = "", font: str = "",
        bold: bool = True, size_pt: float = 0, align: Literal["left", "center", "right"] = "center",
        frame: Frame = "none", invert: bool = False, length_mm: float = 83, margin_mm: float = 2,
        graphic_pos: Literal["left", "right"] = "left", graphic_scale: float = 0.9,
    ) -> dict:
        """Save a label as a new template. Existing templates are not overwritten."""
        spec = make_spec(lines, icon, qr, font, bold, size_pt, align, frame, invert, length_mm,
                         margin_mm, graphic_pos, graphic_scale)
        return await run(lambda: service.save_templates({name: spec.model_dump()}, overwrite=False))

    return mcp
