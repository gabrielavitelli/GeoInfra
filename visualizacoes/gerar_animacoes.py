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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float, float, float, int, int]:
    """SVs = argmin/argmax da projeção w·x; retorna também índices extremos."""
    gap, u, c_plus, c_minus = oriented_gap(plus, minus, theta)
    c_mid = 0.5 * (c_plus + c_minus)
    proj_p = plus @ u
    proj_m = minus @ u
    i_plus = int(np.argmin(proj_p))
    i_minus = int(np.argmax(proj_m))
    sv_plus = plus[np.abs(proj_p - c_plus) <= tol]
    sv_minus = minus[np.abs(proj_m - c_minus) <= tol]
    if len(sv_plus) == 0:
        sv_plus = plus[i_plus : i_plus + 1]
    if len(sv_minus) == 0:
        sv_minus = minus[i_minus : i_minus + 1]
    return sv_plus, sv_minus, u, c_plus, c_minus, c_mid, gap, i_plus, i_minus


def margin_violators(
    plus: np.ndarray, minus: np.ndarray, u: np.ndarray, c_mid: float, gap: float
) -> tuple[np.ndarray, np.ndarray]:
    """Pontos do lado errado do hiperplano (só possível quando γ ≤ 0)."""
    if gap > 1e-9:
        return np.zeros(len(plus), dtype=bool), np.zeros(len(minus), dtype=bool)
    pp, pm = plus @ u, minus @ u
    return pp < c_mid, pm > c_mid


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
    theta: float,
    point_alpha: float,
    hull_alpha: float,
    slab_alpha: float,
    sv_alpha: float,
    arrow_alpha: float,
    settle: float,
    region_alpha: float = 0.0,
    dim_non_sv: float = 0.0,
    gap_max: float = 1.0,
    sweep_mode: bool = False,
    margin_bracket: float = 0.0,
) -> Image.Image:
    fig, ax = new_axes()
    lim = 3.15
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    draw_grid(ax, lim)

    sv_plus, sv_minus, u, c_plus, c_minus, c_mid, gap, i_plus, i_minus = support_vectors(
        plus, minus, theta
    )
    tvec = np.array([-u[1], u[0]])
    gap_ratio = max(0.0, min(1.0, gap / gap_max)) if gap_max > 1e-9 else 0.0
    valid = gap > 1e-9
    viol_plus, viol_minus = margin_violators(plus, minus, u, c_mid, gap)

    if region_alpha > 0 and valid:
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

    if slab_alpha > 0:
        if valid:
            poly = slab_polygon(u, c_minus, c_plus)
            ax.add_patch(
                Polygon(
                    poly,
                    closed=True,
                    facecolor="#e8b84a",
                    edgecolor="none",
                    alpha=(0.06 + 0.14 * gap_ratio) * slab_alpha,
                    zorder=2,
                )
            )
        else:
            poly = slab_polygon(u, c_minus, c_plus)
            ax.add_patch(
                Polygon(
                    poly,
                    closed=True,
                    facecolor="#c04040",
                    edgecolor="none",
                    alpha=0.12 * slab_alpha,
                    zorder=2,
                )
            )
        for c, is_mid in ((c_minus, False), (c_plus, False), (c_mid, True)):
            p0 = c * u - 4.0 * tvec
            p1 = c * u + 4.0 * tvec
            if not valid:
                lc, lw, la = "#a05050", 1.0, 0.55 * slab_alpha
                ls = (0, (2.5, 2.5))
            elif is_mid:
                lc, lw, la, ls = INK, 2.15, 0.95 * slab_alpha, "-"
            else:
                lc, lw, la, ls = PLUS, 1.15, (0.45 + 0.40 * gap_ratio) * slab_alpha, (0, (3.2, 2.8))
            ax.plot(
                [p0[0], p1[0]],
                [p0[1], p1[1]],
                color=lc,
                lw=lw,
                linestyle=ls,
                alpha=la,
                solid_capstyle="round",
                zorder=4,
            )

    # Segmento γ: entre os SVs que fixam as margens (comprimento = gap = 2γ em ||w||=1)
    if margin_bracket > 0 and valid:
        p_sv = plus[i_plus]
        m_sv = minus[i_minus]
        ax.plot(
            [m_sv[0], p_sv[0]],
            [m_sv[1], p_sv[1]],
            color=INK,
            lw=2.0,
            solid_capstyle="round",
            alpha=0.88 * margin_bracket,
            zorder=5,
        )
        mid_br = 0.5 * (m_sv + p_sv)
        half = 0.5 * gap
        a0 = c_mid * u - half * u
        a1 = c_mid * u + half * u
        ax.plot(
            [a0[0], a1[0]],
            [a0[1], a1[1]],
            color=INK,
            lw=1.0,
            ls=(0, (1.2, 2.8)),
            alpha=0.55 * margin_bracket,
            zorder=4,
        )
        for pt in (a0, a1):
            ax.add_patch(
                Circle((pt[0], pt[1]), 0.055, facecolor=INK, edgecolor="none", alpha=0.7 * margin_bracket, zorder=6)
            )

    if hull_alpha > 0 and not sweep_mode:
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

    if sweep_mode and valid:
        for idx in (i_plus,):
            ax.scatter(
                [plus[idx, 0]],
                [plus[idx, 1]],
                s=130,
                facecolors="none",
                edgecolors=PLUS,
                linewidths=1.4,
                alpha=0.95,
                zorder=7,
            )
        for idx in (i_minus,):
            ax.scatter(
                [minus[idx, 0]],
                [minus[idx, 1]],
                s=130,
                facecolors="none",
                edgecolors=MINUS,
                linewidths=1.4,
                alpha=0.95,
                zorder=7,
            )

    if not valid and slab_alpha > 0:
        for pts, mask, col in ((plus, viol_plus, "#ff7070"), (minus, viol_minus, "#ff7070")):
            if mask.any():
                ax.scatter(
                    pts[mask, 0],
                    pts[mask, 1],
                    s=95,
                    facecolors="none",
                    edgecolors=col,
                    linewidths=1.2,
                    alpha=0.85 * slab_alpha,
                    zorder=7,
                )

    if dim_non_sv > 0 and sv_alpha > 0 and not sweep_mode:
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

    if sv_alpha > 0 and not sweep_mode:
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

    if arrow_alpha > 0 and valid:
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
    _, _, _, _, _, _, gap_max, _, _ = support_vectors(plus, minus, theta_star)
    gap_max = max(gap_max, 1e-9)

    sweep = np.concatenate(
        [
            np.linspace(theta_star - 0.72, theta_star + 0.72, 40),
            np.full(6, theta_star + 0.72),
        ]
    )
    frames: list[Image.Image] = []

    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(
            render_svm_frame(plus, minus, theta_star, a, 0, 0, 0, 0, 0, gap_max=gap_max)
        )
    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(
            render_svm_frame(plus, minus, theta_star, 1, a, 0, 0, 0, 0, gap_max=gap_max)
        )

    # Varredura: SVs dinâmicos, faixa γ proporcional a gap, segmento entre SVs
    for th in sweep:
        _, _, _, _, _, _, gap, _, _ = support_vectors(plus, minus, float(th))
        frames.append(
            render_svm_frame(
                plus,
                minus,
                float(th),
                1,
                0,
                1,
                0,
                0,
                0,
                gap_max=gap_max,
                sweep_mode=True,
                margin_bracket=1.0,
            )
        )

    th0 = float(sweep[-1])
    for i in range(16):
        a = ease((i + 1) / 16)
        th = th0 + a * (theta_star - th0)
        _, _, _, _, _, _, gap, _, _ = support_vectors(plus, minus, th)
        frames.append(
            render_svm_frame(
                plus,
                minus,
                th,
                1,
                0,
                1,
                0,
                0,
                0,
                gap_max=gap_max,
                sweep_mode=True,
                margin_bracket=0.4 + 0.6 * a,
            )
        )

    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(
            render_svm_frame(
                plus,
                minus,
                theta_star,
                1,
                0,
                1,
                a,
                a,
                0,
                dim_non_sv=a,
                gap_max=gap_max,
                margin_bracket=1.0,
            )
        )
    for i in range(22):
        a = ease(min(1.0, (i + 1) / 8))
        frames.append(
            render_svm_frame(
                plus,
                minus,
                theta_star,
                1,
                0,
                1,
                1,
                1,
                i / 22,
                region_alpha=a,
                dim_non_sv=1.0,
                gap_max=gap_max,
                margin_bracket=1.0,
            )
        )

    save_gif(frames, path, duration_ms=75)
    frames[-1].save(path.with_suffix(".png").with_name("svm_frame.png"))



