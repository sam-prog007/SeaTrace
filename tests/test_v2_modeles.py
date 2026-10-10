"""Tests des modèles de la V2 : particules, pont brownien, modèle appris, boosting, segmentation, comparatif."""

import math

import numpy as np
import pandas as pd
import pytest

from seatrace import appris as AP, archive, comparatif as CP, config, detect, geo, pipeline
from seatrace import modeles as MD, particules as PF, pont_brownien as BB, reconstruct as R, segmentation as SG

P = config.charger()


@pytest.fixture(scope="module")
def demo():
    return pipeline.charger_demo(P)


def preparer(jeu, trip):
    df = jeu.positions[jeu.positions["trip_id"] == trip]
    pt = P["types_bateau"][jeu.bateaux[df["boat_id"].iloc[0]]["type_bateau"]]
    a = detect.analyser_trajet(df, pt, P)
    g = pipeline.geographie_pour(a.points, P)
    gr = pipeline.grille_pour(g, a.points, P)
    per = max(a.periodes, key=lambda p: p.duree_min)
    deb, fin = pipeline.observations_bornes(a, per, P)
    pts = a.points[a.points["statut"] == "observee"]
    fiables = pd.DataFrame({"x": pts["x"], "y": pts["y"], "t_s": pts["t_s"], "sigma_km": 0.05})
    return gr, deb, fin, pt, per, fiables


def test_particules_coherentes_avec_le_hmm(demo):
    gr, deb, fin, pt, _, _ = preparer(demo, "S5-trou-zone")
    r = PF.reconstruire_particules(gr, deb, fin, pt, P, "Z1", graine=1, n_particules=1500, n_lisses=100)
    assert r.statut == "ok" and r.proba_passage > 0.8
    assert len(r.chemins) == 100
    assert sum(c.poids for c in r.chemins) == pytest.approx(1.0)
    # extrémités proches des observations, chemins en mer
    xs = np.array([c.xs for c in r.chemins])
    ys = np.array([c.ys for c in r.chemins])
    assert np.median(np.hypot(xs[:, -1] - fin.x, ys[:, -1] - fin.y)) < 0.3
    assert gr.en_mer(xs[:, 1:-1], ys[:, 1:-1]).mean() > 0.99
    gr3, deb3, fin3, pt3, _, _ = preparer(demo, "S3-interruption")
    r3 = PF.reconstruire_particules(gr3, deb3, fin3, pt3, P, "Z1", graine=1, n_particules=1000, n_lisses=60)
    assert r3.proba_passage < 0.1


def test_particules_trajet_impossible_signale(demo):
    gr, deb, fin, pt, _, _ = preparer(demo, "S5-trou-zone")
    loin = R.Observation(fin.x, fin.y, fin.sigma_km, 60.0)  # même distance en 1 minute
    r = PF.reconstruire_particules(gr, deb, loin, pt, P, "Z1")
    assert r.statut == "echec" and r.proba_passage is None


def test_estimation_sigma_m_retrouve_la_mobilite():
    s2_vrai = 2.0  # km²/h
    t = np.arange(0, 6 * 3600, 60.0)
    estimations = []
    for graine in range(10):
        rng = np.random.default_rng(graine)
        pas = rng.normal(0, math.sqrt(s2_vrai * 60 / 3600), (2, len(t)))
        x, y = np.cumsum(pas[0]), np.cumsum(pas[1])
        s2, info = BB.estimer_sigma_m(x, y, t, 0.01, 1800)
        assert info["triplets"] >= 5
        estimations.append(s2)
    assert 0.75 * s2_vrai < np.mean(estimations) < 1.25 * s2_vrai


def test_pont_respecte_les_terres_ou_echoue(demo):
    gr, deb, fin, pt, _, fiables = preparer(demo, "S5-trou-zone")
    s2, info = BB.estimer_sigma_m(fiables["x"], fiables["y"], fiables["t_s"], 0.05, fin.t_s - deb.t_s)
    r = BB.reconstruire_pont(gr, deb, fin, pt, P, "Z1", s2, info, graine=0)
    if r.statut == "ok":
        xs = np.array([c.xs for c in r.chemins])
        ys = np.array([c.ys for c in r.chemins])
        assert gr.en_mer(xs[:, 1:-1], ys[:, 1:-1]).all()
        assert "acceptance_rate" in r.diagnostics
    else:
        assert "unsuitable" in r.raisons[0]


