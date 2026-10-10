# Les cinq modèles de reconstruction (V2)

> Tous les chiffres viennent de `eval/comparer_modeles.py` sur des données **synthétiques** (baie fictive).
> Ils montrent comment chaque méthode se comporte, pas qu'elle est validée sur données réelles.

La question reste la même : *« pendant cette période où les positions manquent ou sont suspectes, par où ce
bateau a-t-il pu passer, et avec quelle probabilité est-il entré dans la zone ? »* La V2 y répond avec cinq
modèles aux hypothèses différentes. **Aucune moyenne n'est faite** : un accord entre modèles renforce la
lecture, un désaccord indique que la réponse dépend des hypothèses (« l'outil ne permet pas de conclure »).

## 1. HMM sur grille à cap persistant (`seatrace/reconstruct.py`, V1)

État = case de 200 m × cap (8 directions) × drapeau « déjà passé dans la zone ». Passe avant/arrière exacte,
conditionnée avant ET après l'interruption. Le drapeau donne **exactement** P(au moins une entrée dans la zone),
sous hypothèses et à la résolution de la grille. Paramètres réglés sur le jeu de réglage (Brier).
V2 : chaque chemin tiré porte sa **probabilité a posteriori exacte** (`log_proba`) ; un courant peut être ajouté.

## 2. Modèle appris sur des historiques (`seatrace/appris.py`)

Même machinerie que le HMM, mais trois lois sont **estimées** sur des trajets passés (AIS DMA/NOAA, historiques
d'un client, ou flotte synthétique d'apprentissage) :
- la loi des vitesses, en rapport à la vitesse de croisière de chaque trajet (arrêts compris), remise à
  l'échelle du type du bateau étudié ;
- la matrice de changement de cap entre deux pas de temps (fréquences + lissage de Laplace) ;
- un a priori de densité de trafic (cases historiquement fréquentées), avec un exposant réglé sur le jeu de
  réglage (0,1 retenu ; 0 = ignoré). Il n'est utilisé que si l'historique couvre la zone étudiée.

Les positions suspectes sont écartées avant l'apprentissage ; seuls les segments continus sont utilisés. Le
modèle est stocké en `.npz` (tableaux numériques, lu sans pickle). **Limite** : il favorise les habitudes ;
un trajet volontairement inhabituel y est jugé moins probable (S6 avec l'observation RF simulée : 43 % contre
92 % pour le HMM).

## 3. Filtre particulaire avec lissage (`seatrace/particules.py`)

Espace **continu** : état (x, y, vx, vy), vitesse autorégressive (Ornstein-Uhlenbeck discret, persistance 0,9),
vitesse maximale du type, terres infranchissables, courant optionnel.
- **Guidage vers l'observation de fin** : un filtre naïf ne « verrait » la fin qu'au dernier pas et
  dégénérerait. Chaque particule est pondérée par une fonction de torsion ψ (loi gaussienne approchée de la
  position finale sachant l'état courant, calculée exactement sans contraintes puis gonflée). Les poids
  incrémentaux ψ_k/ψ_{k-1} corrigent exactement ce guidage ; la portée maximale élimine les particules qui ne
  peuvent plus arriver.
- **Lissage FFBSi** (simulation arrière, Godsill, Doucet & West 2004) : pour chaque chemin, on remonte le temps
  en choisissant la particule précédente selon son poids filtré (non tordu) × densité de transition. Les
  chemins lissés sont des tirages de la loi a posteriori du trajet complet.
- Diagnostics publiés : taille effective minimale et finale, nombre de rééchantillonnages.

## 4. Pont brownien (`seatrace/pont_brownien.py`)

Modèle classique de l'écologie du mouvement (Horne et al. 2007) : position ~ N(interpolation linéaire,
T·α(1−α)·σ_m² + erreurs des bornes). La variance de mobilité σ_m² est estimée par **maximum de vraisemblance sur
les positions fiables du même trajet**, à l'échelle de temps de l'interruption (une mobilité mesurée sur 1 min
ne dit rien d'une absence d'1 h). Les chemins traversant une terre ou dépassant la vitesse maximale (avec marge)
sont rejetés ; le taux d'acceptation est publié. **Limites** : pas d'inertie ; si une île barre la ligne directe,
presque tous les ponts sont rejetés et le modèle déclare son échec (S6) ; il sous-estime l'incertitude
(couverture 48 % au lieu de 90 %).

## 5. Boosting stochastique (`seatrace/boosting.py`)

Ne simule pas de chemins : il **prédit directement** P(passage) à partir de descripteurs (durée, distance, portée
à vitesse max, marge de détour pour toucher la zone, part de la zone atteignable, distances des bornes à la zone,
caps déclarés orientés vers la zone…) et, par **empilement**, des probabilités du HMM et du pont brownien.
Gradient boosting **stochastique** (Friedman 2002 : chaque arbre voit une fraction des exemples) ; un ensemble de 7
graines donne une dispersion. Entraîné sur des interruptions à vérité connue des bateaux d'apprentissage et de
réglage (`data/modeles/boosting_entrainement.csv`), ré-entraîné au démarrage (aucun pickle chargé). La sortie
signale quand un descripteur sort du domaine d'entraînement. **Limite** : valable seulement dans le domaine de
ses exemples (ici synthétiques) ; il n'explique pas par où le bateau est passé.

**Réglage par Optuna** (`BO.regler`, `scripts/regler_boosting.py`, appelé par `eval/comparer_modeles.py`) : on
cherche `n_estimators`, `max_depth`, `learning_rate`, `subsample`, `min_samples_leaf` et `max_features` avec
l'échantillonneur TPE d'Optuna (40 essais par défaut). Le critère est le **score de Brier en validation croisée à
5 plis groupés par bateau** : un bateau n'est jamais à la fois dans l'apprentissage et la validation d'un pli, ce
qui évite de récompenser le sur-apprentissage d'un bateau particulier. Seuls les bateaux d'apprentissage et de
réglage servent ; les bateaux d'évaluation ne sont jamais vus, donc le score d'évaluation n'est pas gonflé par la
recherche. Le résultat (hyperparamètres, historique des essais, importance de chaque hyperparamètre) est écrit dans
`data/modeles/boosting_params.json` ; Optuna n'est pas nécessaire pour l'utiliser.

## Pourquoi le score de Brier et pas la log-loss

Les deux sont des règles de score *propres* et valables pour **tout** modèle qui sort une probabilité, pas
seulement pour un boosting (la log-loss est simplement le critère d'entraînement du boosting). Mais pour comparer
ici un HMM, un filtre à particules et un pont brownien sur ~160 interruptions, la log-loss est un mauvais outil :
- elle explose quand un modèle dit 0 % ou 100 % et se trompe, et il faut alors tronquer les probabilités
  (nous avions mis un plancher arbitraire de 0,1 %) : ce choix, pas le modèle, décide du classement ;
- quelques erreurs très confiantes dominent la moyenne, d'où une mesure bruyante sur un petit échantillon ;
- elle est non bornée et difficile à interpréter, alors que le Brier se lit directement (0 = parfait, 0,25 = répondre
  toujours 50 %) et se compare à des références (interpolation, taux de base).

La log-loss a donc été retirée des tableaux. Le jugement repose sur le Brier (avec intervalle de confiance bootstrap
apparié), la **calibration** (« 70 % » arrive-t-il environ 7 fois sur 10 ? ), la couverture de la région à 90 % et
l'erreur spatiale.

## Tous les chemins, avec leur probabilité

Chaque calcul est archivé dans `data/chemins/<identifiant>/` :
- `chemins.csv` : une ligne par point de chaque chemin (modèle, identifiant, famille, **poids**, log-proba,
  nombre de tirages, passage dans la zone, instant, latitude, longitude) ;
- `resume.json` : probabilités, intervalles, familles, diagnostics, hypothèses, versions, empreintes.

Signification du poids : pour le HMM et le modèle appris, les chemins identiques sur la grille sont fusionnés
et le poids est leur part des tirages (la log-proba est la probabilité a posteriori exacte du chemin d'états) ;
pour les particules et le pont, chaque chemin est un tirage équiprobable (poids 1/N) — la probabilité utile est
alors celle de la **famille**.

## Segmentation par ACP (`seatrace/segmentation.py`)

1. Chaque chemin est rééchantillonné en 24 instants intérieurs et exprimé dans le repère de la ligne directe
   début → fin (avancement, écart latéral) : la première composante s'interprète souvent comme « de quel côté ».
2. ACP pondérée sur les chemins de tous les modèles (chaque modèle pèse autant), composantes jusqu'à 90 % de
   variance (5 au plus).
3. Nombre de familles choisi par le **BIC d'un mélange gaussien** (un nuage continu reste une seule famille :
   on ne fabrique pas d'histoires artificielles), confirmé par la **silhouette** (≥ 0,3) ; affectation par
   k-moyennes pondérées ; une famille de moins de 2 % de la masse est refusée.
4. Pour chaque famille : description (« jusqu'à 2,8 km au sud de la ligne directe »), chemin moyen,
   probabilité **par modèle**, part des chemins qui traversent la zone.

**Interprétation des composantes** (règles fixes, `segmentation.interpret_components`) : le signe d'un axe de
l'ACP est arbitraire ; chaque composante est donc orientée pour que son sens principal soit positif, puis on
calcule sa corrélation (pondérée) avec quatre descripteurs simples d'un chemin : *côté* (écart latéral moyen par
rapport à la ligne directe, positif = à gauche en allant du début vers la fin), *détour* (écart latéral maximal),
*rythme* (avance ou retard sur une progression uniforme) et *irrégularité* (écart-type de l'écart latéral). La
composante est décrite par le descripteur le plus corrélé (« PC1 : de quel côté de la ligne droite passe le chemin,
r = 0,99 ; score élevé = passe à gauche, score bas = passe à droite »). L'interface affiche ce tableau, une phrase
sur la qualité de la séparation (silhouette) et la lecture du nuage de points. Les distances du plan ACP sont des
écarts de chemins en km, pas des probabilités : la probabilité d'une famille vient du poids de ses chemins.

Exemple S6 avec l'observation RF simulée (interruption de 74 min, île sur la ligne directe) : F1 « par le sud, à travers la zone » — HMM 90 %,
particules 99 %, appris 47 % ; F2 « par le nord » — HMM 10 %, particules 2 %, appris 53 %. La divergence du
modèle appris vient de sa densité de trafic historique (le nord est la route habituelle).

## Comparatif mesuré (159 interruptions, bateaux jamais vus)

| Modèle | Brier (IC 95 %) | Log-loss | Échecs | Erreur spatiale | Couverture 90 % | Temps médian |
|---|---|---|---|---|---|---|
| HMM à cap | 0,070 (0,045–0,098) | 0,21 | 0 | 2,2 km | 99 % | 0,41 s |
| Appris | 0,087 (0,053–0,122) | 0,28 | 0 | 2,0 km | 93 % | 0,41 s |
| Particules + FFBSi | 0,077 (0,045–0,112) | 0,29 | 0 | 1,8 km | 90 % | 0,12 s |
| Pont brownien | 0,094 (0,063–0,148) | 0,62 | 8 | 1,9 km | 48 % | 0,006 s |
| Boosting stochastique | 0,041 (0,018–0,069) | 0,16 | 0 | — | — | 0,003 s |

Références : interpolation linéaire 0,126 ; taux de base 0,173. Le boosting est significativement meilleur sur
cet échantillon (IC de l'écart au meilleur > 0 pour les autres), mais ses exemples viennent de la même baie
fictive et son descripteur dominant est la probabilité du HMM (importance 0,62). Les écarts entre HMM,
particules et modèle appris ne sont pas significatifs (l'ordre change entre la version rapide et complète).

## Choisir un modèle ? Non : les lire ensemble

| Situation | Ce qu'il faut regarder |
|---|---|
| Tous les modèles ≥ 90 % ou ≤ 10 % | Lecture robuste aux hypothèses (ce n'est toujours pas une preuve) |
| Les modèles divergent | Regarder les familles : quelle histoire chaque modèle privilégie, et pourquoi (habitudes, inertie, obstacle) |
| Le pont échoue | Une terre barre la ligne directe : seuls les modèles à inertie ou sur grille sont pertinents |
| Le boosting signale « hors domaine » | Ignorer son chiffre ; il n'a pas vu de situation comparable |
| Le modèle appris s'écarte seul | Le trajet s'écarte des habitudes de l'historique ; ce n'est pas en soi suspect |
