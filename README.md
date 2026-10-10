# SeaTrace

**Vérifier l'intégrité des trajets de petites embarcations et estimer, avec une incertitude explicite, où un
bateau a pu passer quand ses positions manquent ou ont été altérées.**

Prototype réalisé pendant le hackathon Secure Horizons 2026 (ESSEC, Open Track « AI × Cybersecurity, côté défense »).

> ⚠️ Aide à l'investigation, pas une preuve. SeaTrace ne prouve pas une fraude et ne décide jamais d'une
> sanction ni d'un refus d'indemnisation. **Toutes les données de démonstration sont synthétiques et la
> géographie est fictive.** Le modèle n'est calibré que sur données synthétiques.

## Le problème

Après une sortie, un assureur, un loueur ou une autorité doit savoir où une petite embarcation est réellement
passée. Les positions de traceur peuvent manquer (couverture, batterie), être altérées (modification,
injection, rejeu, suppression) ou faussées par un brouillage du signal satellite. La question centrale :

> « Pendant cette période où les positions sont manquantes ou suspectes, où ce bateau aurait-il pu passer,
> avec quelle incertitude et sur quelles hypothèses ? »

## Installation et lancement

```bash
python -m pip install -r requirements.txt
python scripts/generer_donnees.py          # données de démo (déjà présentes dans data/demo)
streamlit run app.py                        # application : http://127.0.0.1:8501
```

Autres commandes :

```bash
python -m pytest                            # 41 tests (dont sécurité des imports)
python eval/run_eval.py                     # évaluation complète (~6 min) -> eval/resultats/
python eval/run_eval.py --rapide            # évaluation réduite (~2 min)
python scripts/analyser.py --trajet S4-falsification --zone Z1    # analyse sans interface + rapport HTML
```

Fonctionne hors ligne : aucune API externe, aucun service payant, aucun fond de carte en ligne (la carte est
dessinée localement).

## Parcours utilisateur

1. Charger l'exemple ou importer un CSV de positions. 2. Choisir un bateau et un trajet. 3. Voir positions
observées, positions suspectes et anomalies sur la carte et la chronologie. 4. Choisir une période manquante
ou suspecte. 5. Voir les observations qui l'encadrent et leur fiabilité. 6. Reconstruire les trajets
compatibles. 7. Choisir la zone. 8. Lire la probabilité de passage, les hypothèses, la sensibilité et les
limites. 9. Saisir une note et une décision humaine (journalisées). 10. Exporter le rapport (HTML, JSON, CSV).

## Scénarios de démonstration (`data/demo`)

| Trajet | Ce qu'il montre | Résultat attendu |
|---|---|---|
| S1-normal | Sortie normale | Aucune alerte |
| S2-detour | Détour légitime avec mouillage | Écart au trajet prévu visible, aucune alerte |
| S3-interruption | Perte de réception sans manipulation connue | Alerte de *disponibilité* ; passage dans Z1 ≈ 0 % |
| S4-falsification | Positions falsifiées en laboratoire pour masquer un passage | Incohérences détectées, positions isolées ; reconstruction ≈ 35 % : **l'outil ne conclut pas** |
| S5-trou-zone | Segment manquant compatible avec un passage | ≈ 90 %, stable selon les hypothèses |
| S6-ambigu | Interruption longue + observation RF **simulée** | ≈ 85 % sans, ≈ 92 % avec l'observation simulée |
| S7-rejeu-dedoublement | Rejeu d'un segment, identifiant émis depuis deux lieux | Alertes « rejeu » et « identité dédoublée » |

## Architecture

```
app.py                     interface Streamlit (française), locale
seatrace/
  config.py                paramètres versionnés et bornés (config/parametres.json)
  schema.py                format partagé + import sécurisé + exports protégés
  geo.py                   géographie fictive, projection locale, grille
  scenarios.py             laboratoire : trajets synthétiques et manipulations simulées
  detect.py                contrôles explicables, isolation, périodes à reconstruire
  reconstruct.py           modèle de Markov caché à cap persistant, passage, sensibilité, références
  pipeline.py              assemblage, observations externes, contexte du rapport
  report.py                rapport HTML autonome (échappé) + JSON
  audit.py                 journal local chaîné (SHA-256)
eval/run_eval.py           banc d'évaluation (jeux séparés par bateau)
scripts/                   génération des données, analyse en ligne de commande
tests/                     tests unitaires et de sécurité
docs/                      défense, sécurité, données, paramètres, pilotes, pitch, vidéo, équipe
```

## Modèle probabiliste choisi et pourquoi

