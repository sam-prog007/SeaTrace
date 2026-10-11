# SeaTrace

**Vérifier l'intégrité des trajets de bateaux et estimer, avec une incertitude explicite, où un bateau a pu
passer quand ses positions manquent ou ont été altérées.**

Prototype du hackathon Secure Horizons 2026 (ESSEC), Open Track « AI × Cybersecurity, côté défense ».

> ⚠️ **Aide à l'investigation, pas une preuve.** SeaTrace ne prouve aucune fraude et ne décide jamais d'une
> sanction ni d'un refus d'indemnisation. Les modèles ne sont calibrés que sur des données synthétiques.

---

## Le problème

Un assureur, un loueur ou une autorité doit parfois savoir où un bateau est réellement passé. Or les positions
transmises (AIS, traceur) peuvent **manquer** (couverture, batterie, transpondeur coupé) ou être **altérées**
(positions décalées, rejouées, identité clonée). La question :

> « Pendant ce silence, ce bateau a-t-il pu entrer dans une zone interdite ? Avec quelle probabilité, et sur
> quelles hypothèses ? »

## Ce que fait SeaTrace

1. **Contrôle l'intégrité des positions** : sauts impossibles, vitesse et cap incohérents, rejeu, identité vue en
   deux endroits, coupures de signal. Chaque alerte est expliquée, avec les autres causes possibles.
2. **Reconstruit les trajets possibles** pendant une coupure, avec **cinq modèles** qui tiennent compte des
   côtes, du courant et du vent, puis estime la **probabilité de passage dans une zone réglementée**.
3. **Donne un score d'anomalie de 0 à 100** à chaque trajet et à chaque navire de la flotte, pour savoir quoi
   regarder en premier. Le même score sert dans l'analyse d'un trajet et dans la vue de la flotte.

Le tout dans un tableau de bord web en anglais, pensé pour des utilisateurs non techniques.

---

## Démarrage rapide

Python 3.10 ou plus. Les commandes ci-dessous sont pour Windows (PowerShell) ; sur macOS / Linux, remplacer
`.venv\Scripts\activate` par `source .venv/bin/activate` et `python` par `python3` pour créer l'environnement.

```powershell
git clone https://github.com/sam-prog007/Seatrace-Hackathons.git
cd Seatrace-Hackathons
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-data.txt
```

**Préparer les données locales** (une fois, environ une minute ; ces fichiers ne sont pas sur GitHub). L'ordre
compte : le premier export télécharge la carte des côtes dont les deux scripts suivants ont besoin.

```powershell
python src/13_seatrace_export.py    # côtes, zones et bateaux test au format SeaTrace
python src/10_meteo.py              # vent et vagues réels (Open-Meteo)
python src/12_simulate_context.py   # courant de démonstration (simulé, étiqueté comme tel)
python src/13_seatrace_export.py    # même export, avec le courant et le vent
```

**Lancer le tableau de bord :**

```powershell
python serveur.py                   # puis ouvrir http://127.0.0.1:8050
```

En cas d'affichage d'une ancienne version : arrêter tous les serveurs (`Get-Process python | Stop-Process -Force`
sous Windows), relancer, puis recharger la page avec **Ctrl+Shift+R**. `python serveur.py --port 8090` lance le
serveur sur un autre port.

---

## Utiliser le tableau de bord

| Onglet | À quoi il sert |
|---|---|
| **Investigate** | Choisir un jeu de données et un bateau (★ pour le garder en favori), voir ses alertes et son score, choisir une coupure et une zone, puis **Reconstruct** : les cinq modèles comparés, les familles de trajets, tous les chemins, la décision et les exports (HTML, JSON, CSV). |
| **Fleet** | Tous les navires de la flotte du Kattegat, notés avec le même score et triés du plus suspect au moins suspect ; part de la flotte signalée. Un clic ouvre le navire dans Investigate. |
| **Kattegat map** | La carte d'étude de l'équipe : vrais trajets AIS des navires de pêche, zones réglementées, courant, vent et vagues, résultats de SeaTrace navire par navire. |
| **Method** | Évaluation des modèles, sources de données, journal des actions. |

