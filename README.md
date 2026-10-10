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

Python 3.10 ou plus. Sur macOS, la commande s'appelle `python3` (c'est `python` une fois l'environnement activé).

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
python -m pip install -r requirements.txt
python serveur.py                    # interface V2 (Flask + carte Leaflet) : http://127.0.0.1:8050
```

L'ancienne interface Streamlit (V1) reste disponible : `streamlit run app.py` (http://127.0.0.1:8501).

Autres commandes :

```bash
python -m pytest                                        # 63 tests (modèles, sécurité des imports, API web)
python eval/comparer_modeles.py                         # comparatif des 5 modèles (~5 min) -> eval/resultats/comparatif.json
python eval/comparer_modeles.py --rapide                # version réduite (~3 min)
python eval/run_eval.py                                 # évaluation V1 détaillée (détection, réglage du HMM)
python scripts/comparer.py --trajet S6-ambigu --zone Z1 --rf    # comparatif sans interface + rapport HTML
python scripts/importer_ais.py aisdk-2024-06-15.zip --bbox 54.6 55.4 10.2 11.2 --types ais_voilier ais_plaisance
python scripts/apprendre_historique.py --fichier data/ais/aisdk-2024-06-15.csv --sortie data/modeles/appris_dma.npz
```

Fonctionne hors ligne : Leaflet et Chart.js sont embarqués (`web/static/vendor`), aucune API externe. Le fond
OpenStreetMap est optionnel (case dans le sélecteur de couches de la carte, nécessite Internet).

## Nouveautés de la V2

- **Cinq modèles probabilistes** lancés sur la même période et **comparés** (`docs/MODELES.md`) : HMM sur grille
  à cap persistant (V1), modèle **appris sur des historiques**, **filtre particulaire avec lissage** (FFBSi),
  **pont brownien** (BBMM) et **boosting stochastique** qui prédit directement la probabilité. Les probabilités
  ne sont jamais moyennées : l'outil montre leur écart et dit quand les modèles divergent.
- **Tous les chemins générés sont conservés avec leur probabilité** (`data/chemins/<calcul>/chemins.csv`,
  export GeoJSON) : poids de chaque chemin dans la loi du modèle, log-probabilité exacte pour le HMM.
- **Segmentation des chemins par ACP** : les chemins de tous les modèles sont projetés dans le repère de la ligne
  directe, réduits par ACP, puis regroupés en **familles** (« par le sud de l'île, en traversant la zone »). Le
  nombre de familles est choisi par BIC et silhouette ; chaque famille a une probabilité **par modèle**.
- **Données ouvertes** (`docs/DONNEES_OUVERTES.md`) : AIS de l'Autorité maritime danoise et de NOAA
  MarineCadastre (CSV/ZIP), terres et zones en GeoJSON (OSM, Natural Earth, Shom, CNSP, Protected Planet),
  densité EMODnet (XYZ), courants Copernicus Marine (CSV), détections Sentinel-1/2 ou GFW SAR (CSV). Projection
  locale propre à chaque jeu de données.
- **Interface Flask + Leaflet** : carte interactive (chemins cliquables, corridors par modèle, familles), zone
  dessinable à la main, import de couches, onglets comparatif / familles / chemins / évaluation / sources.

### Pourquoi Flask plutôt que Streamlit

Streamlit est idéal pour un premier prototype, mais il ré-exécute tout le script à chaque clic : l'état est
fragile, l'interaction avec la carte est limitée (sélectionner un chemin, dessiner une zone) et le calcul est
mêlé à l'affichage. La V2 sépare une **API JSON** (réutilisable par un client, testée) d'une page HTML/JS avec
une vraie carte Leaflet. Coût : plus de code front-end ; la V1 Streamlit reste disponible.

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
serveur.py                 lance l'interface V2 (Flask), locale par défaut (127.0.0.1:8050)
web/app.py                 API JSON + en-têtes de sécurité ; web/templates, web/static (Leaflet, Chart.js embarqués)
app.py                     interface V1 (Streamlit)
seatrace/
  config.py                paramètres versionnés et bornés (config/parametres.json)
  schema.py                format partagé + import sécurisé + exports protégés
  sources.py               connecteurs : AIS DMA / NOAA, GeoJSON, densité XYZ, courants, observations externes
  catalogue.py             sources de données envisagées et statut réel dans le prototype
  geo.py                   géographie fictive, projection locale par jeu de données, grille
  scenarios.py             laboratoire : trajets synthétiques et manipulations simulées
  detect.py                contrôles explicables, isolation, périodes à reconstruire
  reconstruct.py           HMM à cap persistant (V1) : passage exact, sensibilité, probabilité des chemins
  appris.py                modèle appris sur des historiques (vitesses, changements de cap, densité de trafic)
  particules.py            filtre particulaire guidé + lissage FFBSi
  pont_brownien.py         pont brownien, variance de mobilité estimée sur le trajet
  boosting.py              boosting stochastique (ensemble), descripteurs, sans pickle
  modeles.py               format commun des résultats et des chemins
  segmentation.py          ACP + familles de chemins
  comparatif.py            lance les modèles, segmente, lecture commune (sans moyenne)
  archive.py               conservation de tous les chemins + probabilités, GeoJSON, PNG
  pipeline.py              assemblage, observations externes, couches importées
  report.py                rapports HTML autonomes (échappés) V1 et comparatif V2
  audit.py                 journal local chaîné (SHA-256)
eval/                      run_eval.py (V1) et comparer_modeles.py (comparatif des 5 modèles)
scripts/                   données de démo, analyse et comparatif en ligne de commande, import AIS, apprentissage
data/modeles/              modèle appris (.npz, sans pickle) et exemples d'entraînement du boosting (.csv)
tests/                     tests unitaires, de sécurité et de l'API web
docs/                      modèles, données ouvertes, défense, sécurité, données, paramètres, pilotes, pitch, équipe
```

