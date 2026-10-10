"""Carte de démonstration : aucune trace des modifications dans le HTML public, fichiers privés hors de git."""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = ["synth", "simul", "scénario", "scenario", "truth", "vérité", "verite", "modifié", "modifie", "modified",
             "fictif", "fictive", "fake", "manipul", "in_window", "shown_lat", "demo_boat"]


@pytest.mark.parametrize("page", ["reports/map.html", "reports/map_animation.html"])
def test_html_public_sans_trace(page):
    f = ROOT / page
    if not f.exists():
        pytest.skip(f"{page} absent")
    text = f.read_text(encoding="utf-8").lower()
    found = {w: len(re.findall(re.escape(w), text)) for w in FORBIDDEN if w in text}
    assert found == {}


def test_html_public_sans_mmsi_source():
    ids = ROOT / "data" / "private" / "vessel_id_map.csv"
    page = ROOT / "reports" / "map.html"
    if not ids.exists() or not page.exists():
        pytest.skip("table privée ou carte absente")
    text = page.read_text(encoding="utf-8")
    rows = [line.split(",") for line in ids.read_text().splitlines()[1:]]
    sources = [r[1] for r in rows if r[2].strip().lower() == "true"]
    assert len(sources) == 3 and not [m for m in sources if m in text]


def test_dossiers_prives_ignores_par_git():
    lines = (ROOT / ".gitignore").read_text().splitlines()
    assert "data/private/" in lines and "reports/internal/" in lines
