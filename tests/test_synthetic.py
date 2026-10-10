"""Bateaux synthétiques : isolement des fichiers de vérité et propriétés des scénarios A, B, C."""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SYN = ROOT / "data" / "synthetic"
# Seuls ces fichiers ont le droit de mentionner les fichiers de vérité.
ALLOWED = {ROOT / "src" / "synthetic.py", pathlib.Path(__file__).resolve()}
TRUTH_ONLY = {"reported_present", "is_manipulated", "manipulation", "event_start", "event_end",
              "source_track_id", "source_mmsi", "reported_lat", "reported_lon"}


def test_aucun_code_hors_evaluation_ne_lit_la_verite():
    """Le détecteur ne doit jamais lire *_truth* ; seule l'évaluation (eval/) y a droit."""
    offenders = []
    for folder in ("src", "seatrace", "scripts", "web"):
        for f in (ROOT / folder).rglob("*.py"):
            if f.resolve() not in ALLOWED and "_truth" in f.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(f.relative_to(ROOT)))
    assert offenders == []


def _syn():
    pl = pytest.importorskip("polars")
    if not (SYN / "boat_A_reported.parquet").exists():
        pytest.skip("data/synthetic absent : lancer src/synthetic.py")
    spec = importlib.util.spec_from_file_location("synthetic", ROOT / "src" / "synthetic.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return pl, mod


def test_verite_reservee_a_l_evaluation():
    _, syn = _syn()
    with pytest.raises(PermissionError):
        syn.load_truth("A")


@pytest.mark.parametrize("x,mmsi", [("A", "999000001"), ("B", "999000002"), ("C", "999000003")])
def test_fichiers_reported_sans_fuite(x, mmsi):
    pl, syn = _syn()
    rep = syn.load_reported(x)
    assert not (TRUTH_ONLY & set(rep.columns)), "le fichier vu par le détecteur contient des infos de vérité"
    assert rep["mmsi"].unique().to_list() == [mmsi]
    assert rep["is_synthetic"].all() and rep["scenario"].unique().to_list() == [x]


def test_scenarios():
    pl, syn = _syn()
    a_rep, a_tru = syn.load_reported("A"), syn.load_truth("A", for_evaluation=True)
    gaps = a_rep["timestamp"].diff().dt.total_minutes().max()
    assert gaps >= 44 and a_tru.height > a_rep.height
    b_rep, b_tru = syn.load_reported("B"), syn.load_truth("B", for_evaluation=True)
    assert b_rep.height == b_tru.height and b_tru["is_manipulated"].sum() > 0
    same = b_tru.filter(~pl.col("is_manipulated"))
    assert (same["lat"] == same["reported_lat"]).all() and (same["lon"] == same["reported_lon"]).all()
    c_rep, c_tru = syn.load_reported("C"), syn.load_truth("C", for_evaluation=True)
    assert c_rep["timestamp"].diff().dt.total_minutes().max() >= 44 and c_tru.height > c_rep.height
