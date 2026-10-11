# Pipeline de données du Kattegat (`src/`)

Détail technique du pipeline de données réelles, déplacé du README. Vue d'ensemble : [README](../README.md).

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

## Colonnes du CSV source (en-tête réel lu dans les fichiers, 26 colonnes)

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

## Zones restreintes (`src/03_zones.py`)

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

## Carte des trajets (`src/05` à `src/08`)

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

## Contexte : courant, vent et vagues (`src/09` à `src/11`)

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

## Courant et vent de démonstration (`src/12_simulate_context.py`)

En attendant les vraies données (identifiants Copernicus, quota Open-Meteo), la démonstration utilise un courant et un vent **simulés**, réalistes mais fictifs. La graine est fixe, donc le résultat est reproductible.

- **Vent** : déduit, heure par heure, des vagues réelles d'Open-Meteo (direction des vagues, et vitesse par la relation de Pierson-Moskowitz majorée de 15 %).
- **Courant** : quatre composantes additionnées.
  - Sortie de la Baltique vers le nord, plus forte côté suédois.
  - Dérive due au vent (2,5 %, déviée de 30° vers la droite).
  - Marée M2 faible.
  - Courants plus forts dans l'Øresund et le Grand Belt.
- **Priorité** : `11_context.py` prend les vraies données en priorité dès qu'elles sont présentes. La carte cite alors « données de démonstration SeaTrace » comme source.
- **Fichiers** : `data/simulated/`, ignoré par git.

## Trajets de démonstration (`src/demo_boats.py`)

Trois vrais trajets de chalutiers présumés, de classe A, sans trou naturel de plus de 30 min et avec au moins 200 points, sont copiés puis modifiés pour la démonstration. Les paramètres sont en tête du script.

- **Silence pendant la traversée d'une zone restreinte** : 45 min sans aucun message.
- **Saut de position** : décalage de 10 km pendant 20 min, avec les mêmes horodatages.
- **Trou de 45 min** loin des zones et des tampons, dans un secteur de faible couverture.

Les données réelles restent intactes. Dans `data/processed/map_input_*.parquet`, lu par `06_tracks.py`, chaque copie **remplace** son trajet source : le nombre de navires est inchangé. Les copies portent des MMSI au code pays non attribué par l'UIT (217, 221, 260) : aucun navire réel ne peut avoir ces numéros.

Les fichiers privés sont ignorés par git :
- `data/private/` : vérité terrain, table `vessel_id_map.csv`, liste `excluded_from_stats.csv` ;
- `reports/internal/` : `demo_notes.md` et `map_with_truth.html`.

Les navires listés dans `excluded_from_stats.csv` sont exclus des statistiques (`04_report.py` ; à appliquer aussi dans l'évaluation de l'algorithme).

```bash
.venv/bin/python src/demo_boats.py && .venv/bin/python src/08_map.py
```

## Explorateur de navires et positions AIS (`reports/map.html`)

- **Positions AIS** : un point par message AIS, au plus un par minute ; au plus un point toutes les 10 min pour un navire immobile. Ils sont dessinés sur un canevas, et la couche « Positions AIS » s'active ou se masque en haut à droite.
- **Explorateur** : panneau repliable en bas de la carte.
  - Colonnes : MMSI, nom, longueur, vitesse moyenne, trous de plus de 30 min, passage en zone, durée en zone.
  - Recherche, tris, filtre « Favoris seulement ».
  - Favoris à étoiles, enregistrés dans le navigateur (`localStorage`, avec repli en mémoire), et bouton « Réinitialiser les favoris ».
- **Clic sur un navire** : zoom sur son trajet, trait épaissi, ouverture de sa fiche et affichage de ses trous de signal en pointillés.
- **Branchement de l'algorithme** : les trous sont aussi dans les données intégrées (`DATA.gaps`). L'algorithme SeaTrace s'y branche (section suivante).

## Algorithme SeaTrace sur la carte (`src/13_reconstruct.py`)

Le même algorithme que l'interface Flask, appliqué à **tous** les navires de la carte, sans traitement particulier pour aucun :

- **Intégrité** (`seatrace/detect.py`) : sauts de position, identité dédoublée, rejeu…
- **Reconstruction** (`seatrace/reconstruct.py`, HMM sur grille à cap persistant) de chaque trou de plus de 30 min et de chaque bloc de positions écartées par un saut :
  - grille de la fenêtre que le navire peut atteindre, mer = hors du trait de côte Natural Earth (`data/reference/kattegat_land.geojson`) ;
  - probabilité **exacte** de passage dans une zone restreinte et dans un tampon de 4 milles ;
  - carte de présence, contour à 90 %, 30 trajets tirés (sur 300), zones les plus probables, indicateur de faible couverture (autres navires ayant perdu le signal à moins de 10 km).
