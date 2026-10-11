# Organisation de l'équipe (4 personnes, 24 h)

## Interfaces entre modules (à ne pas casser sans prévenir)

| Interface | Format | Produit par | Consommé par |
|---|---|---|---|
| Positions | CSV : `boat_id, trip_id, timestamp (ISO 8601 UTC), lat, lon, sog_kn, cog_deg, accuracy_m, source, synthetic` | P1 (`scenarios.py`, `scripts/generer_donnees.py`) ou import utilisateur | P2, P3 |
| Métadonnées bateaux | `data/demo/bateaux_demo.json` : `boat_id → type_bateau, nom, flotte` | P1 | P2, P3 |
| Trajets déclarés | `data/demo/routes_declarees.json` : `trip_id → route_declaree_xy, description` | P1 | P3 |
| Observations RF simulées | `data/demo/observations_rf_simulees.csv` : `obs_id, boat_id_candidat, trip_id, timestamp, lat, lon, precision_m, proba_attribution, source, mention` | P1 | P2 (via `pipeline.observations_rf`) |
| Étiquettes (évaluation seulement) | `data/etiquettes/*.csv` | P1 | **P2 évaluation uniquement** — jamais l'application |
| Analyse | `detect.Analyse` : `points` (+ `x, y, t_s, v_calc_kn, statut`), `alertes`, `periodes`, `qualite`, `score_anomalie` | P2 | P3, P4 |
| Reconstruction | `reconstruct.Resultat` : `proba_passage, statut, lecture, raisons, occupation, trajets, hypotheses, sensibilite, controle_mc` | P2 | P3, P4 |
| Paramètres | `config/parametres.json` (validés par `config.valider`) | P2 | tous |
| Rapport | `report.construire(contexte)` → JSON + HTML | P3 + P4 | analyste |

## Fichiers et responsables

| Personne | Rôle | Fichiers |
|---|---|---|
| P1 — Données et scénarios | format partagé, générateur, zones, manipulations, jeux séparés | `seatrace/scenarios.py`, `seatrace/geo.py`, `scripts/generer_donnees.py`, `data/` |
| P2 — Détection et modèle | règles, modèle à cap persistant, passage, évaluation, sensibilité | `seatrace/detect.py`, `seatrace/reconstruct.py`, `eval/run_eval.py`, `config/parametres.json` |
| P3 — Application et sécurité | import sécurisé, carte, chronologie, intégration, journal, export | `app.py`, `seatrace/schema.py`, `seatrace/audit.py`, `seatrace/pipeline.py`, `.streamlit/config.toml` |
| P4 — Validation et produit | parcours complet, rapport, concurrence, pilotes, limites, pitch, vidéo | `seatrace/report.py`, `docs/*.md`, `tests/` (relecture) |

## Planning (heure de Paris)

| Créneau | Objectif commun |
|---|---|
| Sam. 15h–18h | Version réduite complète : un bateau, une zone, une interruption, un rapport (**fait**) |
| Sam. 18h30 | Checkpoint officiel : problème / solution / stack |
| Sam. 19h–23h | Chacun consolide son module ; P4 déroule le parcours complet toutes les 2 h et note les défauts |
| Sam. 23h | Intégration : `pytest`, `python eval/run_eval.py`, démo de bout en bout |
| Dim. 0h–7h | Repos par rotation ; seulement des corrections, pas de nouvelle fonctionnalité risquée |
| **Dim. 8h–12h** | **Stabilisation** (gel des fonctionnalités à 8h) : tests, vidéo (9h), coaching (10h), dépôt à 11h30 |

Points de synchronisation : 10 minutes toutes les 2 heures. Quelqu'un bloqué plus de 30 minutes le dit.

## Règles

- Un seul dépôt Git ; `pytest` doit passer avant chaque fusion.
- Aucune clé, aucun secret dans le dépôt (le jury vérifie).
- Ne jamais lire `data/etiquettes/` depuis l'application ou la détection.
- Toute nouvelle affirmation chiffrée dans le pitch doit avoir une source dans `docs/PITCH.md`.
