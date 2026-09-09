# Scoring

Scores are 0-100 and use only sufficiently supported metrics. Missing components are
excluded and the available weights are renormalized. Confidence is returned separately.

Weights:

- Consistency: 25%
- Win rate: 15%
- Risk-adjusted performance: 20%
- Early entry: 15%
- Trade quality: 15%
- Activity: 10%

Classification thresholds:

- `LOW SIGNAL`: 0-29
- `WATCHLIST`: 30-49
- `PROMISING`: 50-69
- `SMART WHALE`: 70-84
- `ELITE WHALE`: 85-100

Trade-derived metrics require at least five realized trades. Every score is persisted
as an immutable `TraderScore` snapshot with timestamp and component values.
