"""Generate the architecture diagrams as Mermaid, SVG and PNG."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch


def main() -> None:
    """Render all the diagrams from the same shared graph definitions."""
    root = Path(__file__).resolve().parents[1]
    directory = root / "docs/architecture/diagrams"
    rendered = directory / "rendered"
    rendered.mkdir(exist_ok=True)
    definitions = json.loads((directory / "definitions.json").read_text(encoding="utf-8"))
    for name, definition in definitions.items():
        mermaid = ["flowchart TB"]
        nodes = {node["id"]: node for node in definition["nodes"]}
        for node in nodes.values():
            label = json.dumps(node["label"].replace("\n", "<br/>"), ensure_ascii=False)
            mermaid.append(f'  {node["id"]}[{label}]')
        for edge in definition["edges"]:
            mermaid.append(f"  {edge[0]} --> {edge[1]}")
        (directory / f"{name}.mmd").write_text("\n".join(mermaid) + "\n", encoding="utf-8")
        figure, axis = plt.subplots(figsize=definition.get("size", [12, 10]))
        for edge in definition["edges"]:
            first, last = nodes[edge[0]], nodes[edge[1]]
            x1, y1 = first["at"]
            x2, y2 = last["at"]
            if x1 == x2:
                start, finish = (x1, y1 - 0.34), (x2, y2 + 0.34)
            else:
                direction = 1 if x2 > x1 else -1
                start, finish = (x1 + direction * 1.6, y1), (x2 - direction * 1.6, y2)
            axis.add_patch(
                FancyArrowPatch(
                    start,
                    finish,
                    arrowstyle="-|>",
                    mutation_scale=14,
                    color="#527083",
                    linewidth=1.4,
                    connectionstyle=f"arc3,rad={edge[2] if len(edge)>2 else 0}",
                )
            )
        for node in nodes.values():
            x, y = node["at"]
            axis.add_patch(
                FancyBboxPatch(
                    (x - 1.6, y - 0.32),
                    3.2,
                    0.64,
                    boxstyle="round,pad=0.04,rounding_size=0.08",
                    edgecolor="#087f8c",
                    facecolor="#edf7f6",
                    linewidth=1.3,
                )
            )
            axis.text(
                x,
                y,
                node["label"],
                ha="center",
                va="center",
                fontsize=10,
                color="#17354a",
                linespacing=1.35,
            )
        xs = [n["at"][0] for n in nodes.values()]
        ys = [n["at"][1] for n in nodes.values()]
        axis.set_xlim(min(xs) - 2.0, max(xs) + 2.0)
        axis.set_ylim(min(ys) - 0.65, max(ys) + 0.9)
        axis.set_title(definition["title"], fontsize=15, color="#17354a", pad=12)
        axis.axis("off")
        figure.tight_layout()
        figure.savefig(rendered / f"{name}.svg", metadata={"Date": None})
        figure.savefig(rendered / f"{name}.png", dpi=150)
        plt.close(figure)
        print(f"Rendered {name}: Mermaid + SVG + PNG")


if __name__ == "__main__":
    main()
