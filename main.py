"""gta6-trophy-rarity-forecast-01: saturating-growth forecaster (Python 3.13, stdlib only).

For each trophy, Exophase earn rates are cumulative proportions of tracked
players. They rise as players earn trophies but can fall when the tracked
base grows faster than earners, so a straight-line trend overshoots. This
program fits, per trophy, a logistic-ceiling growth model and falls back to
persistence when there is no movement to fit:

  - collect (t_hours, rate) from history, t measured from the first read;
  - if fewer than 3 valid points, no movement, or the max rate is >= 99%:
    persistence (median = last rate);
  - else grid-search a ceiling R, fitting ln(r / (R - r)) = a + b*t by least
    squares for each R and keeping the best fit; predict at the target,
    capping the logit move from the last rate at 2.5;
  - spread combines the fit residual (logit space) with a random-walk term
    0.5 * sqrt(horizon_days), and quantiles are normal on the logit scale.

Quantiles are rounded to 4 decimals half-up, clamped to [0.01, 99.99] and
forced non-decreasing. Output is deterministic, offline, stdlib only.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]
SIGMA_RW = 0.5  # logit spread per sqrt(day) of the random-walk term
MAX_LOGIT_MOVE = 2.5
GRID_N = 25


def clamp(x: float) -> float:
    return min(99.99, max(0.01, x))


def logit(pct: float) -> float:
    p = clamp(pct)
    return math.log(p / (100 - p))


def sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def round4(x: float) -> float | int:
    """x rounded to 4 decimals, half up (like the starter)."""
    scaled = x * 10000
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 10000
    return int(value) if value.is_integer() else value


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ols(xs, ys):
    """Least-squares y = a + b*x. Returns (a, b) or None if degenerate."""
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    return my - b * mx, b


def std(values):
    n = len(values)
    if n == 0:
        return 0.0
    m = sum(values) / n
    return math.sqrt(sum((v - m) ** 2 for v in values) / n)


def quantiles(r_med: float, sigma: float):
    lg = logit(r_med)
    qs = []
    prev = None
    for z in Z:
        q = clamp(100 * sigmoid(lg + z * sigma))
        q = round4(q)
        if prev is not None and q < prev:
            q = prev
        qs.append(q)
        prev = q
    return qs


def forecast_trophy(points, r_last, horizon_days, t_target):
    """points: list of (t_hours, rate) valid points, oldest first.
    Returns (median_rate, sigma)."""
    rw = SIGMA_RW * math.sqrt(horizon_days)
    n = len(points)
    if n == 0:
        return 0.01, rw
    rates = [r for _, r in points]
    r_max = max(rates)
    r_min = min(rates)
    if n < 3 or r_max == r_min or r_max >= 99.0:
        return r_last, rw

    ts = [t for t, _ in points]
    lo = max(r_max * 1.05, r_max + 0.5)
    if lo >= 100.0:
        return r_last, rw

    # Log-spaced grid of ceilings from lo to 100.
    best = None  # (sse, R, a, b)
    for k in range(GRID_N):
        frac = k / (GRID_N - 1)
        r_ceil = lo * (100.0 / lo) ** frac
        ys = [math.log(r / (r_ceil - r)) for _, r in points]
        fit = ols(ts, ys)
        if fit is None:
            continue
        a, b = fit
        sse = sum((y - (a + b * t)) ** 2 for (t, _), y in zip(points, ys))
        if best is None or sse < best[0]:
            best = (sse, r_ceil, a, b)
    if best is None:
        return r_last, rw
    _, r_ceil, a, b = best

    y_pred = a + b * t_target
    r_med = r_ceil / (1.0 + math.exp(-y_pred)) if y_pred > -700 else 0.01
    r_med = clamp(r_med)
    # Cap the logit move from the last rate.
    move = logit(r_med) - logit(r_last)
    if abs(move) > MAX_LOGIT_MOVE:
        target_logit = logit(r_last) + math.copysign(MAX_LOGIT_MOVE, move)
        r_med = clamp(100 * sigmoid(target_logit))

    # Fit residuals in logit(rate) space.
    resid = []
    for t, r in points:
        r_fit = r_ceil / (1.0 + math.exp(-(a + b * t)))
        resid.append(logit(r) - logit(r_fit))
    sigma_fit = min(0.5, max(0.05, std(resid)))
    sigma = math.sqrt(sigma_fit ** 2 + rw ** 2)
    return r_med, sigma


def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    if not case["history"]:
        raise ValueError(f"{name} has no history")
    horizon_days = case["horizon_minutes"] / 1440
    t0 = parse_time(case["history"][0]["at"])
    t_target = (parse_time(case["target"]) - t0).total_seconds() / 3600.0

    rows = []
    for i, trophy in enumerate(case["trophies"]):
        points = []
        for read in case["history"]:
            rate = read["rates"][i]
            if rate is None:
                continue
            t = (parse_time(read["at"]) - t0).total_seconds() / 3600.0
            points.append((t, clamp(rate)))
        r_last = points[-1][1] if points else 0.01
        r_med, sigma = forecast_trophy(points, r_last, horizon_days, t_target)
        rows.append({"id": trophy["id"], "q": quantiles(r_med, sigma)})
    return {"trophies": rows}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
