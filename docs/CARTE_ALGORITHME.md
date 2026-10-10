# Faire tourner l'algorithme sur la carte du Kattegat

> Pont entre la carte (`src/`, Folium) et l'algorithme (`seatrace/`, interface Flask). Vérifié le 10/10/2026
> sur les bateaux test A, B, C (15 au 17/09/2026) et sur un jour réel (15/09/2026, 46 navires de pêche).
> Les résultats ne valent pas validation : trois bateaux test et un jour de données.

## En bref

```bash
# après la carte (src/01 à 08, ou au moins data/synthetic/ et data/reference/)
python src/13_seatrace_export.py                  # 15 au 17/09/2026 par défaut
python serveur.py                                 # http://127.0.0.1:8050
python eval/evaluer_kattegat.py                   # facultatif : note A, B, C contre la vérité
```

Dans l'interface, le jeu « Kattegat map: … » est déjà chargé, avec trois couches :
- les terres (Natural Earth) ;
- les zones du règlement (UE) 2022/952 ;
- le courant, si `src/09` ou `src/12` a tourné.

Il reste à choisir un bateau, une période et une zone, puis à cliquer sur « Reconstruct ». Un lien peut aussi
ouvrir directement un navire : `http://127.0.0.1:8050/?dataset=ais-kattegat_2026-09-15_2026-09-17&boat=999000001`.

**La carte HTML n'est pas lue par l'algorithme.** La V2 peut changer librement son affichage. Seuls comptent
les fichiers de données listés ci-dessous.

## 1. Le contrat : ce que la V2 de la carte doit continuer à produire

| Fichier | Ce que l'export lit | Produit par | Obligatoire |
|---|---|---|---|
| `data/synthetic/boat_<X>_reported.parquet` | `mmsi`, `timestamp` (UTC), `lat`, `lon`, `sog`, `cog`, `vessel_name`, `vessel_length_m`, `scenario` | `src/synthetic.py` | non (bateaux test) |
| `data/processed/ais_kattegat_<AAAA-MM>.parquet/day=…/` | les mêmes colonnes, sauf `scenario` | `src/02_filter.py` | non (navires réels) |
| `data/reference/kattegat_zones.geojson` | `layer` (`zone`, `buffer`, `ais_zone`), `zone_id`, `category`, `country`, `name` | `src/03_zones.py` | oui |
| `data/processed/tracks/crossing_tracks_<début>_<fin>_*.parquet` | `track_id` = `<mmsi>-<n>`, colonnes `minutes_in_*` | `src/07_crossing.py` | non (ordre de sélection) |
| courant de `11_context.load_sources()` | `currents` : xarray `u`, `v` en nœuds, dimensions `time`, `latitude`, `longitude` | `src/09` (Copernicus) ou `src/12` (simulé) | non |
| `data/raw/basemap/ne_10m_land.geojson` | polygones de terres | `src/04_report.py`, sinon téléchargé par l'export | non (le fichier découpé est versionné) |

Il faut au moins un des deux jeux de positions (bateaux test ou navires réels).

**Si la V2 renomme une de ces colonnes ou déplace un de ces fichiers**, il faut adapter
`src/13_seatrace_export.py` (fonctions `standard`, `read_real`, `crossing_priority`, `export_currents`).
`tests/test_carte_algorithme.py` signalera la casse.

**Règle inchangée** : l'export ne lit que les fichiers `*_reported` des bateaux test. La vérité n'est lue que
par `eval/` et `tests/`, et `tests/test_synthetic.py` le vérifie.

## 2. Ce que fait l'export (`src/13_seatrace_export.py`)

Le script écrit trois fichiers :
- `data/ais/kattegat_<début>_<fin>.csv`, au format partagé de SeaTrace : `boat_id, trip_id, timestamp, lat, lon,
  sog_kn, cog_deg, accuracy_m, source, synthetic` ;
- un fichier `.json` à côté, avec les bateaux et les couches à charger ;
- `data/reference/kattegat_land.geojson`, versionné.

Si une source de courant existe, il écrit aussi `data/processed/seatrace/currents_<début>_<fin>.csv`.

Trois choix diffèrent de la carte, volontairement.

