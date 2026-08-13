"""Report addresses the raster layer can never show, at the deepest zoom.

The check runs once per build, on the deepest rendered zoom, because that is
what makes a finding permanent: the deepest zoom is the last tile a client
fetches (it upscales past it), so an address hidden there is hidden at *every*
zoom of the raster layer.  The same address at z17 is unremarkable -- it is
simply waiting for the reader to zoom in.

Two kinds are reported:

  stacked     -- the dot sits within a dot's width of another address's dot, so
                 the two read as one point.  Almost always identical source
                 coordinates (a tower's doors all geocoded to the building
                 centroid, or a duplicated row), which no zoom pulls apart --
                 hence a data problem to take upstream, not a placement one to
                 tune.
  unlabelled  -- the address has a dot but label placement found nowhere to put
                 its number, at any slot or font size on the ladder (see
                 raster.py).  The dot is still drawn; the number is not.

Neither is fatal, so the build carries on.  The value of the report is that it
turns "dropped=96" in the log into a list of addresses someone can look up in
the source data and on the map.
"""

import csv
import math
import os
from collections import Counter, defaultdict

# Centre distance, in zoom pixels, below which two dots read as one: a dot is a
# filled circle of raster.DOT_RADIUS, so centres closer than its diameter leave
# no visible gap.  raster passes its own value in; this is only the default.
STACK_DISTANCE = 4.0
# How many streets to name in the console summary.
_TOP_STREETS = 3


def report(cfg, zoom, rows, points, placements, labelled,
           stack_distance=STACK_DISTANCE):
    """Run the hidden-address check for one zoom: print it, write the CSV.

    ``rows`` is the source identity of each address -- (lon, lat, number, unit,
    street) in slim-file order -- and ``points``/``placements`` are the
    same-length, same-order render lists from raster.py.  ``labelled`` says
    whether this zoom draws labels at all; where it does not, only stacking can
    be judged.

    Returns {"stacked": n, "unlabelled": n, "addresses": n, "path": str|None}.
    """
    stacked = _stacked(points, stack_distance)
    unlabelled = _unlabelled(points, placements) if labelled else {}
    findings = _findings(rows, stacked, unlabelled)
    counts = {
        "stacked": len(stacked),
        "unlabelled": len(unlabelled),
        "addresses": len(findings),
        "path": None,
    }
    if not findings:
        print(f"Raster z{zoom}: hidden-address check clean -- all "
              f"{len(rows):,} addresses have a dot of their own"
              f"{' and a label' if labelled else ''}")
        return counts

    counts["path"] = _write(cfg, zoom, rows, findings, stacked, unlabelled)
    print(f"Raster z{zoom}: hidden-address check -- {len(stacked):,} stacked, "
          f"{len(unlabelled):,} unlabelled: {len(findings):,} of {len(rows):,} "
          f"addresses are not readable at the deepest zoom")
    worst = Counter(rows[i][4] or "(no street)" for i, _ in findings)
    named = ", ".join(f"{st} ({n:,})" for st, n in worst.most_common(_TOP_STREETS))
    print(f"Raster z{zoom}:   worst: {named}")
    print(f"Raster z{zoom}:   wrote {counts['path']}")
    return counts


def _stacked(points, distance):
    """{index: how many other dots it overlaps}, for dots less than ``distance``
    apart.

    A spatial hash with a cell the size of the test distance, so only the 9
    cells around a point are ever compared -- checking the whole city in one
    pass is the point of doing this at build time instead of by eye.
    """
    grid = defaultdict(list)
    for i, (gx, gy, _text, _st) in enumerate(points):
        grid[(int(gx // distance), int(gy // distance))].append(i)

    overlaps = {}
    for (cx, cy), members in grid.items():
        near = [j
                for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                for j in grid.get((cx + dx, cy + dy), ())]
        for i in members:
            gx, gy = points[i][0], points[i][1]
            n = sum(1 for j in near
                    if j != i
                    and math.hypot(points[j][0] - gx, points[j][1] - gy) < distance)
            if n:
                overlaps[i] = n
    return overlaps


def _unlabelled(points, placements):
    """{index: the label it wanted} for addresses whose label was dropped.

    A point with no text at all (no housenumber in the source) is not a
    placement failure and is left out -- there was never a number to draw.
    """
    return {i: points[i][2]
            for i, placement in enumerate(placements)
            if placement is None and points[i][2]}


def _findings(rows, stacked, unlabelled):
    """[(index, problem)], street-then-number order so the CSV reads as a list
    of places rather than of file offsets."""
    out = [(i, "+".join(k for k, hit in (("stacked", i in stacked),
                                         ("unlabelled", i in unlabelled)) if hit))
           for i in set(stacked) | set(unlabelled)]
    out.sort(key=lambda f: (rows[f[0]][4], _number_key(rows[f[0]][2]),
                            _number_key(rows[f[0]][3])))
    return out


def _number_key(number):
    """Sort 9 before 100: leading digits numerically, the whole string after."""
    digits = ""
    for ch in number:
        if not ch.isdigit():
            break
        digits += ch
    return (int(digits) if digits else 0, number)


def _write(cfg, zoom, rows, findings, stacked, unlabelled):
    """Write the CSV alongside the other build outputs; return its path."""
    os.makedirs(cfg.build_dir, exist_ok=True)
    path = os.path.join(cfg.build_dir, f"{cfg.slug}-hidden-z{zoom}.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["lon", "lat", "housenumber", "unit", "street",
                    "problem", "stacked_with", "label"])
        for i, problem in findings:
            lon, lat, number, unit, street = rows[i]
            w.writerow([f"{lon:.7f}", f"{lat:.7f}", number, unit, street,
                        problem, stacked.get(i, 0), unlabelled.get(i, "")])
    return path
