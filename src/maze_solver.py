"""Solve an image of a maze with classic image processing.

The pipeline treats a maze picture as an image-processing problem and never uses
a graph search on a hand-built grid:

    load -> Otsu binarize -> crop to the maze -> seal wall gaps and the outer
    border (only when it keeps the maze connected) -> detect the coloured
    start/end markers -> Fast-Marching geodesic distance from the start ->
    steepest-descent backtrace -> centre the path in the corridors -> overlay.

The geodesic-distance idea comes from Cris Luengo's "Solving mazes using image
analysis" (https://www.crisluengo.net/archives/277/); the distance field itself
is computed with scikit-fmm.

Every function maps to one step so the notebook and the CLI can reuse the exact
same code. `solve_maze_image` runs the whole pipeline and returns all the
intermediate results for visualisation.
"""

from dataclasses import dataclass

import numpy as np
import skfmm
from scipy.ndimage import distance_transform_edt
from skimage.io import imread
from skimage.color import rgb2gray, rgba2rgb
from skimage.filters import threshold_otsu
from skimage.morphology import flood, closing, dilation, disk
from skimage.measure import label

# Colour convention for the markers drawn on the maze.
# A blue / cyan / green marker is the start; a red marker is the end.
_COLOUR_MARGIN = 40      # how much a channel must dominate to count as coloured
_COLOUR_DIFF = 30        # channel spread above which a pixel is "coloured", not grey
_CONNECTIVITY_GUARD = 0.94   # a wall-adding seal is kept only if the main
#                              corridor network keeps at least this fraction.


@dataclass
class MazeSolution:
    """Everything produced while solving a maze, for inspection or plotting."""

    rgb: np.ndarray            # original colour image (uint8)
    maze: np.ndarray           # cropped boolean corridor mask (True = free)
    offset: tuple              # (row, col) offset of the crop in the original image
    start_px: tuple            # start seed in cropped coordinates
    end_px: tuple              # end seed in cropped coordinates
    flood_start: np.ndarray    # pixels reachable from the start
    flood_end: np.ndarray      # pixels reachable from the end
    distance_map: np.ndarray   # masked geodesic distance from the start
    path: list                 # list of (row, col) corridor pixels, end -> start
    solution: np.ndarray       # original image with the path painted red
    reached: bool              # did the backtrace reach the start?


def load_image(path):
    """Read an image file and return an 8-bit RGB array (alpha dropped)."""
    raw = imread(path)
    if raw.ndim == 3 and raw.shape[2] == 4:
        raw = rgba2rgb(raw)                 # RGBA -> RGB (returns floats 0..1)
    if raw.dtype == np.uint8:
        return raw
    return (raw * 255).astype(np.uint8)


def binarize(rgb):
    """Split the image into free space (True) and walls (False) with Otsu."""
    gray = rgb2gray(rgb)
    threshold = threshold_otsu(gray)
    return gray > threshold


def _channels(rgb):
    """Return the red, green and blue channels as signed integer arrays."""
    return (rgb[:, :, 0].astype(int),
            rgb[:, :, 1].astype(int),
            rgb[:, :, 2].astype(int))


def crop_to_walls(rgb, free_mask):
    """Crop to the bounding box of the (non-coloured) walls.

    Walls are dark, but so are the coloured arrows in grayscale, so coloured
    pixels are excluded first. Using the wall extent works for any maze shape
    (square or circular) and removes the outside margin, which stops the
    shortest path from cheating by routing around the maze.
    """
    red, green, blue = _channels(rgb)
    colored = ((np.abs(red - green) > _COLOUR_DIFF) |
               (np.abs(red - blue) > _COLOUR_DIFF) |
               (np.abs(green - blue) > _COLOUR_DIFF))
    wall = (~free_mask) & (~colored)

    wall_rows = np.where(wall.any(axis=1))[0]
    wall_cols = np.where(wall.any(axis=0))[0]
    r0, r1 = int(wall_rows.min()), int(wall_rows.max())
    c0, c1 = int(wall_cols.min()), int(wall_cols.max())

    maze = free_mask[r0:r1 + 1, c0:c1 + 1]
    return maze, (r0, c0)


