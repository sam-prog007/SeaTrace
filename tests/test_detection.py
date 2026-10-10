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


def test_rejeu_ignore_les_tronçons_a_vitesse_constante_et_intervalles_irreguliers():
    """On real AIS the interval varies (30 s, 40 s, ...): at constant speed the same pattern of intervals gives the
    same displacements. That is not a replay."""
    import pandas as pd
    rng = np.random.default_rng(0)
    pas = rng.choice([30, 40], size=400)
    t = np.cumsum(pas)
    vx, vy = 2.0, 1.0  # m/s, constant
    x, y = t * vx, t * vy
    lat, lon = 56.0 + y / 111000.0, 11.0 + x / (111320.0 * np.cos(np.radians(56.0)))
    df = pd.DataFrame({"boat_id": "B", "trip_id": "T", "timestamp": pd.to_datetime(t, unit="s", utc=True),
                       "lat": lat, "lon": lon, "sog_kn": 4.5, "cog_deg": 63.0, "accuracy_m": 10.0,
                       "source": "ais", "synthetic": False})
    a = detect.analyser_trajet(df, P["types_bateau"]["ais_peche"], P)
    assert not [x for x in a.alertes if x.type == "rejeu"]


def test_parametres_adaptes_a_l_intervalle_reel():
    import pandas as pd
    from seatrace import pipeline
    t = pd.to_datetime(np.arange(0, 3000, 30), unit="s", utc=True)
    df = pd.DataFrame({"timestamp": t})
    p = pipeline.params_pour_trajet(P, df)
    assert p["detection"]["intervalle_attendu_s"] == 30 and p["detection"]["facteur_interruption"] * 30 >= 600
    t60 = pd.DataFrame({"timestamp": pd.to_datetime(np.arange(0, 3000, 60), unit="s", utc=True)})
    assert pipeline.params_pour_trajet(P, t60) is P  # nominal interval: untouched