- **Un trajet n'est coupé qu'au-delà de 6 h sans position.** La carte, elle, coupe à 30 min. Un trou plus court
  doit rester *dans* le trajet pour être reconstruit, et 6 h est la durée maximale qu'un modèle accepte.
- **Une position par minute au plus.** Le détecteur attend environ 60 s, alors que l'AIS de classe A émet toutes
  les 2 à 10 s. La dernière position avant un trou et la première après sont toujours gardées : les bornes de
  la reconstruction ne bougent pas.
- **Au plus 50 000 lignes**, la limite d'import de l'interface. L'export garde d'abord les bateaux test, puis
  les navires qui passent en zone ou en tampon (temps décroissant), puis ceux qui ont le plus long trou.
  `--mmsi 219000001 …` force un choix précis.

Pour le courant, les modèles n'acceptent qu'un vecteur constant par reconstruction. L'interface prend donc la
moyenne de la table sur ce que le bateau peut atteindre pendant le trou, période élargie de 30 min de chaque
côté. La source du courant est écrite dans le rapport et le journal.

## 3. Lien depuis la carte V2 (facultatif)

Dans `src/08_map.py`, deux ajouts suffisent.

1. Ajouter `"seatrace": f"ais-kattegat_{PERIOD_START}_{PERIOD_END}"` au dictionnaire de `build_data`.
2. Ajouter ce bouton dans `vesselCard` :

```js
<a class="st-btn" target="_top"
   href="http://127.0.0.1:8050/?dataset=${DATA.seatrace}&boat=${v.mmsi}">Reconstruire avec SeaTrace</a>
```

`target="_top"` sert dans l'onglet « Kattegat map » de l'interface, où la carte est affichée dans un cadre : le
lien ouvre alors l'application entière sur ce navire. Le libellé est déjà traduit en anglais dans
`web/kattegat_i18n.py`.

## 4. Ce qui a changé dans l'algorithme pour ces données, et pourquoi

Avant chaque changement de détection, le banc synthétique de `eval/run_eval.py` (mêmes graines) et les 7
scénarios de démonstration ont été comparés avant et après. **Ils sont strictement identiques** : rappel,
précision, périodes, positions écartées.

| Problème constaté sur les vrais trajets | Correction |
|---|---|
| **Rejeu, fausses alertes** : un chalutier qui file droit à 2,5 nœuds produit des déplacements identiques au mètre près d'une minute à l'autre. C'est aussi le cas avec un rythme d'émission irrégulier (60, 60, 60, 70 s) qui se répète. 3 fausses alertes sur A, 1 sur B. | Une fenêtre n'est comparée que si sa **vitesse** varie d'au moins 10 m sur un pas : `rejeu_variation_min_m` dans `config/parametres.json`, version 0.5.0. Une ligne droite ne porte pas d'empreinte. |
| **Rejeu, lenteur** : 13 s pour analyser 3 jours de chalutier (comparaison de toutes les paires de fenêtres en Python). | Même résultat, calculé en tableaux : **0,27 s**. |
| **Bloc décalé long** : B a des positions décalées de 10 km pendant 60 min. La règle « bloc entre deux sauts » s'arrêtait à 30 positions, si bien que seules 19 des 58 positions décalées étaient écartées. | Un saut suivi d'un saut **retour** (déplacements opposés) isole tout le bloc, jusqu'à 6 h. Résultat : **58 sur 58**. |
| **Grille trop grosse** : la grille couvrait tout le trajet (~90 km sur 3 jours), soit des cases de 0,6 km. | La grille couvre ce que le bateau peut atteindre pendant la période (`pipeline.bornes_reconstruction`). Les cases font **0,23 km** sur A. |
| **Heures ambiguës** : les périodes n'affichaient que l'heure (`02:35:43`) sur un trajet de 3 jours. | La date est affichée quand le trajet couvre plusieurs jours. Champs `start_utc` et `end_utc` ajoutés à l'API. |
| **Bug existant** : une reconstruction *sans zone* plantait dès que le modèle appris et le HMM tournaient ensemble. | Les probabilités absentes sont exclues du comparatif. Test ajouté. |