**Modèle de Markov caché sur grille, à cap persistant, lissé avant/arrière** (`seatrace/reconstruct.py`).

- *État* : case de 200 m × cap (8 directions) × drapeau « déjà passé dans la zone ». *Pas* : ~2 min.
- *Transition* : le cap est conservé avec une probabilité réglée (0,7), sinon il tourne ; la vitesse suit une
  loi large plafonnée à la vitesse maximale du type de bateau ; arrêts possibles. Les terres sont interdites ;
  **la zone étudiée n'est pas exclue**.
- *Observations* : position **avant et après** l'interruption (reconstruction après coup, pas prédiction),
  cap déclaré aux deux bornes, observations externes avec une probabilité d'attribution.
- *Pourquoi* : représente plusieurs chemins (nord/sud d'une île) sans les choisir à l'avance ; le drapeau
  donne **exactement** (sous hypothèses, à la résolution de la grille) la probabilité de passer **au moins une
  fois** dans la zone, sans additionner des probabilités de présence ; calcul en ~0,2 s ; paramètres peu
  nombreux et interprétables.
- *Pourquoi pas autre chose* : une première version sans cap (marche aléatoire) favorisait à tort le chemin le
  plus court (elle reste dans l'évaluation comme comparaison) ; un processus gaussien ou une interpolation ne
  gèrent ni les obstacles ni les chemins multiples ; un filtre particulaire serait plus souple mais plus lent
  et bruité pour une probabilité de passage.
- *Limites* : pas de météo ni de courants ; grille de 200 m ; interruptions de plus de 6 h refusées ; calibré
  uniquement sur données synthétiques.
- *Trajets dessinés* : tirés exactement dans la loi a posteriori ; leur proportion sert de **contrôle Monte
  Carlo** (erreur due au nombre de tirages, affichée séparément de la sensibilité aux hypothèses).

## Fonctionnalités réellement opérationnelles

Import CSV sécurisé ; détection (sauts, incohérence vitesse/cap, dérive cumulée, identité dédoublée, rejeu,
doublons, interruptions) ; isolation des positions suspectes ; reconstruction probabiliste ; probabilité de
passage avec statut « étayée / fragile / insuffisamment étayée », lecture prudente, sensibilité et contrôle
Monte Carlo ; observations RF simulées avec attribution incertaine ; écart au trajet prévu ; décision humaine ;
rapport HTML/JSON ; export CSV protégé ; journal chaîné vérifiable ; modification journalisée des paramètres ;
onglet d'évaluation.

Non opérationnel (feuille de route) : intégration directe de traceurs, observations RF réelles, zones
réglementaires réelles, authentification et multi-clients, détection d'anomalies communes à plusieurs bateaux.

## Métriques effectivement mesurées (`eval/resultats/metriques.json` — données synthétiques)

| | Valeur |
|---|---|
| Rappel de détection (hors suppression), bateaux jamais vus | 96 % (remplacement de segment : 85 %) |
| Précision des alertes d'intégrité | 97 % |
| Trajets propres sans aucune alerte | 85 % (0,17 fausse alerte par trajet) |
| Suppressions signalées comme interruption | 98 % (non distinguables d'une perte de réception) |
| Brier passage : modèle / sans cap / interpolation / taux de base | 0,051 / 0,081 / 0,089 / 0,163 |
| Erreur de position moyenne : modèle / interpolation | 1,2 km / 1,8 km |
| Couverture de la région à 90 % | 97 % (prudent) |
| Temps : analyse d'un trajet / reconstruction | ~17 ms / ~0,24 s (médianes) |

Calibration : correcte aux extrêmes, **sous-estimation** du passage dans les cas intermédiaires (petits
effectifs). Ces chiffres ne valent pas validation opérationnelle.

## Protections testées

Voir `docs/SECURITE.md`. Testé : fichiers vides, binaires, non UTF-8, trop gros, trop de lignes, champs trop
longs, valeurs hors bornes ou non finies, horodatages sans fuseau ou futurs, sources inconnues, injection HTML
et de formules tableur, chemins arbitraires dans le nom de fichier, écrasement d'original, journal altéré ou
tronqué, paramètres hors bornes, trajets impossibles, hypothèses divergentes, dérive progressive.

## Ce qui reste à valider avant un pilote avec des données réelles

1. Bruit, fréquence et champs réellement fournis par les traceurs des loueurs (vitesse, cap, précision).
2. Recalibrage des seuils de détection et des paramètres du modèle sur une saison de données réelles.
3. Calibration des probabilités sur des cas réels dont l'issue est connue.
4. Limites, dates d'application et exceptions des zones réglementaires réelles (source officielle, licence).
5. Base légale RGPD, information des locataires, durée de conservation, transfert sécurisé.
6. Authentification, isolation entre clients, hébergement, ancrage externe du journal, revue des dépendances.
7. Accès et droits d'usage d'éventuelles observations externes ; règles d'attribution émetteur → bateau.
8. Valeur perçue et prix : entretiens avec loueurs, assureurs et une autorité.

## Données réelles : AIS du Kattegat (`src/`)

Pipeline reproductible d'acquisition et de préparation (pas de détection). Détails et chiffres : `reports/data_quality.md`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-data.txt
.venv/bin/python src/01_download.py --start 2026-09-15 --end 2026-09-17   # ~0,65 Go par jour
.venv/bin/python src/02_filter.py   --start 2026-09-15 --end 2026-09-17   # ajouter --delete-raw pour libérer le disque
.venv/bin/python src/03_zones.py    # télécharge et lit le règlement 2022/952 (EUR-Lex)
.venv/bin/python src/04_report.py   --month 2026-09
```

Chaque script saute ce qui est déjà fait : ZIP déjà téléchargé à la bonne taille, jour déjà filtré dans `data/interim/`. La période est réglable ; on peut prendre un mois entier, environ 20 Go compressés pour septembre 2026.

**Source.** Données AIS côtières danoises gratuites, aujourd'hui gérées par la Danish Emergency Management Agency. Les fichiers sont hébergés sur `aisdata.ais.dk` (le lien depuis dma.dk n'a pas pu être vérifié : site inaccessible depuis l'environnement), servi par le bucket S3 public `https://s3.eu-central-1.amazonaws.com/aisdata.ais.dk/`. Il contient un ZIP par jour, `aisdk-YYYY-MM-DD.zip`, d'environ 0,6 à 0,8 Go, qui renferme un CSV d'environ 3,3 Go et 18 M lignes.

**Traitement.** Lecture en flux par blocs de 64 Mo (pyarrow) directement dans le ZIP, sans décompression sur disque ni chargement complet. Ensuite : filtre 55-58 N / 10-13 E, nettoyage compté (voir le rapport), dédoublonnage exact par jour, puis résolution par MMSI du type de navire, de la longueur et du nom.

**Sorties.**
- `data/processed/ais_kattegat_<YYYY-MM>.parquet/day=YYYY-MM-DD/part-0.parquet` : **navires de pêche (type « Fishing ») et messages de classe A uniquement**, triés par MMSI puis par horodatage (un trajet par navire). Format partitionné Hive : lisible avec `pl.scan_parquet(..., hive_partitioning=True)` ou `pd.read_parquet(dossier)`.
- `data/interim/` : positions nettoyées de **tous** les navires du rectangle (`day=*.parquet`, utilisées pour la carte de couverture), infos navires résolues (`vessels.parquet`) et compteurs.
- `reports/` : rapport et cartes.

### Colonnes du CSV source (en-tête réel lu dans les fichiers, 26 colonnes)

| CSV source | Parquet | Type | Contenu (README du dépôt + observation) |
|---|---|---|---|
| `# Timestamp` | `timestamp` | datetime UTC (supposé) | `JJ/MM/AAAA HH:MM:SS`, horodatage de la station de base |
| `Type of mobile` | `mobile_type` | texte | Class A, Class B, Base Station, AtoN, SAR Airborne, Search and Rescue Transponder, Emergency PIRB, Man Overboard Device |
| `MMSI` | `mmsi` | texte | Maritime Mobile Service Identity. Lu en texte : les stations de base n'ont que 7 chiffres dans la source |
| `Latitude` / `Longitude` | `lat` / `lon` | float | degrés décimaux, séparateur « . » ; 91 / 0 = non disponible |
| `Navigational status` | `nav_status` | texte | ex. « Engaged in fishing », « Under way using engine », « Unknown value » |
| `ROT` | `rot` | float | taux de giration, valeur décodée (-708,7 à 708,7) |
| `SOG` | `sog` | float | Speed Over Ground (nœuds) |
| `COG` | `cog` | float | Course Over Ground (degrés) |
| `Heading` | `heading` | int | cap vrai (degrés) |
| `IMO`, `Callsign`, `Name` | `imo`, `callsign`, `name` | texte | identité ; « Unknown » si absent |
| `Ship type` | `ship_type` | texte | **libellé** décodé (« Fishing », « Cargo »...), pas le code numérique ; souvent « Undefined » sur les messages de position |
| `Cargo type` | `cargo_type` | texte | catégorie de cargaison |
| `Width`, `Length` | `width`, `length` | int | dimensions (m) |
| `Type of position fixing device` | `epfd` | texte | GPS, GLONASS, Surveyed... |
| `Draught` | `draught` | float | tirant d'eau (m) |
| `Destination`, `ETA` | `destination`, `eta` | texte | déclaratif |
| `Data source type` | `source_type` | texte | toujours « AIS » sur les jours observés |
| `A`, `B`, `C`, `D` | `dim_a`... `dim_d` | int | distances antenne GPS - étrave, poupe, tribord, bâbord (m) |

Colonnes ajoutées :
- `mmsi_is_9_digits` : booléen.
- `is_class_a` : `mobile_type == "Class A"`, calculé par message.
- `vessel_ship_type` : type connu le plus fréquent du MMSI sur la période.
- `vessel_ship_type_n_distinct` : nombre de types distincts déclarés par le MMSI.
- `vessel_length_m` : longueur la plus fréquente du MMSI.
- `vessel_name` : nom le plus fréquent du MMSI.
- `is_fishing_candidate` : `vessel_ship_type == "Fishing"`, libellé correspondant au code AIS 30.
- `day` : clé de partition.

### Zones restreintes (`src/03_zones.py`)

Source : règlement délégué (UE) 2022/952, qui modifie 2017/118. Le HTML officiel d'EUR-Lex (CELEX 32022R0952) est téléchargé dans `data/reference/source/`, et les 31 tableaux de coordonnées sont lus directement. La structure du texte est vérifiée par assertions : le script s'arrête si elle change.

Zones extraites, avec leur restriction :
- **Annexe I** (« Areas 1 ») : Kims Top (DK00VA247), en 11 sous-zones, et les zones MSFD A à F (Danemark). Engins de fond mobiles interdits.
- **Annexe II** (« Areas 2 ») : trois aires suédoises. Toute pêche interdite.
- **Annexe IV** (« Areas 3 ») : quatre aires suédoises. Seuls certains engins sont autorisés.
- **Annexe V** : zones AIS officielles.

Les côtés sont tracés en loxodromie (rhumb lines), c'est-à-dire en ligne droite dans la projection Mercator. Le script ajoute un tampon de 4 NM (7 408 m) autour des zones danoises, calculé en EPSG:25832, et le compare aux zones AIS de l'annexe V.

Sorties :
- `data/reference/kattegat_zones.geojson` : WGS84, avec la colonne `layer` = `zone` / `buffer` / `ais_zone` ;
- `reports/zones_check.md` : contrôles et points d'attention.

### Carte des trajets (`src/05` à `src/08`)

```bash
.venv/bin/python src/05_protected_sites.py   # Natura 2000 / HELCOM : fichiers à déposer dans data/raw/protected/
.venv/bin/python src/08_map.py               # lance 06 (trajets) et 07 (croisement) si besoin, puis la carte
```

- `05_protected_sites.py` : vérification croisée avec Natura 2000 (EEA, fin 2024) et les aires HELCOM. Elle se fait à partir de fichiers locaux, car leurs domaines sont bloqués dans l'environnement de développement ; une source absente est signalée, jamais remplacée.
- `06_tracks.py` : une ligne par MMSI, coupée au-delà d'un trou de signal (30 min par défaut). Les trajets de moins de 3 points sont écartés, et les lignes simplifiées à 30 m en EPSG:25832.
- `07_crossing.py` : temps et distance passés dans chaque zone et chaque tampon, calculés sur les positions brutes (vitesse supposée constante entre deux messages). Les totaux par trajet sont calculés sur l'union des zones, pour ne pas compter deux fois les tampons qui se chevauchent. Le résultat s'appelle « portion de trajet dans une zone » : ce n'est **pas** une infraction.
- `08_map.py` : les paramètres sont en tête du script (période, seuil de trou, tolérance, animation). Il produit `reports/map.html` (Folium, `prefer_canvas`, LayerControl) et `reports/map_animation.html` (TimestampedGeoJson, sur une page séparée parce que ce plugin ne s'active pas via LayerControl).

Les résultats intermédiaires vont dans `data/processed/tracks/`, avec un nom qui contient les paramètres : un calcul déjà fait avec les mêmes paramètres est réutilisé.

## Documents

`docs/DEFENSE.md` (fiche de défense) · `docs/SECURITE.md` · `docs/DONNEES.md` · `docs/PARAMETRES.md` ·
`docs/PILOTES.md` (concurrence, offre, pilotes, questions d'entretien) · `docs/PITCH.md` · `docs/VIDEO.md` ·
`docs/EQUIPE.md` (interfaces, responsabilités, planning).
