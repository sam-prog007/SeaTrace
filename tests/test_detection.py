"""Tests des contrôles de détection sur des trajets synthétiques."""

import numpy as np
import pytest

from seatrace import config, detect, scenarios

P = config.charger()


@pytest.fixture(scope="module")
def trajet():
    tr = scenarios.generer_flotte(P, n_bateaux=1, trajets_par_bateau=1, graine=5)[0]
    return tr


def analyser(df, tr):
    return detect.analyser_trajet(df, P["types_bateau"][tr["type_bateau"]], P)


def integrite(a):
    return [x for x in a.alertes if x.categorie == detect.INTEGRITE]


def test_trajet_propre_sans_alerte_integrite(trajet):
    a = analyser(trajet["obs"], trajet)
    assert integrite(a) == []
    assert a.score_anomalie == 0


@pytest.mark.parametrize("manip,attendu", [
    ("saut", "saut_position"), ("rejeu", "rejeu"), ("dedoublement", "identite_dedoublee"),
])
def test_manipulations_detectees(trajet, manip, attendu):
    rng = np.random.default_rng(0)
    obs = trajet["obs"].reset_index(drop=True)
    n = len(obs)
    if manip == "saut":
        m, _ = scenarios.manip_saut(obs, rng, n // 3, 8, 4.0)
    elif manip == "rejeu":
        m, _ = scenarios.manip_rejeu(obs, 3, 10, n // 2)
    else:
        m, _ = scenarios.manip_dedoublement(obs, rng, n // 3, 8, 4.0)
    a = analyser(m, trajet)
    assert attendu in {x.type for x in integrite(a)}
    assert a.qualite["points_suspects"] > 0
    assert a.periodes, "les positions suspectes doivent produire une période à reconstruire"


def test_interruption_est_disponibilite_pas_integrite(trajet):
    obs = trajet["obs"].reset_index(drop=True)
    m, _ = scenarios.manip_suppression(obs, len(obs) // 3, 15)
    a = analyser(m, trajet)
    assert any(x.type == "interruption" and x.categorie == detect.DISPONIBILITE for x in a.alertes)
    assert integrite(a) == []
    assert "does not prove an attack" in [x for x in a.alertes if x.type == "interruption"][0].explication


def test_explications_contiennent_valeurs_calculees(trajet):
    rng = np.random.default_rng(1)
    obs = trajet["obs"].reset_index(drop=True)
    m, _ = scenarios.manip_saut(obs, rng, len(obs) // 3, 8, 5.0)
    a = analyser(m, trajet)
    saut = [x for x in a.alertes if x.type == "saut_position"][0]
    assert "vitesse_kn" in saut.valeurs and saut.valeurs["vitesse_kn"] > saut.valeurs["seuil_kn"]


def test_seuil_depend_du_type_de_bateau(trajet):
    obs = trajet["obs"].reset_index(drop=True)
    lent = dict(P["types_bateau"]["voilier_location"])
    rapide = dict(P["types_bateau"]["semi_rigide_location"])
    a_lent = detect.analyser_trajet(obs, lent, P)
    a_rapide = detect.analyser_trajet(obs, rapide, P)
    assert len(integrite(a_lent)) >= len(integrite(a_rapide))


def test_ligne_droite_reguliere_n_est_pas_un_rejeu():
    """Un chalutier qui file droit à 2,5 nœuds, avec un rythme d'émission irrégulier qui se répète,
    produit des déplacements identiques au mètre près : ce n'est pas un rejeu (vu sur de vrais AIS)."""
    import pandas as pd
    rng = np.random.default_rng(3)
    pas = np.tile([60, 60, 60, 70], 100)[:300]
    t = np.concatenate([[0], np.cumsum(pas)])
    v = 2.5 * 1.852 / 3600  # km/s
    x, y = v * t * 0.2, -v * t * 0.98
    lat0, lon0 = 57.2, 11.4
    df = pd.DataFrame({
        "timestamp": pd.Timestamp("2026-09-15T05:00:00Z") + pd.to_timedelta(t, unit="s"),
        "lat": lat0 + (y + rng.normal(0, 0.001, len(t))) / 110.574,
        "lon": lon0 + (x + rng.normal(0, 0.001, len(t))) / (111.320 * np.cos(np.radians(lat0))),
        "sog_kn": 2.5, "cog_deg": 168.5, "accuracy_m": 10.0, "source": "ais", "synthetic": True})
    a = detect.analyser_trajet(df, P["types_bateau"]["ais_peche"], P, (lat0, lon0))
    assert "rejeu" not in {x.type for x in a.alertes}


def test_bloc_decale_long_entierement_ecarte(trajet):
    """Positions décalées de 10 km puis revenues sur la route : tout le bloc est mis à l'écart, même
    au-delà de 30 positions (bateau test B de la carte du Kattegat : 60 positions)."""
    obs = trajet["obs"].reset_index(drop=True)
    n = len(obs)
    i0, i1 = n // 4, n // 4 + min(50, n // 2)
    assert i1 - i0 > 30
    m = obs.copy()
    m.loc[i0:i1 - 1, "lat"] += 10 / 110.574
    a = analyser(m, trajet)
    assert (a.points["statut"].iloc[i0:i1] == "suspecte").all()
    assert any(p.i_avant < i0 and p.i_apres >= i1 for p in a.periodes)