Autres ajouts :
- le rapport indique le courant utilisé et la taille des cases ;
- un bateau test est décrit comme « copie d'un vrai trajet, manipulation simulée », et non comme « géographie
  fictive ».

## 5. Résultats

### Bateaux test A, B, C (`eval/resultats/kattegat_bateaux_test.json`)

Conditions : cinq modèles, courant simulé de `src/12`, bateaux test seuls. La reconstruction passe par l'API de
l'interface. Pour reproduire : `python src/12_simulate_context.py` (après `src/10_meteo.py`), puis
`python src/13_seatrace_export.py --no-real`, puis `python eval/evaluer_kattegat.py`.

| | A : silence de 45 min commencé dans une zone fermée | B : positions décalées de 10 km pendant 60 min | C : trou innocent de 45 min |
|---|---|---|---|
| Période trouvée | 45,2 min (écart aux vraies bornes : −5 s, +4 s) | 80 min (le bloc et, par prudence, ~10 min de chaque côté) | 45,7 min (−22 s, 0 s) |
| Alertes d'intégrité | aucune | 2 sauts de 9,9 km (321 nœuds), 2 dérives cumulées | 1 fausse alerte (voir limites) |
| Positions décalées écartées | — | **58 sur 58** (19 de plus, autour des sauts) | — |
| Passage dans la zone | `SE0510127-A2` (Fladen, pêche interdite) : **100 %** pour HMM, modèle appris, particules et pont brownien ; 87 % pour le boosting | sans objet | aucune zone restreinte à portée : la plus proche est à 39 km, la portée maximale à 23 km |
| Erreur moyenne de la position reconstruite | 0,17 à 0,24 km | 0,03 à 0,50 km | 0,87 à 1,19 km |
| Cases, temps (5 modèles) | 0,23 km ; 10 s | 0,37 km ; 6 s | 0,23 km ; 4 s |

Lecture : A est un cas facile, car la dernière position avant le silence est déjà dans la zone. C'est voulu par le
scénario, et l'outil le confirme sans hésiter. B montre l'enchaînement complet : détecter, écarter, puis
reconstruire où le bateau était vraiment. Le boosting reste en dessous des autres modèles parce qu'il a appris sur
la baie fictive (hors domaine). L'interface le signale.

### Un jour réel (15/09/2026 : 46 navires de pêche, 49 998 positions)

- **Temps** : analyse en 0,06 s par trajet (médiane).
- **Périodes** : 421 périodes à reconstruire, presque toutes des trous de réception ; 106 durent plus de
  15 min, 29 plus de 45 min.
- **Alertes d'intégrité** : 5 trajets sur 46. Le cas le plus net est le MMSI **265586020**. Il fait des sauts
  répétés de 200 à 440 m en 11 à 19 s (32 à 66 nœuds) et semble émettre depuis deux endroits
  (`identite_dedoublee`). C'est un vrai cas à regarder sur la carte : GNSS défaillant, deux transpondeurs, ou
  autre chose.

## 6. Limites

- **Fausse alerte « dérive cumulée » à l'arrêt.** Sous ~2 nœuds, le cap AIS déclaré n'a pas de sens (139°, 20°,
  303° d'une minute à l'autre), et l'estime le prend au sérieux. C'est le cas de C à 19:02.
  - Un garde-fou « vitesse médiane ≥ 2 nœuds » a été essayé. Il fait baisser le rappel « dérive » du banc
    synthétique de 97,9 % à 93,8 %, et n'enlève qu'une alerte sur 9 dans les vraies données. Il n'a donc pas été
    gardé.
  - À trancher par l'équipe.
- **Terres Natural Earth au 1:10 M** : précision d'environ 1 km. Cela suffit au large, pas dans les passages
  étroits. On peut importer les polygones de terres OpenStreetMap comme couche.
- **Courant** : un vecteur moyen par reconstruction. Le courant simulé de `src/12` est fictif.
- **Calibration** : les modèles sont réglés sur données synthétiques. Seuils de détection pensés pour des
  traceurs émettant toutes les ~60 s.
- **Limite d'import** : 50 000 lignes par jeu. Pour un mois complet, choisir les navires avec `--mmsi`.
- **Portée de l'évaluation** : trois bateaux test et un jour réel ne font pas une validation.
