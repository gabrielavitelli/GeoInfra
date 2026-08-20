"""Gera GIFs geométricos, sem texto, de SVM e algoritmo genético."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Circle, Polygon
from PIL import Image

ROOT = Path(__file__).resolve().parent
BG = "#07080c"
PLUS = "#e8b84a"
MINUS = "#4f8bff"
INK = "#f3efe4"


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 0.5 - 0.5 * math.cos(math.pi * t)


def convex_hull(points: np.ndarray) -> np.ndarray:
    pts = np.unique(np.asarray(points, dtype=float), axis=0)
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[np.ndarray] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[np.ndarray] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = np.array(lower[:-1] + upper[:-1], dtype=float)
    return hull


def new_axes(size: float = 7.2):
    fig = plt.figure(figsize=(size, size), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.04, 0.04, 0.92, 0.92])
    ax.set_facecolor(BG)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def fig_to_image(fig) -> Image.Image:
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())
    return Image.fromarray(buf).convert("RGB")


def save_gif(frames: list[Image.Image], path: Path, duration_ms: int = 70) -> None:
    if not frames:
        raise ValueError("no frames")
    tmp = Path(tempfile.mkdtemp(prefix="gif_frames_"))
    try:
        for i, im in enumerate(frames):
            im.save(tmp / f"{i:04d}.png")
        fps = max(8.0, 1000.0 / duration_ms)
        palette = tmp / "palette.png"
        src = str(tmp / "%04d.png")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                f"{fps:.3f}",
                "-i",
                src,
                "-vf",
                "palettegen=max_colors=128:stats_mode=diff:reserve_transparent=0",
                str(palette),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-framerate",
                f"{fps:.3f}",
                "-i",
                src,
                "-i",
                str(palette),
                "-lavfi",
                "paletteuse=dither=bayer:bayer_scale=4",
                str(path),
            ],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        quantized = [im.convert("P", palette=Image.ADAPTIVE, colors=96) for im in frames]
        quantized[0].save(
            path,
            save_all=True,
            append_images=quantized[1:],
            duration=duration_ms,
            loop=0,
            optimize=True,
            disposal=2,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Support Vector Machine
# ---------------------------------------------------------------------------

def svm_points() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Duas classes linearmente separáveis; SVs ficam exatamente nas margens."""
    plus = np.array(
        [
            [1.05, 0.15],
            [0.18, 1.02],
            [1.55, 0.55],
            [1.95, 1.15],
            [2.25, 0.45],
            [1.35, 1.65],
            [2.15, 1.85],
            [2.55, 1.05],
            [1.75, 2.15],
            [0.85, 1.55],
            [2.45, 2.05],
        ]
    )
    minus = np.array(
        [
            [-0.60, -0.60],
            [-1.50, -0.55],
            [-1.90, -1.20],
            [-2.20, -0.40],
            [-1.35, -1.70],
            [-2.10, -1.90],
            [-2.55, -1.05],
            [-1.70, -2.20],
            [-0.85, -1.55],
            [-2.40, -2.10],
        ]
    )
    return plus, minus


def support_vectors(
    plus: np.ndarray, minus: np.ndarray, theta: float, tol: float = 0.025
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float]:
    """SVs = pontos que tocam as margens ±1 (projeções extremas em cada classe)."""
    gap, u, c_plus, c_minus = oriented_gap(plus, minus, theta)
    c_mid = 0.5 * (c_plus + c_minus)
    sv_plus = plus[np.abs(plus @ u - c_plus) <= tol]
    sv_minus = minus[np.abs(minus @ u - c_minus) <= tol]
    return sv_plus, sv_minus, u, c_plus, c_minus, c_mid


def oriented_gap(plus: np.ndarray, minus: np.ndarray, theta: float) -> tuple[float, np.ndarray, float, float]:
    u = np.array([math.cos(theta), math.sin(theta)])
    p = plus @ u
    m = minus @ u
    gap = float(p.min() - m.max())
    return gap, u, float(p.min()), float(m.max())


def best_theta(plus: np.ndarray, minus: np.ndarray) -> float:
    thetas = np.linspace(0.0, math.pi, 721, endpoint=False)
    best_g, best_t = -1e9, math.pi / 4
    for t in thetas:
        g, *_ = oriented_gap(plus, minus, float(t))
        if g > best_g:
            best_g, best_t = g, float(t)
    return best_t


