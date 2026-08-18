"""Cena: derivada como inclinação da reta tangente (post 001)."""

from manim import *
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config_reels import BG_COLOR, ACCENT, ACCENT2, TEXT_COLOR


class DerivadaTangente(Scene):
    def construct(self):
        self.camera.background_color = BG_COLOR

        titulo = Text("O que a derivada mede?", font_size=42, color=TEXT_COLOR)
        titulo.to_edge(UP, buff=0.8)
        self.play(FadeIn(titulo))

        axes = Axes(
            x_range=[-1, 3, 1],
            y_range=[-1, 9, 2],
            x_length=7,
            y_length=8,
            axis_config={"color": GREY_B, "include_numbers": True},
        ).shift(DOWN * 0.3)

        graph = axes.plot(lambda x: x**2, x_range=[-0.8, 2.8], color=ACCENT)
        label = MathTex(r"f(x) = x^2", color=ACCENT).next_to(axes, UP, buff=0.2)

        self.play(Create(axes), Create(graph), Write(label))

        x0 = 1.0
        ponto = Dot(axes.c2p(x0, x0**2), color=ACCENT2, radius=0.1)
        p_label = MathTex(r"P", color=ACCENT2, font_size=36).next_to(ponto, UR, buff=0.1)

        self.play(FadeIn(ponto), Write(p_label))

        h_tracker = ValueTracker(1.0)

        def secante():
            h = h_tracker.get_value()
            x1 = x0 + h
            p1 = axes.c2p(x0, x0**2)
            p2 = axes.c2p(x1, x1**2)
            return Line(p1, p2, color=RED, stroke_width=3)

        sec = always_redraw(secante)
        self.play(Create(sec))

        tangente = axes.plot(
            lambda x: 2 * x0 * (x - x0) + x0**2,
            x_range=[-0.2, 2.2],
            color=ACCENT2,
            stroke_width=4,
        )

        self.play(
            h_tracker.animate.set_value(0.01),
            run_time=3,
            rate_func=smooth,
        )
        self.play(Transform(sec, tangente))

        formula = MathTex(
            r"f'(x) = \lim_{h \to 0} \frac{f(x+h) - f(x)}{h}",
            color=TEXT_COLOR,
            font_size=32,
        ).to_edge(DOWN, buff=1.0)
        self.play(Write(formula))

        resultado = MathTex(r"f'(1) = 2", color=ACCENT2, font_size=40).next_to(formula, UP)
        self.play(FadeIn(resultado))
        self.wait(2)