- **Limites** : au-delà de 6 h, « non reconstruit » (pas d'extrapolation). Navire immobile dont les positions tombent sur la terre (quai) : « à quai ».
- **Niveau d'alerte**, calculé seulement à partir de ces résultats :
  - « à examiner » : alerte d'intégrité, ou passage en zone restreinte estimé à au moins 50 % ;
  - « à vérifier » : passage possible (au moins 10 %), tampon probable, silence d'un navire en route que la réception n'explique pas, ou trou impossible à reconstruire ;
  - « trous de signal » : les autres trous.
- **Lecture** : « sous ces hypothèses, probabilité estimée que le trajet ait traversé la zone », jamais un constat d'infraction.

```bash
python src/13_reconstruct.py   # ~5 min pour 208 navires et 122 périodes ; sortie data/processed/reconstructions_<période>.json
python src/08_map.py           # carte complète (données brutes nécessaires)
python src/08_map.py --refresh # sans les données brutes : régénère seulement le panneau de reports/map.html
python eval/evaluer_carte_kattegat.py   # évaluation (seul script qui lit data/private/)
```

**Tout relancer, dans l'ordre.** `13_reconstruct.py` lit les positions écrites par `08_map.py` (via `06_tracks.py`) et la carte `reports/map.html` : la carte est donc produite une fois avant l'algorithme, puis une fois après pour y intégrer ses résultats. Sous Windows, remplacer `.venv/bin/python` par `.venv\Scripts\python`.

```bash
.venv/bin/python src/01_download.py --start 2026-09-15 --end 2026-09-17
.venv/bin/python src/02_filter.py   --start 2026-09-15 --end 2026-09-17
.venv/bin/python src/03_zones.py
.venv/bin/python src/demo_boats.py
.venv/bin/python src/08_map.py        # trajets et positions, carte sans analyse
.venv/bin/python src/13_reconstruct.py
.venv/bin/python src/08_map.py        # carte avec les résultats de l'algorithme
.venv/bin/python src/04_report.py   --month 2026-09
```

**Positions.** Si `data/processed/tracks/points_<période>_gap30_tol30.parquet` existe, l'algorithme utilise au plus une position par minute. Sinon, il prend ce que porte la carte publiée : une position par navire et par tranche de 10 min (`reports/map_animation.html`), plus les bornes exactes des trous (`DATA.gaps`). Voir `seatrace/carte_kattegat.py`.

**Dans la carte.** Seul le navire étudié reçoit les résultats :
- carte de présence, contour à 90 %, trajets tirés en transparence et marqueurs sur les sauts ;
- probabilités dans l'infobulle du trou et dans la fiche du navire ;
- colonnes « Alerte » et « P(zone) max » dans l'explorateur, avec tri ;
- le style des trajets ne change pas.

`map.html?vessel=<MMSI>` ouvre directement un navire.

**Dans l'interface Flask.** Les liens vont dans les deux sens :
- Onglet « Fleet screening » : tous les navires par niveau. Les boutons *Analyse* et *Map* ouvrent le navire dans le jeu « Kattegat fishing fleet » ou sur la carte. Pour *Analyse*, la période et la zone les plus probables sont présélectionnées, pour comparer les cinq modèles.
- La fiche du navire dans la carte renvoie vers le tableau de bord quand la carte est servie par Flask.

## Faire tourner l'algorithme sur la carte (`src/13_seatrace_export.py`)

La carte et l'algorithme ne lisent pas le même format. Ce script fait le pont, sans modifier les fichiers de la
carte : positions des navires réels et des bateaux test (fichiers `*_reported` seulement), terres, zones du
règlement 2022/952 et courant. Détails, contrat à respecter par la V2 de la carte et résultats :
`docs/CARTE_ALGORITHME.md`.

```bash
.venv/bin/python src/13_seatrace_export.py [--start 2026-09-15] [--end 2026-09-17] [--mmsi 219000001 ...]
.venv/bin/python serveur.py      # jeu « Kattegat map » déjà chargé avec terres, zones et courant
.venv/bin/python eval/evaluer_kattegat.py
```

- Un trajet n'est coupé qu'au-delà de 6 h sans signal (la carte coupe à 30 min) : un trou plus court reste dans le
  trajet pour pouvoir être reconstruit.
- Une position par minute au plus ; la dernière position avant un trou et la première après sont toujours gardées.
- 50 000 lignes au plus (limite d'import) : bateaux test, puis navires passant en zone ou en tampon.
- Lien direct vers un navire : `http://127.0.0.1:8050/?dataset=ais-kattegat_2026-09-15_2026-09-17&boat=<MMSI>`.

