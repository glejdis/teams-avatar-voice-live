#!/usr/bin/env python3
"""Render a draw.io (mxGraph) diagram to a standalone SVG.

This is a lightweight, dependency-free converter used when the draw.io
CLI / desktop app is not available. It understands the subset of mxGraph
used by ``docs/diagrams/architecture.drawio``:

* rounded / plain rectangles with fill, stroke, dashed, font colour and
  bold styling, plus multi-line (``&#xa;`` / ``<br>``) labels;
* plain text labels (``text;html=1;...`` with no fill/stroke);
* orthogonal edges with optional waypoints, arrow heads and mid-point
  labels with a white background.

Usage::

    python scripts/drawio_to_svg.py [input.drawio] [output.svg]

Defaults to ``docs/diagrams/architecture.drawio`` ->
``docs/diagrams/architecture.svg`` relative to the repo root.
"""
from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

PAD = 24  # padding around the drawing, in user units
DEFAULT_FONT = 12
FONT_FAMILY = "Segoe UI, Helvetica, Arial, sans-serif"


def parse_style(style: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not style:
        return out
    for part in style.split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        else:
            out[part] = "1"
    return out


_TAG_RE = re.compile(r"<[^>]+>")


def value_to_lines(value: str | None) -> list[str]:
    """Turn an mxCell label into plain-text lines."""
    if not value:
        return []
    # normalise the various line-break encodings to \n
    text = value.replace("\r", "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)          # drop any remaining html tags
    text = html.unescape(text)            # &amp; &nbsp; &#xa; ...
    text = text.replace("\xa0", " ")
    lines = [ln.rstrip() for ln in text.split("\n")]
    # collapse leading/trailing blank lines
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class Cell:
    def __init__(self, el: ET.Element):
        self.id = el.get("id", "")
        self.value = el.get("value")
        self.style = parse_style(el.get("style"))
        self.is_vertex = el.get("vertex") == "1"
        self.is_edge = el.get("edge") == "1"
        self.source = el.get("source")
        self.target = el.get("target")
        self.x = self.y = self.w = self.h = 0.0
        self.points: list[tuple[float, float]] = []

        geo = el.find("mxGeometry")
        if geo is not None:
            self.x = float(geo.get("x", 0) or 0)
            self.y = float(geo.get("y", 0) or 0)
            self.w = float(geo.get("width", 0) or 0)
            self.h = float(geo.get("height", 0) or 0)
            arr = geo.find("./Array[@as='points']")
            if arr is not None:
                for p in arr.findall("mxPoint"):
                    self.points.append(
                        (float(p.get("x", 0) or 0), float(p.get("y", 0) or 0))
                    )

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def clip_to_rect(cell: Cell, outside: tuple[float, float]) -> tuple[float, float]:
    """Point on ``cell``'s border along the line centre -> outside."""
    cx, cy = cell.cx, cell.cy
    ox, oy = outside
    dx, dy = ox - cx, oy - cy
    if dx == 0 and dy == 0:
        return cx, cy
    hw, hh = cell.w / 2, cell.h / 2
    # scale so the larger axis just reaches the border
    sx = hw / abs(dx) if dx else float("inf")
    sy = hh / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def build_edge_points(edge: Cell, cells: dict[str, Cell]) -> list[tuple[float, float]]:
    src = cells.get(edge.source or "")
    tgt = cells.get(edge.target or "")
    if src is None or tgt is None:
        return []
    way = list(edge.points)
    # anchor points used to clip the box ends
    first_out = way[0] if way else (tgt.cx, tgt.cy)
    last_out = way[-1] if way else (src.cx, src.cy)
    start = clip_to_rect(src, first_out)
    end = clip_to_rect(tgt, last_out)
    return [start, *way, end]


def render(input_path: Path, output_path: Path) -> None:
    tree = ET.parse(input_path)
    root = tree.getroot()
    model = root.find(".//mxGraphModel")
    if model is None:
        model = root
    cells = {}
    order: list[Cell] = []
    for el in model.iter("mxCell"):
        cell = Cell(el)
        if cell.id:
            cells[cell.id] = cell
        order.append(cell)

    vertices = [c for c in order if c.is_vertex and c.w and c.h]
    edges = [c for c in order if c.is_edge]

    # canvas bounds
    xs, ys = [], []
    for c in vertices:
        xs += [c.x, c.x + c.w]
        ys += [c.y, c.y + c.h]
    for e in edges:
        for px, py in build_edge_points(e, cells):
            xs.append(px)
            ys.append(py)
    if not xs:
        raise SystemExit("no drawable cells found")
    min_x, max_x = min(xs) - PAD, max(xs) + PAD
    min_y, max_y = min(ys) - PAD, max(ys) + PAD
    width = max_x - min_x
    height = max_y - min_y

    svg: list[str] = []
    svg.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="{min_x:.0f} {min_y:.0f} {width:.0f} {height:.0f}" '
        f'width="{width:.0f}" height="{height:.0f}" '
        f'font-family="{FONT_FAMILY}">'
    )
    svg.append(
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" '
        'refX="8" refY="3" orient="auto" markerUnits="strokeWidth">'
        '<path d="M0,0 L8,3 L0,6 Z" fill="context-stroke"/></marker></defs>'
    )
    svg.append(
        f'<rect x="{min_x:.0f}" y="{min_y:.0f}" width="{width:.0f}" '
        f'height="{height:.0f}" fill="#ffffff"/>'
    )

    # --- edges first, so boxes sit on top of the lines ---
    for e in edges:
        pts = build_edge_points(e, cells)
        if len(pts) < 2:
            continue
        stroke = e.style.get("strokeColor", "#333333")
        sw = e.style.get("strokeWidth", "1.5")
        dash = ' stroke-dasharray="6 4"' if e.style.get("dashed") == "1" else ""
        d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        svg.append(
            f'<path d="{d}" fill="none" stroke="{stroke}" '
            f'stroke-width="{sw}"{dash} marker-end="url(#arrow)"/>'
        )
        label = " ".join(value_to_lines(e.value))
        if label:
            mid = pts[len(pts) // 2]
            # midpoint of the middle segment reads better for 2-point edges
            if len(pts) == 2:
                mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
            tw = max(6 * len(label) + 8, 12)
            svg.append(
                f'<rect x="{mid[0] - tw / 2:.1f}" y="{mid[1] - 8:.1f}" '
                f'width="{tw:.1f}" height="16" fill="#ffffff" '
                f'fill-opacity="0.85" rx="2"/>'
            )
            svg.append(
                f'<text x="{mid[0]:.1f}" y="{mid[1] + 4:.1f}" font-size="10" '
                f'text-anchor="middle" fill="{stroke}">{xml_escape(label)}</text>'
            )

    # --- vertices ---
    for c in vertices:
        fill = c.style.get("fillColor", "none")
        if fill.lower() == "none":
            fill = "none"
        stroke = c.style.get("strokeColor", "#333333")
        is_text_only = "text" in c.style and fill == "none" and (
            "strokeColor" not in c.style
        )
        if not is_text_only:
            dash = ' stroke-dasharray="6 4"' if c.style.get("dashed") == "1" else ""
            rounded = c.style.get("rounded") == "1"
            arc = float(c.style.get("arcSize", 8) or 8)
            rx = min(c.w, c.h) * arc / 100 if rounded else 0
            rx = max(0, min(rx, 24))
            svg.append(
                f'<rect x="{c.x:.0f}" y="{c.y:.0f}" width="{c.w:.0f}" '
                f'height="{c.h:.0f}" rx="{rx:.0f}" ry="{rx:.0f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>'
            )

        lines = value_to_lines(c.value)
        if not lines:
            continue
        font_color = c.style.get("fontColor", "#222222")
        font_size = float(c.style.get("fontSize", DEFAULT_FONT) or DEFAULT_FONT)
        bold = (int(c.style.get("fontStyle", 0) or 0) & 1) == 1
        weight = ' font-weight="bold"' if bold else ""
        valign = c.style.get("verticalAlign", "middle")
        align = c.style.get("align", "center")
        anchor = {"left": "start", "right": "end"}.get(align, "middle")
        tx = {
            "start": c.x + 8,
            "end": c.x + c.w - 8,
        }.get(anchor, c.cx)

        line_h = font_size * 1.25
        block_h = line_h * len(lines)
        if valign == "top":
            first_y = c.y + 6 + font_size
        elif valign == "bottom":
            first_y = c.y + c.h - block_h + font_size
        else:
            first_y = c.cy - block_h / 2 + font_size
        for i, ln in enumerate(lines):
            ly = first_y + i * line_h
            fw = weight if i == 0 else ""
            fs = font_size if i == 0 else max(font_size - 1, 9)
            svg.append(
                f'<text x="{tx:.1f}" y="{ly:.1f}" font-size="{fs:.0f}" '
                f'text-anchor="{anchor}" fill="{font_color}"{fw}>'
                f'{xml_escape(ln)}</text>'
            )

    svg.append("</svg>")
    output_path.write_text("\n".join(svg), encoding="utf-8")
    print(f"wrote {output_path} ({len(vertices)} shapes, {len(edges)} edges)")


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else repo / "docs/diagrams/architecture.drawio"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else repo / "docs/diagrams/architecture.svg"
    render(src, dst)


if __name__ == "__main__":
    main()
