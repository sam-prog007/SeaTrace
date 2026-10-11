"""Anomaly score shared by Investigate and Fleet (seatrace/score.py)."""
from seatrace import score as SC


def test_score_bornes_et_monotonie():
    assert SC.score(0, 0, None)["score"] == 0
    assert SC.score(100, 0, None)["score"] == 100
    assert SC.score(0, 6 * 60, None)["score"] == 40          # long silence while moving: 40 at most
    assert SC.score(0, 12 * 60, 1.0)["score"] == 88          # silence 40 + certain passage 80, combined
    a, b = SC.score(0, 330, 0.2)["score"], SC.score(0, 330, 0.6)["score"]
    assert 0 < a < b <= 100
    assert not SC.score(0, 60, None)["zone_known"] and SC.score(0, 60, 0.0)["zone_known"]


def test_score_integrite_depuis_types():
    assert SC.integrite_depuis_types(["saut_position"]) == 30
    assert SC.integrite_depuis_types(["rejeu", "identite_dedoublee", "saut_position"]) == 100
    assert SC.integrite_depuis_types(["interruption"]) == 0