def test_modele_appris_sauvegarde_sans_pickle(tmp_path, demo):
    m = AP.apprendre(demo.positions, source="test")
    assert np.allclose(m.matrice.sum(axis=1), 1.0)
    f = tmp_path / "m.npz"
    m.sauver(f)
    m2 = AP.ModeleAppris.charger(f)
    assert m2.nb_caps == m.nb_caps and np.allclose(m2.matrice, m.matrice)
    with np.load(f, allow_pickle=False) as z:  # lisible sans pickle
        assert "rapports_vitesse" in z.files


def test_modele_appris_dans_le_hmm(demo):
    gr, deb, fin, pt, _, _ = preparer(demo, "S5-trou-zone")
    m = AP.apprendre(demo.positions, source="test")
    r = R.reconstruire(gr, deb, fin, pt, P["modele"], "Z1", appris=m, n_trajets=30)
    assert r.proba_passage is not None and 0 <= r.proba_passage <= 1
    assert r.hypotheses["learned_parameters"]["source"] == "test"
    assert len(r.log_probas) == 30 and all(lp <= 0 for lp in r.log_probas)


def _chemin(m, i, dy, n=20):
    xs = np.linspace(0, 10, n + 1)
    ys = dy * np.sin(np.linspace(0, math.pi, n + 1))
    return MD.Chemin(m, f"{m}-{i}", xs, ys, 1.0)


def test_segmentation_separe_deux_familles():
    deb = R.Observation(0, 0, 0.05, 0)
    fin = R.Observation(10, 0, 0.05, 3600)
    rng = np.random.default_rng(1)
    a = [_chemin("a", i, 3 + rng.normal(0, 0.2)) for i in range(70)] + \
        [_chemin("a", 100 + i, -3 + rng.normal(0, 0.2)) for i in range(30)]
    b = [_chemin("b", i, 3 + rng.normal(0, 0.2)) for i in range(20)] + \
        [_chemin("b", 100 + i, -3 + rng.normal(0, 0.2)) for i in range(80)]
    for cs in (a, b):
        for c in cs:
            c.poids = 1 / len(cs)
    seg = SG.segmenter({"a": a, "b": b}, deb, fin, P)
    assert seg["n_families"] == 2
    nord = next(f for f in seg["families"] if f["mean_lateral_offset_km"] > 0)
    assert nord["probability_by_model"]["a"] == pytest.approx(0.7, abs=0.02)
    assert nord["probability_by_model"]["b"] == pytest.approx(0.2, abs=0.02)
    assert "north" in nord["description"]
    assert all(c.famille in ("F1", "F2") for c in a + b)


def test_segmentation_une_seule_famille_si_pas_de_structure():
    deb = R.Observation(0, 0, 0.05, 0)
    fin = R.Observation(10, 0, 0.05, 3600)
    rng = np.random.default_rng(2)
    cs = [_chemin("a", i, rng.normal(0, 1.0)) for i in range(200)]
    for c in cs:
        c.poids = 1 / 200
    seg = SG.segmenter({"a": cs}, deb, fin, P)
    assert seg["n_families"] == 1


def test_comparatif_et_archive(tmp_path, demo):
    gr, deb, fin, pt, per, fiables = preparer(demo, "S5-trou-zone")
    comp = CP.reconstruire_tous(gr, deb, fin, pt, P, "Z1", [], per, fiables, sensibilite=False)
    for m in ("hmm", "appris", "particules", "pont", "boosting"):
        assert m in comp.resultats
    ok = {m: r for m, r in comp.resultats.items() if r.statut == "ok"}
    assert {"hmm", "particules"} <= set(ok)
    for m, r in ok.items():
        assert 0 <= r.proba_passage <= 1
        if r.chemins:
            assert sum(c.poids for c in r.chemins) == pytest.approx(1.0)
            assert all(c.famille for c in r.chemins)
    assert comp.synthese["lecture"]
    run = archive.comparaison_vers_dict(comp, gr, per.debut, "Z1", {"test": True})
    run["run_id"] = archive.nouveau_run_id("x")
    d = archive.sauver(run, tmp_path)
    df = pd.read_csv(d / "chemins.csv")
    n_attendu = sum(len(r.chemins) * (comp.n_pas + 1) for r in comp.resultats.values())
    assert len(df) == n_attendu
    assert set(df["model"]) == {m for m, r in comp.resultats.items() if r.chemins}
    # poids : somme = 1 par modèle (une ligne par point, on prend l'étape 0)
    s = df[df["step"] == 0].groupby("model")["weight"].sum()
    assert np.allclose(s.to_numpy(), 1.0, atol=1e-3)
    assert archive.charger(run["run_id"], tmp_path) is not None
    assert archive.charger("../../etc", tmp_path) is None