def main_network_size(mask):
    """Pixel count of the largest 4-connected free region (main corridor net)."""
    regions = label(mask, connectivity=1)
    sizes = np.bincount(regions.ravel())
    sizes[0] = 0                            # label 0 is the walls
    return int(sizes.max()) if sizes.size > 1 else 0


def seal_walls(maze):
    """Close hairline gaps in the walls, but only if the maze stays connected.

    Thin, anti-aliased walls can lose pixels under Otsu, leaving 1-pixel gaps
    that the flood fill and the marching wave leak through. A morphological
    closing of the walls fills these gaps. On a dense maze the same closing can
    also close a genuine 1-pixel corridor and split the maze apart, so we keep
    the seal only when the main corridor network survives (>= 94 %).
    """
    sealed = ~closing(~maze, disk(1))
    if main_network_size(sealed) >= _CONNECTIVITY_GUARD * main_network_size(maze):
        return sealed, True
    return maze, False


def seal_border(maze):
    """Turn a thin outer frame into wall, but only if the maze stays connected.

    A maze drawn without a solid outer wall has a free strip ringing it, and the
    path could run around the outside. Framing removes that strip. A circular
    maze instead has a real corridor along its boundary, so framing would cut it;
    the same 94 % connectivity guard skips the frame in that case.
    """
    framed = maze.copy()
    border = max(3, int(round(0.01 * min(maze.shape))))
    framed[:border, :] = False
    framed[-border:, :] = False
    framed[:, :border] = False
    framed[:, -border:] = False
    if main_network_size(framed) >= _CONNECTIVITY_GUARD * main_network_size(maze):
        return framed, True
    return maze, False


def find_endpoints(rgb, maze, offset):
    """Locate the start and end seeds from the coloured markers.

    Blue / cyan / green = start, red = end. Each marker's centroid is mapped
    into cropped coordinates and snapped to the nearest pixel of the main
    corridor network (not the nearest free pixel), which avoids tiny specks and
    re-anchors a marker that the border frame pushed into a wall.
    """
    red, green, blue = _channels(rgb)
    start_marker = ((blue - red) > _COLOUR_MARGIN) | ((green - red) > _COLOUR_MARGIN)
    end_marker = ((red - blue) > _COLOUR_MARGIN) & ((red - green) > _COLOUR_MARGIN)
    if start_marker.sum() == 0:
        raise ValueError("Could not find a blue/cyan/green START marker.")
    if end_marker.sum() == 0:
        raise ValueError("Could not find a red END marker.")

    r0, c0 = offset
    start_center = np.argwhere(start_marker).mean(axis=0)
    end_center = np.argwhere(end_marker).mean(axis=0)
    start_point = (start_center[0] - r0, start_center[1] - c0)
    end_point = (end_center[0] - r0, end_center[1] - c0)

    regions = label(maze, connectivity=1)
    counts = np.bincount(regions.ravel())
    counts[0] = 0
    network_coords = np.argwhere(regions == counts.argmax())

    def snap(point):
        d = ((network_coords[:, 0] - point[0]) ** 2 +
             (network_coords[:, 1] - point[1]) ** 2)
        row, col = network_coords[d.argmin()]
        return (int(row), int(col))

    return snap(start_point), snap(end_point)


def flood_fill(maze, seed):
    """Boolean image of every corridor pixel reachable from `seed`.

    4-connectivity matches the path solver, so the flood cannot slip diagonally
    through a one-pixel wall corner.
    """
    return flood(maze, seed, connectivity=1)


def geodesic_distance(maze, start_px):
    """Fast-Marching geodesic distance from the start to every corridor pixel.

    `phi` is -1 at the start and +1 elsewhere; the walls are masked so the wave
    only travels through corridors.
    """
    phi = np.ones(maze.shape)
    phi[start_px] = -1
    phi = np.ma.MaskedArray(phi, mask=~maze)
    return skfmm.distance(phi)