### Le score d'anomalie (0-100)

La case va du **vert (0)** au **rouge (100)**. Le score combine trois signaux (`seatrace/score.py`) :

| Signal | Poids maximal | Détail |
|---|---|---|
| Alertes d'intégrité | 100 | Saut de position 30, incohérence vitesse/cap 20, dérive 20, rejeu 40, identité dédoublée 40 |
| Silence pendant un déplacement | 40 (à 6 h) | Plus longue coupure pendant laquelle le bateau a bougé de plus d'1 km ; un bateau resté au port ou au mouillage ne compte pas |
| Passage probable en zone | 80 | Probabilité estimée par les modèles, connue après **Reconstruct** (ou par le calcul en lot de la flotte) |

`score = 100 × (1 − (1 − intégrité) × (1 − silence) × (1 − zone))`. Un navire est **signalé** à partir de 50.
Le score dit quoi regarder en premier ; il ne mesure la culpabilité de personne.

### Les jeux de données

| Jeu | Contenu |
|---|---|
| **Demo (fictitious bay)** | Sept trajets synthétiques dans une baie inventée (sortie normale, détour, perte de réception, falsification, rejeu…). |
| **Kattegat test boats** | Quatre bateaux test à MMSI fictif (999…), chacun avec un seul événement simulé, à découvrir. |
| **Kattegat fishing fleet** | Les 208 navires de pêche réels de la carte (AIS danois, 15-17 septembre 2026). |
| **Kattegat map export** | Le même export que la carte, au format SeaTrace (créé par `src/13_seatrace_export.py`). |

**Bateaux test** (la réponse n'est lue que par l'évaluation, jamais par le détecteur) :

