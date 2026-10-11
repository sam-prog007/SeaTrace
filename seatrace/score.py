"""Single anomaly score (0-100) of a trip or a vessel, the same in the Investigate view and in the Fleet screening.

Three signals, each turned into a share between 0 and 1, then combined so that the score rises with each of them
and never exceeds 100 (score = 100 x (1 - (1 - integrity) x (1 - silence) x (1 - zone))):

  - integrity : weighted integrity alerts of seatrace/detect.py (position jump 30, replay 40...), capped at 100;
  - silence   : the longest period without reliable positions during which the boat moved more than 1 km (a boat
                that reappears where it vanished stayed in port or at anchor), up to 40 points at 6 h;
  - zone      : estimated probability that the path crossed a restricted area during a gap, up to 80 points.
                Known only after a reconstruction (or the batch screening); before that it counts for nothing.

The score ranks what to look at first. It measures how unusual and unexplained the data are, never anyone's guilt.
"""
from __future__ import annotations

from .detect import INTEGRITE, POIDS

SILENCE_MAX_H = 6.0
DEPLACEMENT_MIN_KM = 1.0
SILENCE_PART = 0.40
ZONE_PART = 0.80


def integrite_depuis_types(types) -> int:
    """Integrity score from the alert types of the batch screening (same weights as detect.py)."""
    return min(100, sum(POIDS.get(t, 0) for t in types if t != "interruption"))


def integrite_depuis_alertes(alertes) -> int:
    return min(100, sum(POIDS[a.type] for a in alertes if a.categorie == INTEGRITE))


def score(integrite: float, silence_max_min: float | None, p_zone: float | None) -> dict:
    i = min(1.0, max(0.0, (integrite or 0) / 100))
    s = SILENCE_PART * min(1.0, max(0.0, (silence_max_min or 0) / 60 / SILENCE_MAX_H))
    z = ZONE_PART * min(1.0, max(0.0, p_zone or 0)) if p_zone is not None else 0.0
    total = round(100 * (1 - (1 - i) * (1 - s) * (1 - z)))
    return {"score": int(total),
            "parts": {"integrity": round(100 * i), "silence": round(100 * s), "zone": round(100 * z)},
            "zone_known": p_zone is not None,
            "longest_gap_min": round(silence_max_min or 0, 1), "p_zone": p_zone}
