# gta6-trophy-rarity-forecast-01

Forecasts every Grand Theft Auto VI trophy's earn rate on Exophase at
2026-11-26 00:00 UTC, as five quantiles per trophy, from the house's recorded
reads up to the deadline. Entry for the make.fun bounty
"gta6-trophy-rarity-forecast-01" ($250, top 20 share).

## Run

```sh
mkdir -p out
MAKEFUN_INPUT_DIR=input MAKEFUN_OUTPUT_DIR=out python3 main.py
```

Reads every `<id>.in` in the input dir, writes `<id>.out` to the output dir.
Python 3.13, standard library only, offline, deterministic.

## Model

Per trophy, a logistic-ceiling growth model: rates are cumulative shares of
tracked players, so they grow toward a ceiling and saturate instead of
trending linearly. A grid search over ceilings fits
`ln(r / (R - r)) = a + b*t` by least squares and predicts at the target, with
the logit move from the last rate capped at 2.5. Trophies with no movement
fall back to persistence (median = last rate). Spread combines the fit
residual with a random-walk term `0.5 * sqrt(horizon_days)` on the logit
scale; quantiles are normal on that scale.
