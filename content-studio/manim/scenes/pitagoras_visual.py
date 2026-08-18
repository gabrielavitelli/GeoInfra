"""Cena: teorema de Pitágoras como soma de áreas (post 002)."""

from manim import *
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config_reels import BG_COLOR, ACCENT, ACCENT2, TEXT_COLOR


class PitagorasVisual(Scene):
    def construct(self):
        self.camera.background_color = BG_COLOR

        titulo = Text("Pitágoras = soma de áreas", font_size=40, color=TEXT_COLOR)
        titulo.to_edge(UP, buff=0.8)
        self.play(FadeIn(titulo))

        # Triângulo retângulo
        A = np.array([-1.5, -1.5, 0])
        B = np.array([1.5, -1.5, 0])
        C = np.array([-1.5, 1.0, 0])

        tri = Polygon(A, B, C, color=WHITE, stroke_width=3)
        tri.shift(DOWN * 0.5)

        a_label = MathTex("a", color=ACCENT).next_to(tri, LEFT, buff=0.2)
        b_label = MathTex("b", color=ACCENT2).next_to(tri, DOWN, buff=0.2)
        c_label = MathTex("c", color=RED).move_to(tri.get_center() + UR * 0.8)

        self.play(Create(tri), Write(a_label), Write(b_label), Write(c_label))

        # Quadrados nos catetos (escala visual)
        sq_a = Square(side_length=2.5, color=ACCENT, fill_opacity=0.35, stroke_width=2)
        sq_a.next_to(tri, LEFT, buff=0.05)
        sq_b = Square(side_length=3.0, color=ACCENT2, fill_opacity=0.35, stroke_width=2)
        sq_b.next_to(tri, DOWN, buff=0.05)

        self.play(FadeIn(sq_a), FadeIn(sq_b))
        self.wait(0.5)

        area_a = MathTex(r"a^2", color=ACCENT, font_size=36).move_to(sq_a)
        area_b = MathTex(r"b^2", color=ACCENT2, font_size=36).move_to(sq_b)
        self.play(Write(area_a), Write(area_b))

        # Quadrado na hipotenusa
        sq_c = Square(side_length=3.8, color=RED, fill_opacity=0.35, stroke_width=2)
        sq_c.rotate(np.arctan2(2.5, 3.0))
        sq_c.move_to(tri.get_center() + UR * 2.2)

        self.play(FadeIn(sq_c))
        area_c = MathTex(r"c^2", color=RED, font_size=36).move_to(sq_c)
        self.play(Write(area_c))

        formula = MathTex(r"a^2 + b^2 = c^2", color=TEXT_COLOR, font_size=48)
        formula.to_edge(DOWN, buff=1.2)
        self.play(Write(formula))
        self.wait(2)