# ---------------------------------------------------------------------------
# Algoritmo genético — árvore de populações (bolinhas P&B)
# ---------------------------------------------------------------------------

GA_PAPER = "#f6f6f4"
GA_INK = "#0e0e0e"
GA_LINE = "#3a3a3a"
GA_MUTED = "#9a9a9a"
GA_FAINT = "#d8d8d6"


@dataclass
class PopNode:
    uid: int
    x: float
    y: float
    gene: float
    fitness: float
    gen: int
    parents: tuple[int, int] | None = None


@dataclass
class PopTreeFrame:
    nodes: list[PopNode]
    edges_done: list[tuple[float, float, float, float]]
    phase: str
    selected: list[int] = field(default_factory=list)
    anim_edges: list[tuple[float, float, float, float]] = field(default_factory=list)
    anim_nodes: list[PopNode] = field(default_factory=list)
    edge_t: float = 1.0
    node_t: float = 1.0


def node_fitness(gene: float) -> float:
    return float(-abs(gene - 0.15))


def layout_row(n: int, gen: int, y_top: float, row_h: float) -> list[tuple[float, float]]:
    y = y_top - gen * row_h
    spread = 0.18 + gen * 0.42
    if n == 1:
        return [(0.0, y)]
    xs = np.linspace(-spread, spread, n)
    return [(float(x), y) for x in xs]