def draw_grid(ax, lim: float = 3.15) -> None:
    for v in np.linspace(-3.0, 3.0, 13):
        ax.plot([-lim, lim], [v, v], color="#141824", lw=0.6, zorder=0)
        ax.plot([v, v], [-lim, lim], color="#141824", lw=0.6, zorder=0)
    ax.plot([-lim, lim], [0, 0], color="#1c2433", lw=0.9, zorder=1)
    ax.plot([0, 0], [-lim, lim], color="#1c2433", lw=0.9, zorder=1)
    ax.add_patch(
        plt.Rectangle(
            (-lim, -lim),
            2 * lim,
            2 * lim,
            fill=False,
            ec="#2a3142",
            lw=1.1,
            zorder=8,
        )
    )


def slab_polygon(u: np.ndarray, c_lo: float, c_hi: float, span: float = 4.2) -> np.ndarray:
    t = np.array([-u[1], u[0]])
    a = c_lo * u - span * t
    b = c_lo * u + span * t
    c = c_hi * u + span * t
    d = c_hi * u - span * t
    return np.vstack([a, b, c, d])


def render_svm_frame(
    plus,
    minus,
    sv_plus,
    sv_minus,
    theta: float,
    point_alpha: float,
    hull_alpha: float,
    slab_alpha: float,
    sv_alpha: float,
    arrow_alpha: float,
    settle: float,
    region_alpha: float = 0.0,
    dim_non_sv: float = 0.0,
) -> Image.Image:
    fig, ax = new_axes()
    lim = 3.15
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    draw_grid(ax, lim)

    gap, u, c_plus, c_minus = oriented_gap(plus, minus, theta)
    c_mid = 0.5 * (c_plus + c_minus)
    tvec = np.array([-u[1], u[0]])

    if region_alpha > 0 and gap > 0:
        ax.add_patch(
            Polygon(
                slab_polygon(u, c_mid, 8.0),
                closed=True,
                facecolor=PLUS,
                edgecolor="none",
                alpha=0.07 * region_alpha,
                zorder=1,
            )
        )
        ax.add_patch(
            Polygon(
                slab_polygon(u, -8.0, c_mid),
                closed=True,
                facecolor=MINUS,
                edgecolor="none",
                alpha=0.08 * region_alpha,
                zorder=1,
            )
        )

    if gap > 0 and slab_alpha > 0:
        poly = slab_polygon(u, c_minus, c_plus)
        ax.add_patch(
            Polygon(
                poly,
                closed=True,
                facecolor="#e8b84a",
                edgecolor="none",
                alpha=0.10 * slab_alpha,
                zorder=2,
            )
        )
        for c, is_mid in ((c_minus, False), (c_plus, False), (c_mid, True)):
            p0 = c * u - 4.0 * tvec
            p1 = c * u + 4.0 * tvec
            ax.plot(
                [p0[0], p1[0]],
                [p0[1], p1[1]],
                color=INK if is_mid else PLUS,
                lw=2.15 if is_mid else 1.15,
                linestyle="-" if is_mid else (0, (3.2, 2.8)),
                alpha=0.95 * slab_alpha if is_mid else 0.75 * slab_alpha,
                solid_capstyle="round",
                zorder=4,
            )

    if hull_alpha > 0:
        for pts, color in ((plus, PLUS), (minus, MINUS)):
            hull = convex_hull(pts)
            ax.add_patch(
                Polygon(
                    hull,
                    closed=True,
                    facecolor=color,
                    edgecolor=color,
                    alpha=0.10 * hull_alpha,
                    lw=1.0,
                    zorder=3,
                )
            )
            ax.plot(
                np.r_[hull[:, 0], hull[0, 0]],
                np.r_[hull[:, 1], hull[0, 1]],
                color=color,
                lw=1.05,
                alpha=0.55 * hull_alpha,
                zorder=3,
            )

    ax.scatter(plus[:, 0], plus[:, 1], s=210, c=PLUS, linewidths=0, zorder=5, alpha=0.16 * point_alpha)
    ax.scatter(
        plus[:, 0],
        plus[:, 1],
        s=74,
        c=PLUS,
        edgecolors="#1a1408",
        linewidths=0.45,
        zorder=6,
        alpha=point_alpha,
    )
    ax.scatter(minus[:, 0], minus[:, 1], s=210, c=MINUS, linewidths=0, zorder=5, alpha=0.16 * point_alpha)
    ax.scatter(
        minus[:, 0],
        minus[:, 1],
        s=74,
        c=MINUS,
        edgecolors="#081018",
        linewidths=0.45,
        zorder=6,
        alpha=point_alpha,
    )

    if dim_non_sv > 0 and sv_alpha > 0:
        sv_all = np.vstack([sv_plus, sv_minus]) if len(sv_plus) and len(sv_minus) else np.empty((0, 2))
        for pts, color in ((plus, PLUS), (minus, MINUS)):
            for p in pts:
                is_sv = any(np.linalg.norm(p - q) < 0.04 for q in sv_all)
                if not is_sv:
                    ax.scatter(
                        [p[0]],
                        [p[1]],
                        s=52,
                        c=color,
                        alpha=0.22 * dim_non_sv * point_alpha,
                        linewidths=0,
                        zorder=5,
                    )

    if sv_alpha > 0:
        svs = np.vstack([sv_plus, sv_minus])
        pulse = 1.0 + 0.08 * math.sin(settle * math.pi * 2)
        for p in svs:
            ax.add_patch(
                Circle(
                    (p[0], p[1]),
                    0.18 * pulse,
                    fill=False,
                    ec=INK,
                    lw=1.35,
                    alpha=0.95 * sv_alpha,
                    zorder=7,
                )
            )
            ax.add_patch(
                Circle(
                    (p[0], p[1]),
                    0.255 * pulse,
                    fill=False,
                    ec=INK,
                    lw=0.6,
                    alpha=0.35 * sv_alpha,
                    zorder=7,
                )
            )

    if arrow_alpha > 0 and gap > 0:
        origin = c_mid * u
        tip = origin + 0.95 * u
        ax.annotate(
            "",
            xy=tip,
            xytext=origin,
            arrowprops=dict(
                arrowstyle="-|>",
                color=INK,
                lw=1.4,
                mutation_scale=11,
                alpha=arrow_alpha,
            ),
            zorder=8,
        )

    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_svm_gif(path: Path) -> None:
    plus, minus = svm_points()
    theta_star = best_theta(plus, minus)
    sv_plus, sv_minus, _, _, _, _ = support_vectors(plus, minus, theta_star)
    # Varredura de orientações → margem máxima (busca geométrica do hiperplano).
    sweep = np.concatenate(
        [
            np.linspace(theta_star - 0.72, theta_star + 0.72, 36),
            np.full(8, theta_star + 0.72),
        ]
    )
    frames: list[Image.Image] = []

    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(render_svm_frame(plus, minus, sv_plus, sv_minus, theta_star, a, 0, 0, 0, 0, 0))
    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(render_svm_frame(plus, minus, sv_plus, sv_minus, theta_star, 1, a, 0, 0, 0, 0))
    for th in sweep:
        frames.append(
            render_svm_frame(plus, minus, sv_plus, sv_minus, float(th), 1, 0.55, 1, 0, 0, 0)
        )
    th0 = float(sweep[-1])
    for i in range(16):
        a = ease((i + 1) / 16)
        th = th0 + a * (theta_star - th0)
        frames.append(
            render_svm_frame(plus, minus, sv_plus, sv_minus, th, 1, 0.35 * (1 - a), 1, 0, 0, 0)
        )
    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, theta_star, 1, 0, 1, a, a, 0, dim_non_sv=a
            )
        )
    for i in range(22):
        a = ease(min(1.0, (i + 1) / 8))
        frames.append(
            render_svm_frame(
                plus,
                minus,
                sv_plus,
                sv_minus,
                theta_star,
                1,
                0,
                1,
                1,
                1,
                i / 22,
                region_alpha=a,
                dim_non_sv=1.0,
            )
        )

    save_gif(frames, path, duration_ms=75)
    frames[-1].save(path.with_suffix(".png").with_name("svm_frame.png"))


