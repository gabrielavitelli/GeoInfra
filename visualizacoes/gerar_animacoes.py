"""Gera GIFs geométricos, sem texto, de SVM e algoritmo genético."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
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
# Algoritmo genético — árvore com raízes (seleção + amplificação)
# ---------------------------------------------------------------------------

SOIL_CMAP = LinearSegmentedColormap.from_list(
    "soil_geo",
    ["#0a0810", "#1a1028", "#3a1848", "#7a2848", "#c24a3a", "#e6b84c", "#f8ecc8"],
)
TRUNK = "#6b4a2a"
CANOPY = "#7ec86a"


def water_field(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Fontes de água/nutrientes no subsolo — fitness do fenótipo (ponta da raiz)."""
    main = np.exp(-((x) ** 2 + (y + 2.05) ** 2) / 0.42)
    decoy_l = 0.38 * np.exp(-((x + 1.35) ** 2 + (y + 1.35) ** 2) / 0.28)
    decoy_r = 0.30 * np.exp(-((x - 1.15) ** 2 + (y + 1.55) ** 2) / 0.32)
    return main + decoy_l + decoy_r


def genes_to_polyline(genes: np.ndarray, base: tuple[float, float] = (0.0, 0.0)) -> np.ndarray:
    """Genótipo → traçado da raiz (segmentos com ângulo acumulado, crescendo para baixo)."""
    x, y = base
    angle = -math.pi / 2
    pts = [(x, y)]
    for d_angle, length in genes:
        angle += float(d_angle)
        x += float(length) * math.cos(angle)
        y += float(length) * math.sin(angle)
        pts.append((x, y))
    return np.array(pts, dtype=float)


def root_fitness(genes: np.ndarray) -> float:
    tip = genes_to_polyline(genes)[-1]
    depth = max(0.0, -tip[1])
    return float(water_field(tip[0], tip[1]) + 0.12 * depth / 3.0)


class RootIndividual:
    __slots__ = ("genes", "fitness", "selected", "generation")

    def __init__(self, genes: np.ndarray, generation: int = 0):
        self.genes = genes.copy()
        self.generation = generation
        self.fitness = root_fitness(genes)
        self.selected = False


def random_genes(rng: np.random.Generator, n_seg: int = 5) -> np.ndarray:
    return np.column_stack(
        [
            rng.normal(0.0, 0.38, n_seg),
            rng.uniform(0.28, 0.52, n_seg),
        ]
    )


def tournament_roots(pop: list[RootIndividual], rng: np.random.Generator, k: int = 3) -> RootIndividual:
    cand = rng.choice(len(pop), size=k, replace=False)
    return max((pop[i] for i in cand), key=lambda r: r.fitness)


def evolve_roots(rng: np.random.Generator, n_pop: int = 14, n_gen: int = 10) -> list[list[RootIndividual]]:
    n_seg = 5
    pop = [RootIndividual(random_genes(rng, n_seg)) for _ in range(n_pop)]
    history: list[list[RootIndividual]] = [pop]
    for g in range(n_gen):
        ranked = sorted(pop, key=lambda r: r.fitness, reverse=True)
        elite_n = 3
        keep = [RootIndividual(r.genes, g + 1) for r in ranked[:elite_n]]
        for r in keep:
            r.selected = True
        new_pop: list[RootIndividual] = keep
        sigma = 0.34 * (0.82**g)
        while len(new_pop) < n_pop:
            p1 = tournament_roots(pop, rng)
            p2 = tournament_roots(pop, rng)
            alpha = rng.uniform(0.35, 0.65)
            child_genes = alpha * p1.genes + (1 - alpha) * p2.genes
            child_genes[:, 0] += rng.normal(0.0, sigma, n_seg)
            child_genes[:, 1] += rng.normal(0.0, sigma * 0.55, n_seg)
            child_genes[:, 1] = np.clip(child_genes[:, 1], 0.18, 0.62)
            if rng.random() < 0.10:
                child_genes = random_genes(rng, n_seg)
            new_pop.append(RootIndividual(child_genes, g + 1))
        pop = new_pop
        history.append(pop)
    return history


def draw_tree(ax, trunk_alpha: float, canopy_alpha: float) -> None:
    if trunk_alpha <= 0:
        return
    # Tronco
    ax.plot([0, 0], [0, 2.35], color=TRUNK, lw=5.5, solid_capstyle="round", alpha=0.92 * trunk_alpha, zorder=6)
    ax.plot([0, 0], [0, 2.35], color=PLUS, lw=1.4, alpha=0.35 * trunk_alpha, zorder=7)
    if canopy_alpha > 0:
        for dx, dy, r in [
            (0.0, 2.55, 0.72),
            (-0.55, 2.35, 0.52),
            (0.58, 2.28, 0.48),
            (-0.28, 2.85, 0.42),
            (0.32, 2.92, 0.38),
        ]:
            ax.add_patch(
                Circle(
                    (dx, dy),
                    r,
                    facecolor=CANOPY,
                    edgecolor="#3a5a30",
                    lw=0.8,
                    alpha=0.55 * canopy_alpha,
                    zorder=8,
                )
            )
        ax.add_patch(
            Circle((0, 2.65), 0.28, facecolor=PLUS, edgecolor="none", alpha=0.25 * canopy_alpha, zorder=9)
        )