def simulate_population_tree(rng: np.random.Generator) -> tuple[list[list[PopNode]], list[tuple[int, int, int]]]:
    sizes = [1, 3, 4, 6, 7, 9, 10]
    y_top, row_h = 2.65, 0.58
    generations: list[list[PopNode]] = []
    links: list[tuple[int, int, int]] = []
    uid = 0

    g0 = PopNode(uid, 0.0, y_top, float(rng.normal(0, 0.2)), node_fitness(0), 0)
    uid += 1
    generations.append([g0])

    for g in range(1, len(sizes)):
        prev = generations[-1]
        coords = layout_row(sizes[g], g, y_top, row_h)
        row: list[PopNode] = []
        for (x, y) in coords:
            p1 = prev[rng.integers(0, len(prev))]
            p2 = prev[rng.integers(0, len(prev))]
            alpha = float(rng.uniform(0.32, 0.68))
            gene = alpha * p1.gene + (1 - alpha) * p2.gene + float(rng.normal(0, 0.07 + 0.015 * g))
            child = PopNode(uid, x, y, gene, node_fitness(gene), g, (p1.uid, p2.uid))
            links.append((p1.uid, p2.uid, child.uid))
            uid += 1
            row.append(child)
        generations.append(row)

    return generations, links


def _edges_from_map(uid_map: dict[int, PopNode], links: list[tuple[int, int, int]]) -> list[tuple[float, float, float, float]]:
    edges: list[tuple[float, float, float, float]] = []
    for pa, pb, cu in links:
        if cu not in uid_map or pa not in uid_map or pb not in uid_map:
            continue
        a, b, c = uid_map[pa], uid_map[pb], uid_map[cu]
        edges.append((a.x, a.y, c.x, c.y))
        if pa != pb:
            edges.append((b.x, b.y, c.x, c.y))
    return edges


