"""Gera GIFs geométricos, sem texto, de SVM e algoritmo genético."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
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
# Algoritmo genético — raízes que crescem e se espalham (visual profissional)
# ---------------------------------------------------------------------------

# Paleta sóbria — ilustração botânica / corte geológico
SKY = "#141a22"
SOIL_LAYERS = ["#3a3530", "#322c28", "#2a2520", "#211d18"]
GROUND = "#8a8278"
TRUNK_DARK = "#3d2e22"
TRUNK_MID = "#5a4535"
CANOPY = "#3a5a3e"
CANOPY_HI = "#4d6e50"
ROOT_PALE = "#ddd0bc"
ROOT_MID = "#c4b49a"
ROOT_DEEP = "#a89478"
ROOT_GLOW = "#ebe3d4"


@dataclass
class RootBranch:
    genes: np.ndarray
    base_angle: float
    gen: int = 0
    fitness: float = 0.0
    thickness: float = 0.9

    def polyline(self, progress: float = 1.0) -> np.ndarray:
        full = genes_to_polyline(self.genes, start_angle=-math.pi / 2 + self.base_angle)
        return trim_polyline(full, progress)


def genes_to_polyline(
    genes: np.ndarray,
    base: tuple[float, float] = (0.0, 0.0),
    start_angle: float = -math.pi / 2,
) -> np.ndarray:
    x, y = base
    angle = start_angle
    pts = [(x, y)]
    for d_angle, length in genes:
        angle += float(d_angle)
        x += float(length) * math.cos(angle)
        y += float(length) * math.sin(angle)
        pts.append((x, y))
    return np.array(pts, dtype=float)


def trim_polyline(poly: np.ndarray, progress: float) -> np.ndarray:
    if progress >= 1.0 or len(poly) < 2:
        return poly
    progress = max(0.0, progress)
    seg_len = np.linalg.norm(np.diff(poly, axis=0), axis=1)
    total = float(seg_len.sum())
    if total <= 1e-9:
        return poly[:1]
    target = progress * total
    walked = 0.0
    out = [poly[0]]
    for i, length in enumerate(seg_len):
        if walked + length >= target:
            t = (target - walked) / length if length > 0 else 0.0
            out.append(poly[i] + t * (poly[i + 1] - poly[i]))
            break
        walked += length
        out.append(poly[i + 1])
    return np.array(out, dtype=float)


def branch_fitness(branch: RootBranch, sector_fill: dict[int, float] | None = None) -> float:
    """Premia profundidade, espalhamento lateral e exploração de setores vazios."""
    poly = branch.polyline(1.0)
    tip = poly[-1]
    if tip[1] > -0.08:
        return 0.02
    depth = -tip[1]
    spread = abs(tip[0])
    length = float(np.sum(np.linalg.norm(np.diff(poly, axis=0), axis=1)))
    sector = int(np.clip((tip[0] + 2.8) / 5.6 * 10, 0, 9))
    novelty = 1.0 - (sector_fill or {}).get(sector, 0.0)
    return (
        0.30 * min(spread / 2.6, 1.0)
        + 0.30 * min(depth / 2.6, 1.0)
        + 0.22 * min(length / 3.8, 1.0)
        + 0.18 * novelty
    )


def sector_fill_map(network: list[RootBranch]) -> dict[int, float]:
    counts = {i: 0 for i in range(10)}
    for b in network:
        tip = b.polyline(1.0)[-1]
        s = int(np.clip((tip[0] + 2.8) / 5.6 * 10, 0, 9))
        counts[s] += 1
    mx = max(counts.values()) or 1
    return {k: v / mx for k, v in counts.items()}


def random_branch(rng: np.random.Generator, base_angle: float, gen: int, n_seg: int = 4) -> RootBranch:
    genes = np.column_stack(
        [
            rng.normal(0.0, 0.12, n_seg),
            rng.uniform(0.40, 0.56, n_seg),
        ]
    )
    b = RootBranch(genes, base_angle, gen=gen)
    return b


def mutate_branch(
    rng: np.random.Generator,
    parent: RootBranch,
    gen: int,
    spread_out: bool,
    override_angle: float | None = None,
) -> RootBranch:
    genes = parent.genes.copy()
    genes[:, 0] += rng.normal(0.0, 0.10, len(genes))
    genes[:, 1] *= rng.uniform(0.94, 1.10, len(genes))
    if override_angle is not None:
        angle = override_angle
    else:
        outward = 1.0 if parent.base_angle >= 0 else -1.0
        delta = rng.uniform(0.06, 0.20) if spread_out else rng.uniform(-0.10, 0.10)
        angle = parent.base_angle + outward * delta
    return RootBranch(genes, angle, gen=gen, thickness=0.75)


def spawn_generation(rng: np.random.Generator, gen: int, network: list[RootBranch]) -> list[RootBranch]:
    """Gera candidatos distribuídos simetricamente — leque que alarga a cada geração."""
    n_dirs = 5 + gen // 2
    fan = 0.45 + gen * 0.18
    angles = np.linspace(-fan, fan, n_dirs)
    growing = [random_branch(rng, float(a), gen) for a in angles]

    if gen > 0 and network:
        elites = sorted(network, key=lambda b: b.fitness, reverse=True)[:4]
        for p in elites:
            for side in (-1.0, 1.0):
                if rng.random() < 0.55:
                    angle = p.base_angle + side * rng.uniform(0.12, 0.32)
                    growing.append(mutate_branch(rng, p, gen, spread_out=True, override_angle=angle))
    return growing


def evolve_spreading_network(rng: np.random.Generator, n_gen: int = 10) -> list[dict]:
    """Rede acumulativa: raízes persistem, engrossam e ocupam setores laterais."""
    network: list[RootBranch] = []
    timeline: list[dict] = []

    for g in range(n_gen):
        fill = sector_fill_map(network)
        growing = spawn_generation(rng, g, network)
        for b in growing:
            b.fitness = branch_fitness(b, fill)

        timeline.append({"network": [clone_branch(b) for b in network], "growing": growing, "gen": g})

        ranked = sorted(growing, key=lambda b: b.fitness, reverse=True)
        taken: list[RootBranch] = []
        used_sectors: set[int] = set()
        for e in ranked:
            tip = e.polyline(1.0)[-1]
            sec = int(np.clip((tip[0] + 2.8) / 5.6 * 10, 0, 9))
            if sec in used_sectors and e.fitness < 0.55:
                continue
            e.thickness = 1.0 + e.fitness * 1.8
            taken.append(clone_branch(e))
            used_sectors.add(sec)
            if len(taken) >= 3 + g // 3:
                break

        for old in network:
            old.thickness = min(old.thickness + 0.08, 3.2)
        network.extend(taken)

    timeline.append({"network": [clone_branch(b) for b in network], "growing": [], "gen": n_gen})
    return timeline


def clone_branch(b: RootBranch) -> RootBranch:
    c = RootBranch(b.genes.copy(), b.base_angle, b.gen, b.fitness, b.thickness)
    return c


def draw_soil_section(ax, alpha: float = 1.0) -> None:
    ax.axhspan(0, 3.05, facecolor=SKY, alpha=alpha, zorder=0)
    bounds = [(0, -0.7), (-0.7, -1.5), (-1.5, -2.3), (-2.3, -3.05)]
    for (y0, y1), color in zip(bounds, SOIL_LAYERS):
        ax.axhspan(y1, y0, facecolor=color, alpha=0.96 * alpha, zorder=1)
    for y in (-0.55, -1.1, -1.75, -2.45):
        ax.axhline(y, color="#4a443c", lw=0.45, alpha=0.28 * alpha, zorder=2)
    ax.axhline(0, color=GROUND, lw=2.0, alpha=0.82 * alpha, zorder=6)
    ax.plot([-3.05, 3.05], [0, 0], color="#6a6258", lw=0.6, alpha=0.35 * alpha, zorder=6)


def draw_tree_pro(ax, trunk_alpha: float, canopy_alpha: float) -> None:
    if trunk_alpha <= 0:
        return
    ax.plot([0, 0], [0, 2.05], color=TRUNK_DARK, lw=5.8, solid_capstyle="round", alpha=0.95 * trunk_alpha, zorder=7)
    ax.plot([0, 0], [0, 2.05], color=TRUNK_MID, lw=1.6, alpha=0.45 * trunk_alpha, zorder=8)
    if canopy_alpha <= 0:
        return
    from matplotlib.patches import Ellipse

    for xy, w, h, c, a in [
        ((0.0, 2.28), 1.55, 1.05, CANOPY, 0.88),
        ((-0.42, 2.05), 0.82, 0.62, CANOPY_HI, 0.45),
        ((0.45, 2.08), 0.78, 0.58, CANOPY_HI, 0.42),
    ]:
        ax.add_patch(
            Ellipse(
                xy,
                w,
                h,
                facecolor=c,
                edgecolor="#2a4030",
                lw=0.9,
                alpha=a * canopy_alpha,
                zorder=9,
            )
        )


def draw_root(ax, poly: np.ndarray, thickness: float, alpha: float, z: int, highlight: float = 0.0) -> None:
    if len(poly) < 2:
        return
    t = min(1.0, thickness / 3.0)
    core = ROOT_PALE if t < 0.45 else ROOT_MID if t < 0.75 else ROOT_DEEP
    glow_w = 2.2 + thickness * 2.8 + highlight * 1.4
    core_w = 0.55 + thickness * 1.05 + highlight * 0.5
    ax.plot(
        poly[:, 0],
        poly[:, 1],
        color=ROOT_GLOW,
        lw=glow_w,
        solid_capstyle="round",
        alpha=0.10 * alpha,
        zorder=z,
    )
    ax.plot(
        poly[:, 0],
        poly[:, 1],
        color=core,
        lw=core_w,
        solid_capstyle="round",
        alpha=0.88 * alpha,
        zorder=z + 1,
    )


def render_ga_tree_frame(
    network: list[RootBranch],
    growing: list[RootBranch],
    progress: float,
    trunk_alpha: float,
    canopy_alpha: float,
    soil_alpha: float,
    select_pulse: float = 0.0,
) -> Image.Image:
    fig, ax = new_axes()
    ax.set_facecolor(SKY)
    ax.set_xlim(-3.05, 3.05)
    ax.set_ylim(-3.05, 3.05)

    draw_soil_section(ax, soil_alpha)
    draw_tree_pro(ax, trunk_alpha, canopy_alpha)

    for b in sorted(network, key=lambda x: x.thickness):
        draw_root(ax, b.polyline(1.0), b.thickness, soil_alpha, z=3)

    if growing and progress > 0:
        fit_vals = np.array([b.fitness for b in growing])
        fmin, fmax = fit_vals.min(), fit_vals.max()
        span = max(fmax - fmin, 1e-6)
        ranked = sorted(growing, key=lambda b: b.fitness, reverse=True)
        elite = {id(b) for b in ranked[: max(2, len(ranked) // 2)]}

        for b in sorted(growing, key=lambda x: x.fitness):
            poly = b.polyline(progress)
            t = (b.fitness - fmin) / span
            is_elite = id(b) in elite
            alpha = (0.35 + 0.65 * t) * soil_alpha
            hl = select_pulse if is_elite else 0.0
            if not is_elite and select_pulse > 0.2:
                alpha *= max(0.12, 1.0 - 0.75 * select_pulse)
            th = 0.65 + t * 1.1 + (0.9 * select_pulse if is_elite else 0.0)
            draw_root(ax, poly, th, alpha, z=4, highlight=hl)

    ax.add_patch(
        plt.Rectangle((-3.05, -3.05), 6.1, 6.1, fill=False, ec="#2c3340", lw=1.0, zorder=10)
    )
    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_ga_gif(path: Path) -> None:
    rng = np.random.default_rng(23)
    timeline = evolve_spreading_network(rng)

    frames: list[Image.Image] = []

    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(
            render_ga_tree_frame([], [], 0.0, a * 0.95, 0.0, a, 0.0)
        )
    for i in range(8):
        a = ease((i + 1) / 8)
        frames.append(
            render_ga_tree_frame([], timeline[0]["growing"], 0.0, 1.0, a, 1.0, 0.0)
        )

    for entry in timeline[:-1]:
        growing = entry["growing"]
        network = entry["network"]
        steps = 9 if entry["gen"] == 0 else 7
        for s in range(steps):
            prog = ease((s + 1) / steps)
            frames.append(
                render_ga_tree_frame(network, growing, prog, 1.0, 1.0, 1.0, 0.0)
            )
        for s in range(6):
            pulse = ease((s + 1) / 6)
            frames.append(
                render_ga_tree_frame(network, growing, 1.0, 1.0, 1.0, 1.0, pulse)
            )

    final = timeline[-1]["network"]
    for _ in range(16):
        frames.append(render_ga_tree_frame(final, [], 1.0, 1.0, 1.0, 1.0, 0.0))

    save_gif(frames, path, duration_ms=80)
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