def test_lecture_commune_ne_moyenne_pas():
    code, texte = CP.lecture_commune({"a": 0.1, "b": 0.9}, 0.3)
    assert code == "divergence" and "cannot conclude" in texte
    assert CP.lecture_commune({"a": 0.95, "b": 0.97}, 0.3)[0] == "convergence_haute"
    assert CP.lecture_commune({"a": 0.02, "b": 0.05}, 0.3)[0] == "convergence_basse"


def test_parametres_v2_bornes():
    p = config.copie(P)
    p["modeles"]["particules"]["n_particules"] = 10 ** 7
    with pytest.raises(config.ErreurParametres):
        config.valider(p)
    p = config.copie(P)
    p["modeles"]["courant_kmh"] = [100, 0]
    with pytest.raises(config.ErreurParametres):
        config.valider(p)


def test_acp_interpretation_en_anglais_et_coherente():
    deb = R.Observation(0, 0, 0.05, 0)
    fin = R.Observation(10, 0, 0.05, 3600)
    rng = np.random.default_rng(3)
    cs = [_chemin("a", i, 3 + rng.normal(0, 0.2)) for i in range(60)] + \
         [_chemin("a", 100 + i, -3 + rng.normal(0, 0.2)) for i in range(40)]
    for c in cs:
        c.poids = 1 / len(cs)
    seg = SG.segment({"a": cs}, deb, fin, P)
    comps = seg["interpretation"]["components"]
    assert comps and comps[0]["main_descriptor"] == "lateral_side"
    assert comps[0]["correlation"] > 0.9
    assert "side of the straight line" in comps[0]["meaning"]
    assert comps[0]["variance_pct"] > 80
    assert any("families were kept" in x for x in seg["interpretation"]["reading"])
    # oriented so that the main meaning reads positive: the 'left' family has positive PC1 on average
    left = [pt["pc1"] for pt in seg["pca_cloud"] if pt["family"] == next(f["id"] for f in seg["families"] if f["mean_lateral_offset_km"] > 0)]
    assert np.mean(left) > 0


def test_boosting_regle_par_optuna(tmp_path):
    import json
    from seatrace import boosting as BO
    rng = np.random.default_rng(0)
    n = 90
    df = pd.DataFrame({c: rng.normal(size=n) for c in BO.DESCRIPTEURS})
    df["p_hmm"] = rng.uniform(0, 1, n)
    df["verite_passage"] = (df["p_hmm"] + rng.normal(0, 0.2, n)) > 0.6
    df["trip_id"] = [f"F{(i % 9) + 1:03d}-T{i // 9}" for i in range(n)]
    r = BO.regler(df, n_essais=4, graine=0)
    assert r["n_trials"] == 4 and r["n_boats"] == 9
    assert len(r["trials"]) == 4 and r["trials"][-1]["best_so_far"] == r["best_cv_brier"]
    assert r["best_cv_brier"] <= r["reference_cv_brier"] + 0.05
    json.dumps(r)  # serialisable: written to data/modeles/boosting_params.json
    ens = BO.EnsembleBoosting(df, r["best_params"], n_modeles=2)
    assert ens.hp == BO._nettoyer_hp(r["best_params"])
    # grouped CV never puts a boat in both the training and validation parts of a fold
    from sklearn.model_selection import GroupKFold
    g = BO._bateaux(df)
    for tr, va in GroupKFold(n_splits=3).split(df, groups=g):
        assert not (set(g[tr]) & set(g[va]))
