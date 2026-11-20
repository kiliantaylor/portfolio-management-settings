"""Supervisor sizing confidence as a Platt-scaled probability (Kilian 2026-10-01).

Supervisors used to pick a label (low / medium / high) and almost never said high: the label invites the same hedging
toward the middle that the forecasters' ladders show. They now report `sizing.confidence`, a raw probability in [0, 1]
that the stock's 12-month Sharpe ratio (excess total return over T-bills / realized vol) beats the S&P 500's. Like the forecasters' P(close >= spot), it is
sharpened with the paper's Platt coefficient, p' = sigmoid(a * logit p), a = sqrt(3), and the label used by allocate.py
is read off the scaled number with the thresholds in data/review_settings.json (confidence_thresholds). Old runs that
only have a label keep it. The raw number is a forecast of a scorable event, so it can be re-fitted once outcomes exist.

Usage: python3 sup_confidence.py 0.6 0.65 0.7   -> scaled values and labels."""
import json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
A_PLATT = math.sqrt(3)
RANK = {'low': 0, 'medium': 1, 'high': 2}


def platt(p, a=A_PLATT):
    p = min(max(float(p), 1e-4), 1 - 1e-4)
    return 1 / (1 + math.exp(-a * math.log(p / (1 - p))))


def thresholds():
    cfg = json.load(open(os.path.join(HERE, 'data', 'review_settings.json')))
    return cfg.get('confidence_thresholds') or {'high': 0.70, 'medium': 0.60}


def label(sizing, th=None):
    """Label from sizing.confidence (Platt-scaled) when present, else the supervisor's own label."""
    sizing = sizing or {}
    if sizing.get('confidence') is None: return sizing.get('conviction')
    th = th or thresholds(); p = platt(sizing['confidence'])
    return 'high' if p >= th['high'] else 'medium' if p >= th['medium'] else 'low'


def annotate(sizing, th=None):
    """Adds confidence_scaled and the derived conviction label to a sizing dict (in memory only)."""
    if not sizing or sizing.get('confidence') is None: return sizing
    th = th or thresholds()
    return dict(sizing, confidence_scaled=round(platt(sizing['confidence']), 4), conviction=label(sizing, th),
                conviction_source='Platt-scaled confidence')


if __name__ == '__main__':
    th = thresholds()
    for x in sys.argv[1:]:
        print(x, round(platt(float(x)), 4), label({'confidence': float(x)}, th))
