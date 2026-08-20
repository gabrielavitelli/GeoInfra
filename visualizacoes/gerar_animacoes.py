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
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.patches import Circle, Polygon
from PIL import Image

ROOT = Path(__file__).resolve().parent
BG = "#07080c"
PLUS = "#e8b84a"
MINUS = "#4f8bff"
INK = "#f3efe4"

PEAKS_CMAP = LinearSegmentedColormap.from_list(
    "peaks_geo",
    ["#08070c", "#14122a", "#2a1848", "#6a2158", "#c24a3a", "#e6b84c", "#f4e6c4"],
)


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
    """Pontos linearmente separáveis com hiperplano ótimo x + y = 0."""
    sv_plus = np.array([[1.05, 0.15], [0.18, 1.02]])
    sv_minus = np.array([[-0.60, -0.60]])
    plus = np.vstack(
        [
            sv_plus,
            [
                [1.55, 0.55],
                [1.95, 1.15],
                [2.25, 0.45],
                [1.35, 1.65],
                [2.15, 1.85],
                [2.55, 1.05],
                [1.75, 2.15],
                [0.85, 1.55],
                [2.45, 2.05],
            ],
        ]
    )
    minus = np.vstack(
        [
            sv_minus,
            [
                [-1.50, -0.55],
                [-1.90, -1.20],
                [-2.20, -0.40],
                [-1.35, -1.70],
                [-2.10, -1.90],
                [-2.55, -1.05],
                [-1.70, -2.20],
                [-0.85, -1.55],
                [-2.40, -2.10],
            ],
        ]
    )
    return plus, minus, sv_plus, sv_minus


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
    plus, minus, sv_plus, sv_minus = svm_points()
    theta_star = best_theta(plus, minus)
    # Sweep a neighbourhood then settle on the optimum.
    sweep = np.concatenate(
        [
            np.linspace(theta_star - 0.72, theta_star + 0.72, 36),
            np.full(8, theta_star + 0.72),
        ]
    )
    frames: list[Image.Image] = []

    # 1. Points appear
    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, theta_star, a, 0, 0, 0, 0, 0
            )
        )
    # 2. Convex hulls
    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, theta_star, 1, a, 0, 0, 0, 0
            )
        )
    # 3. Orientation sweep of the separating slab
    for k, th in enumerate(sweep):
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, float(th), 1, 0.55, 1, 0, 0, 0
            )
        )
    # 4. Return to optimum
    th0 = float(sweep[-1])
    for i in range(16):
        a = ease((i + 1) / 16)
        th = th0 + a * (theta_star - th0)
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, th, 1, 0.35 * (1 - a), 1, 0, 0, 0
            )
        )
    # 5. Support vectors and normal
    for i in range(14):
        a = ease((i + 1) / 14)
        frames.append(
            render_svm_frame(
                plus, minus, sv_plus, sv_minus, theta_star, 1, 0, 1, a, a, 0
            )
        )
    # 6. Hold / pulse with half-spaces
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
            )
        )

    save_gif(frames, path, duration_ms=75)
    frames[-1].save(path.with_suffix(".png").with_name("svm_frame.png"))


# ---------------------------------------------------------------------------
# Genetic algorithm on the peaks landscape
# ---------------------------------------------------------------------------

