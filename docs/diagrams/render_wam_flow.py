"""Render the location-aware spec using the Lanshu skill's drawing/export engine."""

import argparse
import importlib.util
import math
from pathlib import Path
import sys


def install_layout(renderer):
    scene = {}
    theme = renderer.THEME

    def text(ex, draw, content, box, size=19, color=None, bold=False):
        x, y, width, height = box
        fitted, actual, _ = renderer.fit_text(
            draw, content, width, height, size, min_size=18, bold=bold
        )
        if actual < 18:
            raise ValueError(f"Rewrite cramped diagram text: {content!r}")
        renderer.draw_text(
            ex, draw, fitted, x, y, width, height, actual,
            color=color, bold=bold,
        )

    def card(ex, draw, item):
        x, y, width, height = item["box"]
        renderer.draw_rect(
            ex, draw, x, y, width, height, item["color"],
            theme["blue_fill"], radius=12,
        )
        text(
            ex, draw, item["title"], [x + 12, y + 8, width - 24, 32],
            item.get("title_size", 24), item["color"], True,
        )
        text(ex, draw, item["body"], [x + 12, y + 44, width - 24, height - 52])

    def render_static(spec):
        scene.update(spec)
        width, height = spec["canvas"]["width"], spec["canvas"]["height"]
        ex = renderer.Excal(width, height)
        image = renderer.Image.new(
            "RGBA", (renderer.c(width), renderer.c(height)), theme["bg"]
        )
        draw = renderer.ImageDraw.Draw(image)
        renderer.draw_rect(ex, draw, 10, 10, width - 20, height - 20, theme["frame"])
        text(ex, draw, spec["title"], [30, 30, 590, 55], 42, bold=True)
        renderer.draw_rect(
            ex, draw, 640, 25, 620, 65, theme["highlight"],
            theme["highlight"], radius=18,
        )
        text(ex, draw, spec["highlight"], [650, 30, 600, 55], 42, theme["green"], True)
        text(ex, draw, "@WAM", [1330, 35, 210, 45], 28, theme["cyan"], True)
        text(ex, draw, spec["subtitle"], [30, 100, width - 60, 30])
        for panel in spec["panels"]:
            x, y, w, h = panel["box"]
            renderer.draw_rect(ex, draw, x, y, w, h, panel["color"], panel["fill"])
            text(ex, draw, panel["title"], [x + 12, y + 10, w - 24, 34], 24, panel["color"], True)
        for item in spec["nodes"] + spec["notes"]:
            card(ex, draw, item)
        for edge in spec["edges"]:
            renderer.draw_line(
                ex, draw, edge["points"], edge.get("color", theme["white"]),
                style=edge.get("style", "solid"), arrow=True,
            )
        for label in spec["labels"]:
            text(ex, draw, label["text"], label["box"], 18, label.get("color"))
        return ex, image.resize((width, height), renderer.Image.Resampling.LANCZOS)

    def animate_frame(base, idx, total):
        frame = base.convert("RGBA")
        overlay = renderer.Image.new("RGBA", frame.size, (0, 0, 0, 0))
        draw = renderer.ImageDraw.Draw(overlay)
        for number, edge in enumerate(scene["edges"]):
            if not edge.get("animate"):
                continue
            # The cloud path is optional; alternate cached and network cycles.
            if edge.get("conditional") and idx < total // 2:
                continue
            point = renderer.point_at_fraction(edge["points"], idx / total + number * 0.09)
            renderer.draw_glow_dot(draw, *point, theme["cyan"])
        node = scene["nodes"][(idx // 10) % 3]
        x, y, width, height = node["box"]
        renderer.pulse_rect(draw, (x, y, x + width, y + height), theme["cyan"], idx / total * math.tau)
        frame.alpha_composite(overlay)
        return frame.convert("RGB")

    renderer.render_static = render_static
    renderer.animate_frame = animate_frame


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Remaining options go to the skill renderer: --spec --outdir --basename --verify --check.",
    )
    parser.add_argument("--renderer", required=True, type=Path, help="Path to the skill's render_animated_diagram.py.")
    args, remaining = parser.parse_known_args()
    module_spec = importlib.util.spec_from_file_location("lanshu_renderer", args.renderer)
    renderer = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(renderer)
    install_layout(renderer)
    sys.argv = [str(args.renderer), *remaining]
    renderer.main()


if __name__ == "__main__":
    main()
