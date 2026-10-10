# Fiche de défense — SeaTrace (Open Track, Secure Horizons 2026)

**Problème.** Après une sortie, un assureur ou une autorité doit savoir où une petite embarcation est réellement
passée. Or les positions de traceur peuvent manquer (couverture, batterie) ou être altérées (modification,
injection, rejeu, suppression), et le signal satellite peut être brouillé ou leurré.
**Actif protégé :** l'intégrité des données de navigation utilisées par une plateforme maritime.

## Scénario de menace (simulé uniquement dans notre environnement, sur des copies locales)
Un acteur ayant accès au flux ou à la base (prestataire, utilisateur interne, compte compromis) **modifie,
injecte, rejoue ou supprime** des positions pour masquer un passage dans une zone interdite à la navigation,
avant qu'un dossier soit analysé. Aucune action contre un système réel.

## Fonctions NIST CSF 2.0
- **Detect** : contrôles explicables de cohérence (vitesse impossible, vitesse/cap déclarés contredits par le
  déplacement, dérive cumulée, identité dédoublée, rejeu, doublons) ; interruptions signalées comme
  problème de *disponibilité*, jamais comme attaque.
- **Respond** : isolation des positions suspectes (conservées, mais exclues de l'analyse), reconstruction
  probabiliste de la période, estimation de passage dans une zone avec hypothèses et sensibilité, rapport
  d'investigation, décision humaine journalisée. Aucune décision automatique.

## Correspondances ATT&CK (à vérifier sur attack.mitre.org avant soumission)
- Enterprise **T1565.001 Stored Data Manipulation** et **T1565.002 Transmitted Data Manipulation** :
  altération des positions stockées ou transmises.
- ICS **T0856 Spoof Reporting Message** (télémesure falsifiée) : un miroir tiers l'indique remplacé par
  **T1692.002 Reporting Message** dans la version récente — identifiant à confirmer. La stratégie de détection
  MITRE associée recommande de comparer aux valeurs attendues et à des données indépendantes du processus :
  c'est ce que font nos contrôles (physique du mouvement, vitesse/cap déclarés, observations externes).
- Pas de correspondance OWASP Agentic ni ATLAS revendiquée : SeaTrace n'est pas un agent et n'utilise pas de LLM.

## Métriques obtenues (données **synthétiques** — pas une validation opérationnelle)
Jeux séparés par bateau : 12 bateaux d'apprentissage, 6 de réglage, 12 d'évaluation (48 trajets, dont ~23 % « hors habitudes »).

| Détection (trajets jamais vus) | Valeur |
|---|---|
| Rappel, toutes manipulations sauf suppression | **96 %** (saut 100 %, identité dédoublée 100 %, dérive 98 %, rejeu 98 %, remplacement 85 %) |
| Précision des alertes d'intégrité | **97 %** |
| Fausses alertes sur trajets propres | 0,17 par trajet ; **85 %** des trajets propres sans aucune alerte |
| Suppression de positions | signalée comme interruption dans 98 % des cas (non distinguable d'une perte de réception — attendu) |
| Temps d'analyse d'un trajet (après import) | ~17 ms (médiane) ; analyse **post-sortie**, pas de détection en temps réel |

| Passage dans la zone (158 interruptions, échantillon enrichi en passages) | Score de Brier (↓) |
|---|---|
| **Modèle à cap persistant (réglé)** | **0,051** |
| Variante sans cap (marche aléatoire) | 0,081 |
| Interpolation linéaire | 0,089 |
| Taux de base | 0,163 |
| Zone atteignable (oui/non) | 0,665 |

Reconstruction : erreur de la position moyenne estimée **1,2 km** (interpolation : 1,8 km) ; la position réelle
est dans la région à 90 % de probabilité **97 %** du temps (prudent). Calcul : 0,24 s médian par interruption.
Calibration : bonne aux extrêmes ; dans les cas intermédiaires (10–70 %) le modèle **sous-estime** le passage,
sur de petits effectifs (15, 11, 8 cas). Erreur plus forte pour les interruptions de 30 à 90 min.

## Comment l'humain garde le contrôle
Scores séparés (anomalie des données, qualité, probabilité de passage, écart au trajet prévu) — jamais de
score de culpabilité ; chaque alerte affiche les valeurs calculées qui la justifient et des explications
alternatives ; statut « étayée / fragile / insuffisamment étayée » ; décision et note de l'analyste
journalisées ; paramètres versionnés et changements journalisés.

## Limites connues
Données et géographie synthétiques ; modèle non calibré sur données réelles ; un attaquant qui modifie
positions **et** vitesse/cap de façon cohérente échappe aux règles (seules des sources indépendantes le
contrediraient) ; isolation prudente qui peut écarter des positions authentiques ; pas de météo ni de
courants ; zone évaluée à la résolution de 200 m ; journal chaîné mais pas inviolable ; prototype local sans
authentification.
