# Stratégie de données et croisement des sources

> État au 10/10/2026. **Aucune donnée réelle n'est utilisée dans le prototype.** Toutes les données de
> démonstration et d'évaluation sont synthétiques et marquées comme telles (`synthetic = true`).

## 1. Ce que le prototype utilise réellement

| Source | Statut | Remarque |
|---|---|---|
| Positions de traceurs (CSV) | **Réellement intégrée** (import local) | Format en `docs/EQUIPE.md`. Fichier non fiable par défaut : validé, borné, conservé sous un nom interne. |
| Trajets, bateaux, géographie, zone Z1 | **Synthétiques** | Géographie entièrement fictive, au large, dans un repère local. Zone Z1 décrite comme « interdite à la navigation pour le bateau et la période du scénario ». |
| Observations radiofréquences | **Simulées** (« observations RF simulées ») | Aucune intégration ni partenariat avec un fournisseur. Pas de nom ni de logo de fournisseur dans l'interface. |

## 2. Sources publiques ou ouvertes envisagées (non intégrées)

| Source | Fournisseur | Licence / droits | Granularité | Limites | Attribution |
|---|---|---|---|---|---|
| Limites maritimes (ZEE, eaux territoriales) | Flanders Marine Institute — MarineRegions.org | **CC-BY** depuis la version 11 (2019). La page précise que la base n'est pas destinée à un usage juridique ni à la navigation, et demande de contacter l'éditeur pour les usages hors licence ; elle demande aussi de ne pas redistribuer les produits en téléchargement. | Polygones mondiaux, versions datées | Pas de réglementation d'activité (pêche, mouillage…) | Citation demandée : « Flanders Marine Institute (année) : MarineRegions.org » + notification à l'éditeur en cas d'usage dans un produit |
| Aires protégées | UNEP-WCMC — Protected Planet (WDPA) | **Usage commercial interdit sans autorisation écrite** de UNEP-WCMC ; redistribution et sous-licence interdites. Les usages commerciaux sont orientés vers IBAT. | Polygones, mise à jour mensuelle | Limites réglementaires précises non garanties | Citation obligatoire |
| Trait de côte | OpenStreetMap | ODbL (partage à l'identique des bases dérivées) — *à confirmer pour notre usage précis* | Fin | Qualité variable selon les zones | « © contributeurs OpenStreetMap » |
| Historiques AIS publics | Autorités maritimes (ex. Danemark), NOAA | **À vérifier** source par source avant tout usage commercial | Variable | Les petites embarcations de location n'émettent souvent pas d'AIS | Selon la source |

Règle : une carte consultable en ligne n'autorise pas l'extraction ni la réutilisation de ses données. Pour
chaque source, on documente fournisseur, licence, droits commerciaux, granularité, fraîcheur, couverture et
attribution **avant** de l'intégrer.

## 3. Observations radiofréquences d'un fournisseur spécialisé (prévu)

- Accès conditionné à un échantillon, un accord ou un partenariat avec droits d'usage : **ni gratuit, ni ouvert**.
- À vérifier avant intégration : couverture (zones, revisites), horodatage, précision de localisation,
  informations permettant de rapprocher un émetteur d'un bateau.
- Dans le modèle, une détection n'est **jamais attribuée automatiquement** à un bateau : elle entre avec une
  probabilité d'attribution `p` (mélange « concerne ce bateau » / « n'apporte aucune information »).

## 4. Données d'un pilote avec un assureur et un gestionnaire de flotte

On ne suppose pas que l'assureur possède les historiques : ils viennent en général du **gestionnaire de flotte**
(traceurs) ou de l'assuré. À définir ensemble, en se limitant au nécessaire :

- identifiant du bateau et caractéristiques utiles (type, vitesse maximale) ;
- exports des traceurs (positions, horodatages, précision, vitesse et cap s'ils existent) ;
- journaux techniques du traceur (alimentation, réception), s'ils existent ;
- trajet déclaré ou zones convenues dans le contrat de location, si pertinent.

Exigences : transfert sécurisé, séparation stricte entre clients, accès autorisés, durée de conservation
définie, base légale et information des personnes au titre du RGPD (les trajets d'un bateau loué peuvent être
rattachés à un locataire). **Aucun contact ni collecte réelle sans instruction explicite de l'équipe.**

## 5. Croisement des sources dans le modèle

- Chaque observation garde : source, horodatage, précision, statut (`observee`, `declaree`, `simulee`, `estimee`).
- Les observations externes ajustent la probabilité des trajets compatibles ; elles ne sont pas une vérité absolue.
- **Dépendances** : deux services qui reprennent le même message AIS ne sont pas deux confirmations
  indépendantes ; un traceur et un AIS peuvent partager le même récepteur satellite. Dans le prototype, une
  seule source de positions est utilisée ; l'ajout d'une source dépendante devra être déclaré comme tel
  (et non multiplié comme une preuve indépendante).
- Une **absence d'observation** ne prouve pas l'absence du bateau tant que la couverture de la source n'est
  pas connue : le prototype n'utilise jamais une absence comme information négative.
- Une contradiction entre sources est affichée (une observation incompatible rend la reconstruction
  « insuffisamment étayée »), elle n'est pas lissée.