def peaks(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (
        3.0 * (1 - x) ** 2 * np.exp(-(x**2) - (y + 1) ** 2)
        - 10.0 * (x / 5.0 - x**3 - y**5) * np.exp(-(x**2) - y**2)
        - 1.0 / 3.0 * np.exp(-((x + 1) ** 2) - y**2)
    )


def run_ga(rng: np.random.Generator, n_pop: int = 48, n_gen: int = 18) -> np.ndarray:
    """Devolve histórico (n_gen+1, n_pop, 2)."""
    pop = rng.uniform(-3.0, 3.0, size=(n_pop, 2))
    hist = [pop.copy()]
    for g in range(n_gen):
        fit = peaks(pop[:, 0], pop[:, 1])
        elite_n = 4
        elite_idx = np.argsort(fit)[-elite_n:]
        elite = pop[elite_idx].copy()
        new = [elite]
        sigma = 0.62 * (0.91**g)
        while sum(len(chunk) for chunk in new) < n_pop:
            i = tournament(fit, rng)
            j = tournament(fit, rng)
            alpha = rng.uniform(0.25, 0.75)
            child = alpha * pop[i] + (1 - alpha) * pop[j]
            child = child + rng.normal(0.0, sigma, size=2)
            if rng.random() < 0.12:
                child = rng.uniform(-3.0, 3.0, size=2)
            child = np.clip(child, -3.0, 3.0)
            new.append(child[None, :])
        pop = np.vstack(new)[:n_pop]
        hist.append(pop.copy())
    return np.stack(hist, axis=0)


def tournament(fit: np.ndarray, rng: np.random.Generator, k: int = 3) -> int:
    idx = rng.choice(len(fit), size=k, replace=False)
    return int(idx[np.argmax(fit[idx])])


def render_ga_frame(
    xs,
    ys,
    zz,
    pop: np.ndarray,
    trails: list[np.ndarray],
    gen_alpha: float,
    landscape_alpha: float,
) -> Image.Image:
    fig, ax = new_axes()
    lim = 3.05
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)

    norm = Normalize(vmin=-6.2, vmax=8.1)
    ax.contourf(
        xs,
        ys,
        zz,
        levels=18,
        cmap=PEAKS_CMAP,
        norm=norm,
        alpha=0.96 * landscape_alpha,
        zorder=1,
        extend="both",
    )
    ax.contour(
        xs,
        ys,
        zz,
        levels=18,
        colors="#f3efe4",
        linewidths=0.45,
        alpha=0.18 * landscape_alpha,
        zorder=2,
    )
    ax.add_patch(
        plt.Rectangle(
            (-lim, -lim),
            2 * lim,
            2 * lim,
            fill=False,
            ec="#d8c48a",
            lw=1.15,
            alpha=0.35,
            zorder=8,
        )
    )

    zpop = peaks(pop[:, 0], pop[:, 1])
    t = np.clip((zpop + 6.0) / 14.0, 0, 1)
    sizes = 42 + 55 * t

    for fade, old in trails:
        ax.scatter(
            old[:, 0],
            old[:, 1],
            s=26,
            c="#f4e6c4",
            alpha=0.22 * fade * gen_alpha,
            linewidths=0,
            zorder=4,
        )

    ax.scatter(
        pop[:, 0],
        pop[:, 1],
        s=sizes * 3.4,
        c="#f7edd0",
        linewidths=0,
        alpha=0.18 * gen_alpha,
        zorder=5,
    )
    ax.scatter(
        pop[:, 0],
        pop[:, 1],
        s=sizes,
        c="#f7edd0",
        edgecolors="#161018",
        linewidths=0.55,
        alpha=gen_alpha,
        zorder=6,
    )

    elite = pop[np.argsort(zpop)[-4:]]
    ax.scatter(
        elite[:, 0],
        elite[:, 1],
        s=92,
        c=PLUS,
        edgecolors="#161018",
        linewidths=0.55,
        alpha=gen_alpha,
        zorder=7,
    )
    for p in elite:
        ax.add_patch(
            Circle(
                (p[0], p[1]),
                0.16,
                fill=False,
                ec=INK,
                lw=1.15,
                alpha=0.92 * gen_alpha,
                zorder=8,
            )
        )

    img = fig_to_image(fig)
    plt.close(fig)
    return img


def make_ga_gif(path: Path) -> None:
    rng = np.random.default_rng(11)
    hist = run_ga(rng)
    grid = np.linspace(-3.0, 3.0, 240)
    xs, ys = np.meshgrid(grid, grid)
    zz = peaks(xs, ys)

    frames: list[Image.Image] = []
    empty = hist[0]

    for i in range(12):
        a = ease((i + 1) / 12)
        frames.append(render_ga_frame(xs, ys, zz, empty, [], 0.0, a))

    for i in range(10):
        a = ease((i + 1) / 10)
        frames.append(render_ga_frame(xs, ys, zz, empty, [], a, 1.0))

    trails: list[tuple[float, np.ndarray]] = []
    n_interp = 4
    for g in range(len(hist) - 1):
        a0, a1 = hist[g], hist[g + 1]
        for k in range(n_interp):
            t = ease((k + 1) / n_interp)
            pop = (1 - t) * a0 + t * a1
            fade_trails = [(max(0.0, 1.0 - 0.28 * i), p) for i, (_, p) in enumerate(trails)]
            frames.append(render_ga_frame(xs, ys, zz, pop, fade_trails, 1.0, 1.0))
        trails.insert(0, (1.0, a1.copy()))
        trails = trails[:5]

    last = hist[-1]
    for i in range(18):
        frames.append(render_ga_frame(xs, ys, zz, last, trails, 1.0, 1.0))

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
