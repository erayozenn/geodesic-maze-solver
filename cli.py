"""Command-line interface for the geodesic maze solver.

Examples
--------
    python cli.py examples/maze_medium.png
    python cli.py my_maze.png -o solved.png
    python cli.py examples/maze_large.png --steps      # also save step figures
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")                     # save figures without a display
import matplotlib.pyplot as plt

# Make `from src.maze_solver import ...` work no matter where we are run from.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.maze_solver import solve_maze_image


def save_step_figures(result, stem):
    """Save the intermediate pipeline images next to the solved output."""
    plt.imsave("%s_1_binarized.png" % stem, result.maze, cmap="gray")

    fig, axes = plt.subplots(1, 2, figsize=(11, 5.5))
    for ax, reach, title, cmap in (
        (axes[0], result.flood_start, "Flood fill from START", "autumn"),
        (axes[1], result.flood_end, "Flood fill from END", "winter"),
    ):
        ax.imshow(result.maze, cmap="gray")
        ax.imshow(np.ma.masked_where(~reach, reach), cmap=cmap, alpha=0.6)
        ax.set_title(title)
        ax.axis("off")
    fig.savefig("%s_2_floodfill.png" % stem, bbox_inches="tight")
    plt.close(fig)

    fig = plt.figure(figsize=(6.5, 6))
    heat = plt.imshow(result.distance_map, cmap="viridis")
    plt.colorbar(heat, label="geodesic distance from start")
    plt.axis("off")
    fig.savefig("%s_3_distance.png" % stem, bbox_inches="tight")
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Solve a maze image with morphology and the Fast Marching Method.")
    parser.add_argument("image", help="path to the maze image (PNG/JPG)")
    parser.add_argument("-o", "--output", help="where to write the solved image "
                        "(default: solved_<name>.png)")
    parser.add_argument("--steps", action="store_true",
                        help="also save the binarize / flood / distance figures")
    args = parser.parse_args(argv)

    in_path = Path(args.image)
    if not in_path.exists():
        parser.error("image not found: %s" % in_path)

    out_path = Path(args.output) if args.output else Path("solved_%s.png" % in_path.stem)

    result = solve_maze_image(in_path)
    plt.imsave(out_path, result.solution)

    print("Maze:            %s" % in_path)
    print("Reached start:   %s" % result.reached)
    print("Path length:     %d pixels" % len(result.path))
    print("Solved image ->  %s" % out_path)

    if args.steps:
        save_step_figures(result, str(out_path.with_suffix("")))
        print("Step figures ->  %s_1_binarized.png, _2_floodfill.png, _3_distance.png"
              % out_path.with_suffix(""))

    return 0 if result.reached else 1


if __name__ == "__main__":
    raise SystemExit(main())