def draw_soil(ax, xs, ys, zz, alpha: float) -> None:
    ax.contourf(xs, ys, zz, levels=16, cmap=SOIL_CMAP, alpha=0.97 * alpha, zorder=1, extend="both")
    ax.contour(xs, ys, zz, levels=16, colors=INK, linewidths=0.35, alpha=0.14 * alpha, zorder=2)
    ax.axhline(0, color=INK, lw=1.6, alpha=0.55 * alpha, zorder=5)
    ax.fill_between([-3.05, 3.05], -3.05, 0, color="#120e18", alpha=0.35 * alpha, zorder=0)


def render_ga_tree_frame(
    xs,
    ys,
    zz,
    roots: list[RootIndividual],
    progress: float,
    trunk_alpha: float,
    canopy_alpha: float,
    soil_alpha: float,
    select_pulse: float = 0.0,
) -> Image.Image:
    fig, ax = new_axes()
    ax.set_xlim(-3.05, 3.05)
    ax.set_ylim(-3.05, 3.05)

    draw_soil(ax, xs, ys, zz, soil_alpha)
    draw_tree(ax, trunk_alpha, canopy_alpha)

    ranked = sorted(roots, key=lambda r: r.fitness, reverse=True)
    elite_set = {id(r) for r in ranked[:3]}
    fit_vals = np.array([r.fitness for r in roots])
    fmin, fmax = fit_vals.min(), fit_vals.max()
    span = max(fmax - fmin, 1e-6)

    for r in sorted(roots, key=lambda x: x.fitness):
        poly = genes_to_polyline(r.genes)
        n = max(2, int(len(poly) * progress))
        seg = poly[:n]
        if len(seg) < 2:
            continue
        t = (r.fitness - fmin) / span
        is_elite = id(r) in elite_set
        base_lw = 0.7 + 2.8 * t
        if is_elite and select_pulse > 0:
            base_lw *= 1.0 + 1.8 * select_pulse
        alpha = 0.25 + 0.75 * t
        if not is_elite and select_pulse > 0.25:
            alpha *= max(0.06, 1.0 - 0.9 * select_pulse)
        color = SOIL_CMAP(0.55 + 0.45 * t)
        ax.plot(
            seg[:, 0],
            seg[:, 1],
            color=color,
            lw=base_lw,
            solid_capstyle="round",
            alpha=alpha,
            zorder=4 if not is_elite else 5,
        )
        tip = seg[-1]
        if tip[1] < -0.05:
            ax.scatter(
                [tip[0]],
                [tip[1]],
                s=28 + 95 * t,
                color=SOIL_CMAP(0.78 + 0.22 * t),
                edgecolors="none",
                alpha=(0.2 + 0.8 * t) * alpha,
                zorder=6,
            )

    for r in ranked[:3]:
        poly = genes_to_polyline(r.genes)
        n = max(2, int(len(poly) * progress))
        seg = poly[:n]
        if len(seg) < 2:
            continue
        ax.plot(seg[:, 0], seg[:, 1], color=INK, lw=0.85, alpha=0.4 * select_pulse, zorder=7)
        ax.add_patch(
            Circle(
                (seg[-1, 0], seg[-1, 1]),
                0.10 + 0.07 * select_pulse,
                fill=False,
                ec=PLUS,
                lw=1.15,
                alpha=0.9 * select_pulse,
                zorder=8,
            )
        )

    ax.add_patch(
        plt.Rectangle((-3.05, -3.05), 6.1, 6.1, fill=False, ec="#2a3142", lw=1.1, zorder=10)
    )
    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_ga_gif(path: Path) -> None:
    rng = np.random.default_rng(17)
    history = evolve_roots(rng)
    grid = np.linspace(-3.0, 3.0, 220)
    xs, ys = np.meshgrid(grid, grid)
    zz = water_field(xs, ys)

    frames: list[Image.Image] = []

    # Céu + solo + tronco
    for i in range(12):
        a = ease((i + 1) / 12)
        frames.append(render_ga_tree_frame(xs, ys, zz, history[0], 0.0, a * 0.9, 0.0, a, 0.0))
    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(render_ga_tree_frame(xs, ys, zz, history[0], 0.0, 1.0, a, 1.0, 0.0))

    for gen_idx, pop in enumerate(history):
        grow_steps = 10 if gen_idx == 0 else 8
        for s in range(grow_steps):
            prog = ease((s + 1) / grow_steps)
            frames.append(render_ga_tree_frame(xs, ys, zz, pop, prog, 1.0, 1.0, 1.0, 0.0))

        if gen_idx < len(history) - 1:
            # Pulso de seleção: raízes boas engrossam, fracas esmaecem
            for s in range(8):
                pulse = ease((s + 1) / 8)
                frames.append(render_ga_tree_frame(xs, ys, zz, pop, 1.0, 1.0, 1.0, 1.0, pulse))

    final = history[-1]
    for i in range(20):
        pulse = 0.6 + 0.4 * math.sin(i / 20 * math.pi * 2)
        frames.append(render_ga_tree_frame(xs, ys, zz, final, 1.0, 1.0, 1.0, 1.0, pulse))

    save_gif(frames, path, duration_ms=85)
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