# ---------------------------------------------------------------------------
# Algoritmo genético — visualização matemática P&B (bolinhas + operadores)
# ---------------------------------------------------------------------------
#
# Roteiro do GIF (sem texto):
#   1. Paisagem f(x,y) — curvas de nível em preto e branco
#   2. População P₀ — bolinhas aleatórias no domínio
#   3. Avaliação — tamanho/brilho ∝ fitness
#   4. Seleção — anéis nas escolhidas (torneio)
#   5. Crossover — segmento entre pais; filho = α·p₁ + (1−α)·p₂
#   6. Mutação — vetor ε gaussiano a partir do filho
#   7. Nova geração — elitismo + descendência; repetir até convergência
#
# Referências: blend crossover (Holland), torneio, paisagem Rastrigin 2D
# (padrão em visualizadores educacionais de AG).

GA_BG = "#050505"
GA_GRID = "#1a1a1a"
GA_CONTOUR = "#2e2e2e"
GA_CONTOUR_HI = "#6a6a6a"
GA_DOT = "#b0b0b0"
GA_DOT_HI = "#ffffff"
GA_LINE = "#8a8a8a"
GA_SELECT = "#ffffff"


@dataclass
class GAIndividual:
    pos: np.ndarray
    fitness: float = 0.0


@dataclass
class GAStep:
    """Estado de um frame da animação."""
    population: list[GAIndividual]
    phase: str  # landscape | init | evaluate | select | crossover | mutate | generation
    selected: list[int] = field(default_factory=list)
    parent_pairs: list[tuple[int, int]] = field(default_factory=list)
    crossover_alpha: list[float] = field(default_factory=list)
    blend_points: list[np.ndarray] = field(default_factory=list)
    children_pre: list[np.ndarray] = field(default_factory=list)
    children_post: list[np.ndarray] = field(default_factory=list)
    elites: list[int] = field(default_factory=list)
    crossover_t: float = 1.0
    mutate_t: float = 1.0
    landscape_alpha: float = 1.0