## Modèle probabiliste de référence (V1) et pourquoi

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

Non opérationnel (feuille de route) : intégration directe de traceurs, observations RF réelles, téléchargement
automatique des données ouvertes (les fichiers sont téléchargés par l'utilisateur), bathymétrie, vent,
authentification et multi-clients, détection d'anomalies communes à plusieurs bateaux.

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

## Comparatif des cinq modèles (`eval/resultats/comparatif.json` — données synthétiques)

159 interruptions sur des bateaux jamais vus (échantillon enrichi en interruptions proches de la zone, 22 % de
passages réels). Score de Brier : plus bas = meilleur. IC à 95 % par bootstrap apparié.

| Modèle | Brier (IC 95 %) | Échecs | Erreur spatiale | Couverture 90 % | Temps médian |
|---|---|---|---|---|---|
| HMM à cap (V1) | 0,070 (0,045–0,098) | 0 | 2,2 km | 99 % | 0,41 s |
| Appris sur historiques | 0,087 (0,053–0,122) | 0 | 2,0 km | 93 % | 0,41 s |
| Particules + lissage | 0,077 (0,045–0,112) | 0 | 1,8 km | 90 % | 0,12 s |
| Pont brownien | 0,094 (0,063–0,148) | 8 | 1,9 km | 48 % | 0,006 s |
| Boosting stochastique | **0,041** (0,018–0,069) | 0 | — | — | 0,003 s* |
| *Interpolation linéaire / taux de base* | *0,126 / 0,173* | | | | |

\* hors calcul du HMM et du pont, dont il utilise les probabilités comme descripteurs (empilement).

Lecture honnête : le boosting est le meilleur sur cet échantillon (écart significatif), mais il a appris sur la
même baie fictive et s'appuie surtout sur la probabilité du HMM ; hors de ce domaine, il n'est qu'indicatif. Le
filtre particulaire donne les chemins les plus précis ; le HMM est le plus prudent (couverture 99 %) ; le pont
brownien sous-estime l'incertitude (couverture 48 %) et échoue quand une île barre la ligne directe. Le modèle
appris dépend de son historique : il est meilleur quand le trajet suit les habitudes, moins bon sinon. D'une
série de tirages à l'autre (version rapide), l'ordre HMM / particules / appris change : leurs écarts ne sont
pas significatifs. Rien de tout cela ne vaut validation sur données réelles.

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
- `08_map.py` : les paramètres sont en tête du script (période, seuil de trou, tolérance, seuil « quasi immobile », animation). Il produit :
  - `reports/map.html` : carte Folium (`prefer_canvas`, LayerControl), avec un panneau latéral qui contient :
    - **Étudier un navire** : recherche par nom ou par MMSI, puis une fiche (pavillon déduit du MMSI, longueur, période, vitesse, statut « en pêche » déclaré, temps en zone et en tampon, zones traversées). Les autres navires restent affichés en gris.
    - **Filtres** : passage en zone ou en tampon, longueur, pavillon, statut « en pêche », jours, trajets quasi immobiles.
    - **Légende**.
    - **Glossaire** (« Comprendre les termes ») qui explique chaque dénomination.

    Les zones sont séparées par type de restriction, avec des infobulles en langage courant.
  - `reports/map_animation.html` : trajets animés avec TimestampedGeoJson, sur une page séparée parce que ce plugin ne s'active pas via LayerControl.

Les résultats intermédiaires vont dans `data/processed/tracks/`, avec un nom qui contient les paramètres : un calcul déjà fait avec les mêmes paramètres est réutilisé.

### Contexte : courant, vent et vagues (`src/09` à `src/11`)

Contexte pour interpréter les trajets, **pas** pour détecter des infractions. Il couvre seulement la période de la carte, du 15 au 17/09/2026.

```bash
.venv/bin/python src/09_currents.py [--wind]   # courants Copernicus (identifiants requis) ; --wind : vent Copernicus en secours
.venv/bin/python src/10_meteo.py               # vent (archive Open-Meteo) et vagues (API marine Open-Meteo, DWD), avec cache
.venv/bin/python src/08_map.py                 # lance 11_context.py puis régénère la carte
```

- **Courants** : jeu `cmems_mod_bal_phy_anfc_PT15M-i` du produit `BALTICSEA_ANALYSISFORECAST_PHY_003_006`, variables `uo` et `vo` en m/s, valeurs de surface toutes les 15 min.
  - Ce jeu contient la marée : le manuel du produit décrit à part des jeux « détidés », filtrés sur 39 h (filtre de Doodson).
  - Identifiants : `COPERNICUSMARINE_SERVICE_USERNAME` et `COPERNICUSMARINE_SERVICE_PASSWORD`, ou `copernicusmarine login`.
- **Vent et vagues** : grille de 0,25°, points en mer seulement.
  - Les réponses sont mises en cache dans `data/raw/meteo/`.
  - Un point qu'Open-Meteo déplace de plus de 10 km vers sa maille de mer est écarté.
- **`11_context.py`** :
  - interpolation linéaire en espace et en temps, avec repli sur la maille la plus proche ;
  - pour chaque position dans une zone ou un tampon : vitesse et direction du courant, angle entre cap de route et courant, **estimation** de la vitesse par rapport à l'eau, vent et vagues ;
  - moyennes par portion, plus des grilles journalières pour les flèches.
- **Carte** :
  - couches « Courant moyen du JJ/MM » et « Vent moyen du JJ/MM », masquées par défaut ;
  - conditions affichées dans l'infobulle des passages ;
  - résumé par jour et sources dans le panneau.

  Une source absente est affichée « non disponible ».

### Courant et vent de démonstration (`src/12_simulate_context.py`)

En attendant les vraies données (identifiants Copernicus, quota Open-Meteo), la démonstration utilise un courant et un vent **simulés**, réalistes mais fictifs. La graine est fixe, donc le résultat est reproductible.

- **Vent** : déduit, heure par heure, des vagues réelles d'Open-Meteo (direction des vagues, et vitesse par la relation de Pierson-Moskowitz majorée de 15 %).
- **Courant** : quatre composantes additionnées.
  - Sortie de la Baltique vers le nord, plus forte côté suédois.
  - Dérive due au vent (2,5 %, déviée de 30° vers la droite).
  - Marée M2 faible.
  - Courants plus forts dans l'Øresund et le Grand Belt.
- **Priorité** : `11_context.py` prend les vraies données en priorité dès qu'elles sont présentes. La carte cite alors « données de démonstration SeaTrace » comme source.
- **Fichiers** : `data/simulated/`, ignoré par git.

### Bateaux test synthétiques (`src/synthetic.py`)

Trois copies de vrais trajets de chalutiers présumés (classe A ; l'engin n'est pas dans l'AIS), avec des MMSI fictifs. Les données réelles ne sont jamais modifiées. Les trois navires sources sont **retirés du jeu final** (`EXCLUDED_MMSI` dans `src/02_filter.py`), et donc des trajets et de la carte : le détecteur ne voit pas à la fois l'original et sa copie modifiée. Ils restent dans `data/interim/`, où `src/synthetic.py` les lit.

| Bateau | MMSI | Scénario | Trajet réel copié |
|---|---|---|---|
| A | 999000001 | Silence de 45 min commencé dans une zone où la pêche est interdite (Fladen), réapparition hors de toute zone restreinte | `220345000-162` |
| B | 999000002 | Positions décalées de 10 km (cap 60°) pendant 60 min, puis retour à la normale | `220317000-152` |
| C | 999000003 | Témoin négatif : trou de 45 min, à 39 km de toute zone, dans une zone de faible couverture | `266388000-315` |

```bash
.venv/bin/python src/synthetic.py [--silence-min 45] [--shift-km 10] [--shift-min 60] [--shift-bearing 60] [--gap-min 45]
```

- **Ce que voit le détecteur** : `data/synthetic/boat_<X>_reported.parquet`.
- **La vérité** : `data/synthetic/boat_<X>_truth.parquet` et `summary_truth.json`.

**Le détecteur ne lit jamais les fichiers de vérité.** Seule l'évaluation y a accès, via `load_truth(..., for_evaluation=True)`. `tests/test_synthetic.py` vérifie qu'aucun code de `src/`, `seatrace/`, `scripts/` ou `web/` ne lit `*_truth`, et que les fichiers « reported » ne contiennent aucune information de vérité.

## Documents

`docs/MODELES.md` (les cinq modèles, comparatif, segmentation) · `docs/DONNEES_OUVERTES.md` (télécharger et
importer les données ouvertes) · `docs/DEFENSE.md` (fiche de défense) · `docs/SECURITE.md` · `docs/DONNEES.md` ·
`docs/PARAMETRES.md` ·
`docs/PILOTES.md` (concurrence, offre, pilotes, questions d'entretien) · `docs/PITCH.md` · `docs/VIDEO.md` ·
`docs/EQUIPE.md` (interfaces, responsabilités, planning).
