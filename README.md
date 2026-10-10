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

## Documents

`docs/MODELES.md` (les cinq modèles, comparatif, segmentation) · `docs/DONNEES_OUVERTES.md` (télécharger et
importer les données ouvertes) · `docs/DEFENSE.md` (fiche de défense) · `docs/SECURITE.md` · `docs/DONNEES.md` ·
`docs/PARAMETRES.md` ·
`docs/PILOTES.md` (concurrence, offre, pilotes, questions d'entretien) · `docs/PITCH.md` · `docs/VIDEO.md` ·
`docs/EQUIPE.md` (interfaces, responsabilités, planning).
