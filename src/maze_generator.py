"""Generate synthetic maze images that the solver can read.

The mazes are "perfect" mazes (exactly one path between any two cells) carved
with a recursive-backtracker (randomised depth-first search). They are rendered
with thick walls and wide corridors so the solver never leaks through a wall,
and a green start marker and a red end marker are drawn in opposite corners,
matching the colour convention in `maze_solver.py`.

Run this file to (re)create the example images used by the README and demo:

    python src/maze_generator.py
"""

from pathlib import Path
import random

import numpy as np
from skimage.io import imsave

# Marker colours (bright enough to survive Otsu, clearly non-grey).
START_COLOR = (60, 200, 90)      # green  -> start
END_COLOR = (220, 60, 60)        # red    -> end


def carve(width, height, seed):
    """Carve a perfect maze and return a boolean wall grid.

    The grid has shape (2*height + 1, 2*width + 1): odd/odd cells are always
    passages, the cells between them are walls that get removed when two cells
    are joined. True = wall, False = passage.
    """
    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), width * height + 100))

    rng = random.Random(seed)
    grid = np.ones((2 * height + 1, 2 * width + 1), dtype=bool)
    visited = [[False] * width for _ in range(height)]

    def visit(cy, cx):
        visited[cy][cx] = True
        grid[2 * cy + 1, 2 * cx + 1] = False          # open this cell
        neighbours = [(cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)]
        rng.shuffle(neighbours)
        for ny, nx in neighbours:
            if 0 <= ny < height and 0 <= nx < width and not visited[ny][nx]:
                # Remove the wall between (cy, cx) and (ny, nx).
                grid[cy + ny + 1, cx + nx + 1] = False
                visit(ny, nx)

    visit(0, 0)

    # Open an entrance (top-left) and an exit (bottom-right) for the classic look.
    grid[0, 1] = False
    grid[2 * height, 2 * width - 1] = False
    return grid


def _draw_marker(rgb, grid_row, grid_col, scale, color):
    """Paint a filled square marker centred on a passage cell."""
    cy = int((grid_row + 0.5) * scale)
    cx = int((grid_col + 0.5) * scale)
    half = max(3, scale // 3)
    rgb[cy - half:cy + half, cx - half:cx + half] = color


def render(grid, width, height, scale=16):
    """Turn a wall grid into an RGB image with start/end markers."""
    # Upscale each grid unit to a scale x scale block: walls black, free white.
    free = ~grid
    big = np.kron(free, np.ones((scale, scale), dtype=bool))
    gray = np.where(big, 255, 0).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=-1)      # (H, W, 3)

    # Start in the top-left cell, end in the bottom-right cell.
    _draw_marker(rgb, 1, 1, scale, START_COLOR)
    _draw_marker(rgb, 2 * height - 1, 2 * width - 1, scale, END_COLOR)
    return rgb


def generate_maze(width, height, seed, scale=16):
    """Carve and render a maze in one call; returns an RGB uint8 image."""
    grid = carve(width, height, seed)
    return render(grid, width, height, scale)


if __name__ == "__main__":
    import sys

    # Let `import maze_solver` work when run as `python src/maze_generator.py`.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from maze_solver import solve_maze_image

    examples = Path(__file__).resolve().parent.parent / "examples"
    examples.mkdir(exist_ok=True)

    specs = [
        ("maze_small.png", 10, 10, 1),
        ("maze_medium.png", 16, 16, 2),
        ("maze_large.png", 24, 24, 3),
    ]
    for name, w, h, seed in specs:
        image = generate_maze(w, h, seed)
        path = examples / name
        imsave(path, image)
        # Round-trip: the generated maze must be solvable end-to-end.
        result = solve_maze_image(path)
        status = "reached" if result.reached else "FAILED"
        print("%-16s %dx%d  %s  (%s, %d path px)"
              % (name, w, h, str(image.shape), status, len(result.path)))
        assert result.reached, "Generated maze %s is not solvable!" % name
    print("All example mazes generated and verified.")