def build_pop_tree_timeline(rng: np.random.Generator) -> list[PopTreeFrame]:
    generations, links = simulate_population_tree(rng)
    uid_map: dict[int, PopNode] = {}
    timeline: list[PopTreeFrame] = []

    g0 = generations[0][0]
    for t in np.linspace(0.0, 1.0, 10):
        timeline.append(PopTreeFrame([], [], "spawn", anim_nodes=[g0], node_t=float(t)))
    uid_map[g0.uid] = g0
    timeline.append(PopTreeFrame(list(uid_map.values()), [], "hold"))

    for g in range(1, len(generations)):
        prev_row = generations[g - 1]
        new_row = generations[g]
        ranked = sorted(prev_row, key=lambda n: n.fitness, reverse=True)
        n_sel = min(3, len(ranked))
        selected = [ranked[i].uid for i in range(n_sel)]

        for _ in range(6):
            timeline.append(
                PopTreeFrame(
                    list(uid_map.values()),
                    _edges_from_map(uid_map, links),
                    "select",
                    selected=selected,
                )
            )

        new_uids = {n.uid for n in new_row}
        new_links = [lk for lk in links if lk[2] in new_uids]

        for t in np.linspace(0.0, 1.0, 9):
            anim_edges: list[tuple[float, float, float, float]] = []
            anim_nodes: list[PopNode] = []
            for child in new_row:
                p1 = uid_map[child.parents[0]]
                p2 = uid_map[child.parents[1]]
                mx, my = (p1.x + p2.x) / 2, (p1.y + p2.y) / 2
                # crossover no meio → mutação até posição final
                if t < 0.55:
                    s = t / 0.55
                    px = mx + s * (child.x - mx) * 0.75
                    py = my + s * (child.y - my) * 0.75
                else:
                    s = (t - 0.55) / 0.45
                    bx = mx + 0.75 * (child.x - mx)
                    by = my + 0.75 * (child.y - my)
                    px = bx + s * (child.x - bx)
                    py = by + s * (child.y - by)
                anim_nodes.append(
                    PopNode(child.uid, px, py, child.gene, child.fitness, child.gen, child.parents)
                )
            for pa, pb, cu in new_links:
                a, b = uid_map[pa], uid_map[pb]
                anim_c = next(n for n in anim_nodes if n.uid == cu)
                anim_edges.append((a.x, a.y, anim_c.x, anim_c.y))
                if pa != pb:
                    anim_edges.append((b.x, b.y, anim_c.x, anim_c.y))
            timeline.append(
                PopTreeFrame(
                    list(uid_map.values()),
                    _edges_from_map(uid_map, [lk for lk in links if lk[2] not in new_uids]),
                    "crossover",
                    selected=selected,
                    anim_edges=anim_edges,
                    anim_nodes=anim_nodes,
                    edge_t=float(t),
                    node_t=float(t),
                )
            )

        for child in new_row:
            uid_map[child.uid] = child
        done = _edges_from_map(uid_map, links)
        for _ in range(4):
            timeline.append(PopTreeFrame(list(uid_map.values()), done, "hold"))

    final = list(uid_map.values())
    final_edges = _edges_from_map(uid_map, links)
    for _ in range(16):
        timeline.append(PopTreeFrame(final, final_edges, "hold"))

    return timeline


def new_ga_fig():
    fig = plt.figure(figsize=(7.2, 7.2), dpi=100, facecolor=GA_PAPER)
    ax = fig.add_axes([0.05, 0.05, 0.90, 0.90])
    ax.set_facecolor(GA_PAPER)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def render_pop_tree_frame(step: PopTreeFrame) -> Image.Image:
    fig, ax = new_ga_fig()
    ax.set_xlim(-3.05, 3.05)
    ax.set_ylim(-3.05, 3.05)

    for gy in np.linspace(2.65, 2.65 - 0.58 * 9, 10):
        ax.axhline(gy, color=GA_FAINT, lw=0.5, ls=(0, (1, 6)), alpha=0.55, zorder=0)

    selected = set(step.selected)

    for x1, y1, x2, y2 in step.edges_done:
        ax.plot([x1, x2], [y1, y2], color=GA_LINE, lw=1.05, solid_capstyle="round", alpha=0.5, zorder=1)

    for x1, y1, x2, y2 in step.anim_edges:
        ax.plot([x1, x2], [y1, y2], color=GA_INK, lw=1.2, solid_capstyle="round", alpha=0.8, zorder=2)

    def draw_node(n: PopNode, scale: float = 1.0) -> None:
        r = 0.105 + 0.02 * max(0, min(1, (n.fitness + 1) / 1.2))
        r *= 0.3 + 0.7 * scale
        sel = n.uid in selected and step.phase in ("select", "crossover")
        ax.add_patch(
            Circle(
                (n.x, n.y),
                r,
                facecolor=GA_PAPER if sel else GA_INK,
                edgecolor=GA_INK,
                lw=1.3,
                zorder=4,
            )
        )
        if sel:
            ax.add_patch(Circle((n.x, n.y), r + 0.065, fill=False, ec=GA_INK, lw=0.85, zorder=3))

    for n in step.nodes:
        draw_node(n)
    for n in step.anim_nodes:
        draw_node(n, scale=step.node_t)

    ax.add_patch(plt.Rectangle((-3.05, -3.05), 6.1, 6.1, fill=False, ec=GA_MUTED, lw=0.9, zorder=10))
    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_ga_gif(path: Path) -> None:
    rng = np.random.default_rng(7)
    timeline = build_pop_tree_timeline(rng)
    frames = [render_pop_tree_frame(s) for s in timeline]
    save_gif(frames, Path(path), duration_ms=95)
    frames[-1].save(Path(path).with_suffix(".png").with_name("algoritmo_genetico_frame.png"))

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