| Bateau | Événement simulé | Ce que montre SeaTrace |
|---|---|---|
| **A** | Transpondeur coupé 45 min pendant la traversée d'une zone fermée | Silence trouvé à 5 s près ; passage en zone ≈ 100 % |
| **B** | Positions décalées de 10 km pendant 1 h | Positions écartées puis trajet reconstruit (0,03-0,5 km d'erreur) ; score 100 |
| **C** | Perte de signal innocente, loin de toute zone | Trou trouvé, aucune zone à portée |
| **D** | Coupure de 5 h 30 juste après la sortie du Limfjord, près de la zone DK-MSFD-A ; le vrai trajet y chalute | Les modèles divergent (0 % à 77 %) : un cas réellement incertain |

A, B et C sont des copies de vrais trajets de chalutiers (`src/synthetic.py`) ; D est entièrement simulé
(`src/boat_d.py`).

---

## Les modèles

Les cinq modèles tournent sur la même coupure et sont **comparés, jamais moyennés** dans l'analyse : l'outil
montre leurs écarts et dit quand ils divergent. Détails : [`docs/MODELES.md`](docs/MODELES.md).

| Modèle | Idée | Brier* |
|---|---|---|
| HMM sur grille à cap persistant | Grille autour de la coupure, le bateau garde plutôt son cap ; probabilité exacte de passage | 0,070 |
| Appris sur des historiques | Même grille, vitesses et virages appris sur de vrais trajets | 0,087 |
| Filtre particulaire + lissage | Milliers de trajets simulés, gardés selon leur cohérence | 0,077 |
| Pont brownien | Marche aléatoire entre les deux positions connues | 0,108 |
| Boosting stochastique | Prédit directement la probabilité à partir de descripteurs (réglé avec Optuna) | 0,050 |

\* Score de Brier sur 159 coupures de bateaux synthétiques jamais vus (plus bas = meilleur ; une interpolation
en ligne droite fait 0,126). Ces chiffres viennent de données synthétiques et ne valent pas validation sur
données réelles.

Les chemins de tous les modèles sont regroupés en **familles** (« par le nord, en traversant la zone »…) par ACP,
et tous les chemins générés sont conservés avec leur probabilité.

---

## Données

- **Démonstration** : trajets synthétiques et géographie fictive (`data/demo/`).
- **AIS réel du Kattegat** : fichiers journaliers publics de l'Autorité maritime danoise, filtrés sur les navires
  de pêche ; zones du règlement délégué (UE) 2022/952 ; côtes Natural Earth ; vent et vagues Open-Meteo ; courant
  Copernicus Marine (ou simulé à défaut, et étiqueté comme tel). Pipeline complet (`src/01` à `src/13`) :
  [`docs/PIPELINE_KATTEGAT.md`](docs/PIPELINE_KATTEGAT.md).
- **Autres sources ouvertes importables** (AIS NOAA, GeoJSON de zones, observations satellite…) :
  [`docs/DONNEES_OUVERTES.md`](docs/DONNEES_OUVERTES.md).

Les données volumineuses (`data/raw`, `data/interim`, `data/processed`, `data/ais`…) sont régénérables et ne sont
pas versionnées.

---

## Structure du dépôt

```
serveur.py            lance le tableau de bord (Flask), http://127.0.0.1:8050
web/                  API JSON, page HTML, JavaScript et styles (Leaflet et Chart.js embarqués)
seatrace/             cœur : import sécurisé, détection, cinq modèles, score, segmentation, rapports, journal
src/                  pipeline des données du Kattegat (01 à 13) et bateaux test (synthetic.py, boat_d.py)
eval/                 évaluation des modèles et des bateaux test
scripts/              outils en ligne de commande (import AIS, comparatif, réglage du boosting)
config/               paramètres versionnés (config/parametres.json)
data/                 démo, bateaux test, zones et côtes de référence, modèles appris
reports/              carte du Kattegat et rapports de qualité des données
tests/                tests unitaires, de sécurité et de l'API web
docs/                 documentation détaillée (voir ci-dessous)
app.py                ancienne interface Streamlit (V1) : streamlit run app.py
```

## Tests et évaluation

```powershell
python -m pytest                      # ~110 tests : modèles, sécurité des imports, API web, score
python eval/comparer_modeles.py       # comparatif des 5 modèles (~6 min ; --rapide : ~3 min)
python eval/evaluer_kattegat.py       # bateaux test passés dans l'algorithme
python src/13_reconstruct.py          # recalcule le criblage de toute la flotte (~5 min)
```

---

## Limites et suite

- Modèles calibrés uniquement sur des données synthétiques ; à recalibrer sur une saison de données réelles.
- Coupures de plus de 6 h non reconstruites (pas d'extrapolation).
- Le criblage de la flotte est un calcul en lot ; un service en continu reste à construire.
- Avant un pilote : RGPD, authentification et isolation entre clients, source officielle des zones.

## Documentation

| Document | Contenu |
|---|---|
| [`docs/MODELES.md`](docs/MODELES.md) | Les cinq modèles, leur comparaison, la segmentation en familles |
| [`docs/CARTE_ALGORITHME.md`](docs/CARTE_ALGORITHME.md) | Faire tourner l'algorithme sur la carte du Kattegat |
| [`docs/PIPELINE_KATTEGAT.md`](docs/PIPELINE_KATTEGAT.md) | Pipeline des données réelles, étape par étape |
| [`docs/DONNEES_OUVERTES.md`](docs/DONNEES_OUVERTES.md) · [`docs/DONNEES.md`](docs/DONNEES.md) | Sources de données et format |
| [`docs/PARAMETRES.md`](docs/PARAMETRES.md) | Paramètres et hypothèses |
| [`docs/SECURITE.md`](docs/SECURITE.md) | Protections testées |
| [`docs/DEFENSE.md`](docs/DEFENSE.md) | Fiche de défense du projet |
| [`docs/ETUDE_MARCHE.md`](docs/ETUDE_MARCHE.md) · [`docs/PILOTES.md`](docs/PILOTES.md) | Marché, concurrence, pilotes |
| [`docs/PITCH.md`](docs/PITCH.md) · [`docs/VIDEO.md`](docs/VIDEO.md) | Pitch et vidéo |
| [`docs/EQUIPE.md`](docs/EQUIPE.md) | Équipe, rôles, planning |