def trace_path(distance_map, start_px, end_px):
    """Steepest descent from the end to the start over the distance field.

    Only orthogonal moves are allowed, so a step between two corridor pixels can
    never cut across a wall corner. Returns (path, reached).
    """
    work = distance_map.filled(np.inf)
    neighbours = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    current = end_px
    path = [current]
    steps = 0
    while current != start_px and steps < work.size:
        row, col = current
        best_pixel = None
        best_value = work[row, col]
        for d_row, d_col in neighbours:
            n_row, n_col = row + d_row, col + d_col
            if (0 <= n_row < work.shape[0] and 0 <= n_col < work.shape[1]
                    and work[n_row, n_col] < best_value):
                best_value = work[n_row, n_col]
                best_pixel = (n_row, n_col)
        if best_pixel is None:
            break
        current = best_pixel
        path.append(current)
        steps += 1
    return path, current == start_px


def center_path(path, maze):
    """Nudge the path toward the corridor centre lines.

    The steepest-descent path hugs the inside of every bend. We push each
    interior point toward a larger distance-to-wall value (the corridor middle)
    while keeping it within one pixel of both neighbours, so the path stays
    connected. The number of passes is about the corridor half-width.
    """
    wall_distance = distance_transform_edt(maze)
    if not maze.any():
        return path
    passes = int(max(8, round(np.median(wall_distance[maze]))))
    moves = [(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)]
    centered = list(path)
    for _ in range(passes):
        for i in range(1, len(centered) - 1):
            row, col = centered[i]
            prev_row, prev_col = centered[i - 1]
            next_row, next_col = centered[i + 1]
            best = (row, col)
            best_distance = wall_distance[row, col]
            for d_row, d_col in moves:
                r, c = row + d_row, col + d_col
                if (0 <= r < maze.shape[0] and 0 <= c < maze.shape[1]
                        and maze[r, c] and wall_distance[r, c] > best_distance):
                    if (max(abs(r - prev_row), abs(c - prev_col)) <= 1
                            and max(abs(r - next_row), abs(c - next_col)) <= 1):
                        best = (r, c)
                        best_distance = wall_distance[r, c]
            centered[i] = best
    return centered


def draw_solution(rgb, maze, path, offset):
    """Paint the (thickened, corridor-clipped) path red on the colour image."""
    path_mask = np.zeros(maze.shape, dtype=bool)
    for (row, col) in path:
        path_mask[row, col] = True
    thick_path = dilation(path_mask, disk(2)) & maze

    r0, c0 = offset
    solution = rgb.copy()
    rows, cols = np.where(thick_path)
    solution[rows + r0, cols + c0] = (255, 0, 0)
    return solution


def solve_maze_image(path):
    """Run the whole pipeline on an image file and return a `MazeSolution`."""
    rgb = load_image(path)
    free_mask = binarize(rgb)
    maze, offset = crop_to_walls(rgb, free_mask)
    maze, _ = seal_walls(maze)
    maze, _ = seal_border(maze)

    start_px, end_px = find_endpoints(rgb, maze, offset)
    flood_start = flood_fill(maze, start_px)
    flood_end = flood_fill(maze, end_px)
    if not flood_start[end_px]:
        raise ValueError("The start cannot reach the end: this maze is unsolvable.")

    distance_map = geodesic_distance(maze, start_px)
    raw_path, reached = trace_path(distance_map, start_px, end_px)
    path_centered = center_path(raw_path, maze)
    solution = draw_solution(rgb, maze, path_centered, offset)

    return MazeSolution(
        rgb=rgb, maze=maze, offset=offset,
        start_px=start_px, end_px=end_px,
        flood_start=flood_start, flood_end=flood_end,
        distance_map=distance_map, path=path_centered,
        solution=solution, reached=reached,
    )