def rastrigin(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Função teste clássica; mínimo global em (0, 0)."""
    return 20 + x**2 + y**2 - 10 * (np.cos(2 * np.pi * x) + np.cos(2 * np.pi * y))


def ga_fitness(pos: np.ndarray) -> float:
    return float(-rastrigin(pos[0], pos[1]))


def tournament_select(rng: np.random.Generator, pop: list[GAIndividual], k: int = 3) -> int:
    idx = rng.choice(len(pop), size=k, replace=False)
    best = idx[0]
    for i in idx[1:]:
        if pop[i].fitness > pop[best].fitness:
            best = i
    return int(best)


def build_ga_timeline(rng: np.random.Generator, n_pop: int = 28, n_gen: int = 7) -> list[GAStep]:
    lim = 4.08
    pop = [
        GAIndividual(rng.uniform(-lim, lim, size=2))
        for _ in range(n_pop)
    ]
    timeline: list[GAStep] = []

    # 1 — paisagem
    for a in np.linspace(0.15, 1.0, 10):
        timeline.append(GAStep([], "landscape", landscape_alpha=float(a)))

    # 2 — população inicial
    for a in np.linspace(0.0, 1.0, 12):
        timeline.append(GAStep(list(pop), "init", landscape_alpha=1.0))
        timeline[-1].landscape_alpha = 1.0
        # fade-in via pop count
        n_show = max(1, int(a * n_pop))
        timeline[-1].population = pop[:n_show]

    for g in range(n_gen):
        for ind in pop:
            ind.fitness = ga_fitness(ind.pos)

        # 3 — avaliação
        for _ in range(5):
            timeline.append(GAStep(list(pop), "evaluate", landscape_alpha=1.0))

        # 4 — seleção (torneio visual: 4 pais = 2 pares)
        pairs: list[tuple[int, int]] = []
        selected: list[int] = []
        for _ in range(2):
            i = tournament_select(rng, pop)
            j = tournament_select(rng, pop)
            while j == i:
                j = tournament_select(rng, pop)
            pairs.append((i, j))
            selected.extend([i, j])
        selected = list(dict.fromkeys(selected))
        for _ in range(6):
            timeline.append(GAStep(list(pop), "select", selected=selected, landscape_alpha=1.0))

        # 5 — crossover (blend geométrico)
        alphas: list[float] = []
        blends: list[np.ndarray] = []
        children_pre: list[np.ndarray] = []
        parent_pairs: list[tuple[int, int]] = []
        for i, j in pairs:
            alpha = float(rng.uniform(0.28, 0.72))
            p1, p2 = pop[i].pos, pop[j].pos
            blend = alpha * p1 + (1 - alpha) * p2
            alphas.append(alpha)
            blends.append(blend)
            children_pre.append(blend.copy())
            parent_pairs.append((i, j))
        for t in np.linspace(0.0, 1.0, 10):
            timeline.append(
                GAStep(
                    list(pop),
                    "crossover",
                    selected=selected,
                    parent_pairs=parent_pairs,
                    crossover_alpha=alphas,
                    blend_points=blends,
                    crossover_t=float(t),
                    landscape_alpha=1.0,
                )
            )

        # 6 — mutação (vetor ε)
        children_post: list[np.ndarray] = []
        sigma = 0.55 * (0.82**g)
        for blend in children_pre:
            child = blend + rng.normal(0.0, sigma, size=2)
            child = np.clip(child, -lim, lim)
            children_post.append(child)
        for t in np.linspace(0.0, 1.0, 8):
            timeline.append(
                GAStep(
                    list(pop),
                    "mutate",
                    selected=selected,
                    parent_pairs=parent_pairs,
                    crossover_alpha=alphas,
                    blend_points=blends,
                    children_pre=children_pre,
                    children_post=children_post,
                    mutate_t=float(t),
                    landscape_alpha=1.0,
                )
            )

        # 7 — nova geração
        ranked = sorted(range(len(pop)), key=lambda k: pop[k].fitness, reverse=True)
        elite_n = 2
        elites = ranked[:elite_n]
        new_pop = [GAIndividual(pop[i].pos.copy(), pop[i].fitness) for i in elites]
        child_idx = 0
        while len(new_pop) < n_pop:
            if child_idx < len(children_post):
                new_pop.append(GAIndividual(children_post[child_idx].copy()))
                child_idx += 1
            else:
                pi = tournament_select(rng, pop)
                pj = tournament_select(rng, pop)
                alpha = rng.uniform(0.25, 0.75)
                c = alpha * pop[pi].pos + (1 - alpha) * pop[pj].pos
                c = c + rng.normal(0.0, sigma, size=2)
                new_pop.append(GAIndividual(np.clip(c, -lim, lim)))
        pop = new_pop[:n_pop]
        for _ in range(4):
            timeline.append(GAStep(list(pop), "generation", elites=elites, landscape_alpha=1.0))

    for ind in pop:
        ind.fitness = ga_fitness(ind.pos)
    for _ in range(18):
        timeline.append(GAStep(list(pop), "evaluate", landscape_alpha=1.0))

    return timeline


def draw_ga_landscape(ax, xs, ys, zz, alpha: float) -> None:
    levels = np.linspace(zz.min(), zz.max(), 14)
    ax.contour(xs, ys, zz, levels=levels, colors=GA_CONTOUR, linewidths=0.55, alpha=0.85 * alpha, zorder=1)
    ax.contour(xs, ys, zz, levels=levels[::2], colors=GA_CONTOUR_HI, linewidths=0.75, alpha=0.45 * alpha, zorder=2)
    for v in np.linspace(-4, 4, 9):
        ax.axhline(v, color=GA_GRID, lw=0.35, alpha=0.55 * alpha, zorder=0)
        ax.axvline(v, color=GA_GRID, lw=0.35, alpha=0.55 * alpha, zorder=0)
    ax.add_patch(Circle((0, 0), 0.12, facecolor=GA_DOT_HI, edgecolor="none", alpha=0.55 * alpha, zorder=3))
    ax.add_patch(
        Circle((0, 0), 0.22, facecolor="none", edgecolor=GA_DOT_HI, lw=0.8, alpha=0.35 * alpha, zorder=3)
    )


def fitness_norm(pop: list[GAIndividual]) -> np.ndarray:
    vals = np.array([p.fitness for p in pop])
    lo, hi = vals.min(), vals.max()
    if hi - lo < 1e-9:
        return np.ones(len(pop))
    return (vals - lo) / (hi - lo)


def render_ga_frame(step: GAStep, xs, ys, zz) -> Image.Image:
    fig, ax = new_axes()
    ax.set_facecolor(GA_BG)
    lim = 4.15
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)

    draw_ga_landscape(ax, xs, ys, zz, step.landscape_alpha)

    pop = step.population
    if not pop and step.phase != "landscape":
        img = fig_to_image(fig)
        plt.close(fig)
        return img

    fn = fitness_norm(pop) if pop else np.array([])
    selected = set(step.selected)
    elites = set(step.elites)

    # Bolinhas — tamanho e brilho ∝ fitness
    for k, ind in enumerate(pop):
        t = fn[k] if len(fn) else 0.5
        is_sel = k in selected
        is_elite = k in elites
        fade = 0.28 if (step.phase == "select" and not is_sel) else 1.0
        r = 5.5 + 11 * t
        ax.scatter(
            [ind.pos[0]],
            [ind.pos[1]],
            s=r * r,
            c=GA_DOT_HI if t > 0.62 else GA_DOT,
            alpha=(0.35 + 0.65 * t) * fade,
            edgecolors=GA_DOT_HI if is_sel or is_elite else "none",
            linewidths=0.9 if is_sel else 0.0,
            zorder=5,
        )
        if is_sel and step.phase in ("select", "crossover", "mutate"):
            ax.add_patch(
                Circle(
                    (ind.pos[0], ind.pos[1]),
                    0.22 + 0.06 * t,
                    fill=False,
                    ec=GA_SELECT,
                    lw=1.1,
                    alpha=0.92,
                    zorder=6,
                )
            )

    # Crossover — segmento p₁—p₂ e filho deslizando no blend
    if step.phase in ("crossover", "mutate") and step.parent_pairs:
        for idx, (i, j) in enumerate(step.parent_pairs):
            p1, p2 = pop[i].pos, pop[j].pos
            ax.plot([p1[0], p2[0]], [p1[1], p2[1]], color=GA_LINE, lw=1.0, ls=(0, (4, 3)), alpha=0.75, zorder=4)
            if idx < len(step.blend_points):
                blend = step.blend_points[idx]
                alpha = step.crossover_alpha[idx] if idx < len(step.crossover_alpha) else 0.5
                # filho percorre o segmento até o ponto de blend
                cx = p1[0] + (1 - step.crossover_t) * (blend[0] - p1[0])
                cy = p1[1] + (1 - step.crossover_t) * (blend[1] - p1[1])
                ax.scatter([cx], [cy], s=70, c=GA_DOT_HI, alpha=0.95, edgecolors="none", zorder=7)
                if step.crossover_t > 0.85:
                    ax.scatter([blend[0]], [blend[1]], s=55, facecolors="none", edgecolors=GA_DOT_HI, linewidths=1.0, zorder=7)

    # Mutação — vetor ε
    if step.phase == "mutate" and step.children_pre and step.children_post:
        for pre, post in zip(step.children_pre, step.children_post):
            px = pre[0] + step.mutate_t * (post[0] - pre[0])
            py = pre[1] + step.mutate_t * (post[1] - pre[1])
            ax.annotate(
                "",
                xy=(post[0], post[1]),
                xytext=(pre[0], pre[1]),
                arrowprops=dict(arrowstyle="-|>", color=GA_LINE, lw=1.0, mutation_scale=9),
                alpha=0.55 + 0.45 * step.mutate_t,
                zorder=4,
            )
            ax.scatter([px], [py], s=65, c=GA_DOT_HI, alpha=0.9, edgecolors="none", zorder=7)

    ax.add_patch(plt.Rectangle((-lim, -lim), 2 * lim, 2 * lim, fill=False, ec="#2a2a2a", lw=1.0, zorder=10))
    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_ga_gif(path: Path) -> None:
    rng = np.random.default_rng(42)
    timeline = build_ga_timeline(rng)
    grid = np.linspace(-4.1, 4.1, 200)
    xs, ys = np.meshgrid(grid, grid)
    zz = rastrigin(xs, ys)

    frames = [render_ga_frame(step, xs, ys, zz) for step in timeline]
    save_gif(frames, path, duration_ms=90)
    frames[-1].save(path.with_suffix(".png").with_name("algoritmo_genetico_frame.png"))


def main() -> None:
    svm_path = ROOT / "svm.gif"
    ga_path = ROOT / "algoritmo_genetico.gif"
    print("rendering SVM…")
    make_svm_gif(svm_path)
    print("wrote", svm_path, "size", svm_path.stat().st_size)
    print("rendering genetic algorithm…")
    make_ga_gif(ga_path)
    print("wrote", ga_path, "size", ga_path.stat().st_size)


if __name__ == "__main__":
    main()
