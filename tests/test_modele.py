"""Tests du modèle de reconstruction et de la probabilité de passage."""

from seatrace import config, geo, reconstruct as R

P = config.charger()
PT = P["types_bateau"]["semi_rigide_location"]
G = geo.geographie_demo()
GRILLE = geo.construire_grille(G, P["modele"]["taille_case_km"])


def obs(x, y, t, cap=None, v=None):
    return R.Observation(x, y, 0.05, t, cap_deg=cap, vitesse_kn=v)


def test_probabilite_bornee_et_controle_mc_coherent():
    r = R.reconstruire(GRILLE, obs(13.8, 7.5, 0), obs(8.8, 5.4, 15 * 60), PT, P["modele"], "Z1",
                       n_trajets=300, graine=1)
    assert 0.0 <= r.proba_passage <= 1.0
    mc = r.controle_mc
    assert abs(mc["proportion_passage"] - r.proba_passage) < 4 * mc["erreur_type"] + 0.02
    assert mc["coherence_drapeau_geometrie"] == 1.0


def test_passage_quasi_certain_quand_route_directe_obligatoire():
    # 9 km en 15 min : seule la route directe, à travers Z1, est compatible
    r = R.reconstruire(GRILLE, obs(14.2, 5.2, 0), obs(9.4, 5.0, 12 * 60), PT, P["modele"], "Z1",
                       echantillonner=False)
    assert r.proba_passage > 0.9


def test_passage_quasi_nul_loin_de_la_zone():
    r = R.reconstruire(GRILLE, obs(6.5, 11.5, 0), obs(9.5, 9.3, 10 * 60), PT, P["modele"], "Z1",
                       echantillonner=False)
    assert r.proba_passage < 0.01


def test_zone_non_exclue_du_modele():
    r = R.reconstruire(GRILLE, obs(11.0, 5.0, 0), obs(12.5, 5.0, 6 * 60), PT, P["modele"], "Z1",
                       echantillonner=False)
    assert r.hypotheses["zone_excluded_from_model"] is False
    assert r.proba_passage > 0.99  # départ et arrivée dans la zone


def test_trajet_impossible_signale():
    voilier = P["types_bateau"]["voilier_location"]
    r = R.reconstruire(GRILLE, obs(2.0, 2.0, 0), obs(18.0, 12.0, 10 * 60), voilier, P["modele"], "Z1",
                       echantillonner=False)
    assert r.proba_passage is None and r.statut == "insuffisamment_etayee"


def test_interruption_trop_longue():
    r = R.reconstruire(GRILLE, obs(5, 5, 0), obs(6, 6, 10 * 3600), PT, P["modele"], "Z1")
    assert r.proba_passage is None and "insufficiently supported" in r.raisons[0]


def test_trajets_tires_evitent_les_terres():
    r = R.reconstruire(GRILLE, obs(5.6, 7.5, 0), obs(13.8, 7.5, 50 * 60), PT, P["modele"], "Z1",
                       n_trajets=100, graine=2)
    for xs, ys, _ in r.trajets:
        assert not G.est_terre(xs, ys).any()


def test_observation_externe_attribution_incertaine_moins_informative():
    deb, fin = obs(5.6, 7.5, 0), obs(13.8, 7.5, 50 * 60)
    sud = R.Observation(11.0, 5.3, 0.8, 25 * 60, p_attribution=1.0, source="rf_simulee")
    sud_incertaine = R.Observation(11.0, 5.3, 0.8, 25 * 60, p_attribution=0.3, source="rf_simulee")
    p0 = R.reconstruire(GRILLE, deb, fin, PT, P["modele"], "Z1", echantillonner=False).proba_passage
    p1 = R.reconstruire(GRILLE, deb, fin, PT, P["modele"], "Z1", [sud], echantillonner=False).proba_passage
    p2 = R.reconstruire(GRILLE, deb, fin, PT, P["modele"], "Z1", [sud_incertaine], echantillonner=False).proba_passage
    assert p1 > p2 > p0 - 1e-6


def test_qualification_insuffisante_si_hypotheses_divergent():
    r = R.Resultat(0.5, "etayee", [], {}, None)
    sens = [{"variation": "a", "proba_passage": 0.1}, {"variation": "b", "proba_passage": 0.9}]
    r = R.qualifier(r, sens, "normale", calibre=True, duree_min=30)
    assert r.statut == "insuffisamment_etayee"
