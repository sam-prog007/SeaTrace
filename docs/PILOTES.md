# Go-to-market : concurrence, offre et propositions de pilote

> Tout ce qui concerne prix, coûts et volumes est une **hypothèse à valider** en entretien. Aucun contact,
> achat ou déploiement n'a été fait.

## 1. Paysage concurrentiel (vérifié le 10/10/2026, sources en bas de page)

| Acteur | Ce qu'il fait (d'après ses pages publiques) | Lien avec SeaTrace |
|---|---|---|
| **Argotrak** | Plateforme de suivi GPS spécialisée dans les bateaux de location et les clubs : alertes de géorepérage et d'usage non autorisé, historique ; formule de base annoncée à partir de 15 $ par utilisateur et par mois (Capterra) | Concurrent direct possible sur la surveillance ; partenaire possible (source de données) |
| **Docklyne** | Logiciel pour loueurs de bateaux : suivi en temps réel, historique de position pendant la location, appel du client depuis la carte | Idem : fournisseur de données et canal de distribution potentiel |
| **Siren Marine** | Surveillance de bateaux (batterie, cale, GPS, géorepérage) | Cible plutôt les propriétaires ; pas d'analyse d'intégrité mise en avant sur les pages consultées |
| **Windward** | Plateforme de renseignement maritime ; offre dédiée aux assureurs (souscription, sinistres, vérification de l'activité quand l'AIS est incomplet, manipulé ou contesté) | Couvre déjà la détection de manipulation AIS/GNSS pour les assureurs maritimes |
| **Kpler** (avec MarineTraffic et Spire Maritime) | Données et analyses maritimes, signaux de risque, AIS satellitaire | Contrôle une part importante des données AIS ; acteur majeur du risque et de la conformité |
| **Unseenlabs** | Géolocalisation de navires par radiofréquences depuis l'espace, y compris navires « sombres » ; clients annoncés : agences maritimes, gouvernements, assureurs, pêche, ONG | Source d'observations indépendantes envisagée (partenariat à obtenir) |

Ce que nous **n'affirmons pas** : que les grands acteurs ignorent les petits navires ou la pêche, ni que notre
combinaison est unique (non vérifié). **Notre hypothèse de différenciation, à tester** : une analyse
post-sortie, branchée sur les traceurs que les loueurs possèdent déjà, qui (1) contrôle l'intégrité des
données, (2) reconstruit les portions manquantes ou suspectes avec une incertitude explicite, (3) produit un
rapport explicable pour un assureur ou une autorité. Les fournisseurs de traceurs pourraient construire cette
brique eux-mêmes : c'est le principal risque concurrentiel, et aussi la piste de partenariat la plus naturelle.

## 2. Offre annuelle (hypothèse)

Abonnement par bateau suivi comprenant : analyse des trajets après chaque sortie ; surveillance du
fonctionnement de la collecte (traceurs muets, horloges décalées) ; maintenance logicielle et mises à jour de
sécurité ; suivi des performances du détecteur (fausses alertes, dérives) ; assistance et traitement des
faux positifs ; conservation des rapports pendant une durée définie.
**Non inclus** : fourniture, installation et entretien physique des traceurs.
**Aucune** disponibilité garantie ni performance commerciale annoncée tant qu'elles ne sont pas mesurées.

Hypothèses de prix à tester (pas des tarifs) : un montant mensuel par bateau, à situer par rapport au coût du
traceur et de son abonnement déjà payé par le loueur ; une option « rapport d'investigation » à l'acte pour
l'assureur. Coûts à estimer : hébergement, assistance, et coût d'accès aux données externes — **non chiffré**,
faute de tarifs publics fiables.

## 3. Pilote A — Gestionnaire de flotte de location + son assureur

| Élément | Contenu |
|---|---|
| Utilisateurs | Gestionnaire de flotte (exploitation), chargé de sinistres de l'assureur |
| Acheteur / décideur à identifier | Directeur d'exploitation du loueur ; responsable indemnisation ou souscription chez l'assureur (qui paie ? à valider) |
| Problème précis | Après un incident (avarie, échouement, sortie de zone de navigation autorisée), données de traceur incomplètes ou contestées ; reconstitution manuelle longue |
| Données nécessaires | Exports des traceurs de 5 à 15 bateaux sur une saison ; caractéristiques des bateaux ; zones de navigation contractuelles ; journaux techniques des traceurs si disponibles |
| Résultat livré | Rapport par sortie (qualité, anomalies, reconstruction, probabilité de passage, hypothèses) ; tableau mensuel de santé de la collecte |
| Critères de succès | Temps d'analyse d'un dossier réduit (mesuré avant / après) ; taux de faux positifs jugés inutiles par l'analyste ; nombre de dossiers où le rapport a modifié la suite de l'investigation ; zéro incident de sécurité |
| Confidentialité / intégration | Trajets rattachables à des locataires : base légale, information, minimisation, durée de conservation ; transfert sécurisé ; séparation entre clients ; export depuis le logiciel du traceur (CSV) avant toute intégration directe |
| Questions à valider | Qui paie ? Quelle fréquence d'incidents ? Les traceurs fournissent-ils vitesse et cap ? Quelle valeur probante attend l'assureur ? |

## 4. Pilote B — Autorité publique (surveillance d'une zone réglementée)

| Élément | Contenu |
|---|---|
| Utilisateurs | Agents chargés du contrôle d'une zone (aire protégée, zone interdite à certaines activités) |
| Acheteur / décideur à identifier | Service de l'État ou gestionnaire d'aire protégée ; marché public ou expérimentation |
| Problème précis | Prioriser les contrôles : quels trajets présentent des données incohérentes ou des interruptions compatibles avec un passage dans la zone ? |
| Données nécessaires | Limites officielles de la zone (source, version, dates d'application, activités et types de bateaux concernés, exceptions) ; données de traceurs ou d'AIS dont l'autorité dispose légalement |
| Résultat livré | Liste priorisée de dossiers à examiner, chacun avec rapport explicable ; aucune décision automatique |
| Critères de succès | Part des contrôles déclenchés jugés pertinents par les agents ; temps de préparation d'un contrôle ; lisibilité des rapports par un juriste |
| Contraintes | Cadre juridique de la surveillance, proportionnalité, hébergement souverain probable, traçabilité des décisions humaines ; traverser une zone ne prouve pas une activité interdite |
| Questions à valider | Quelles données l'autorité peut-elle légalement partager ? Quel format de preuve est exploitable ? Quel budget et quel calendrier d'achat ? |

## 5. Cinq questions d'entretien client

1. « Racontez-moi le dernier dossier où vous avez dû reconstituer où était un bateau. Combien de temps cela a-t-il pris et qu'est-ce qui manquait ? »
2. « À quelle fréquence vos données de traceur sont-elles incomplètes, incohérentes ou contestées ? Qu'en faites-vous aujourd'hui ? »
3. « Si un rapport vous disait "passage dans la zone : 60 %, sous ces hypothèses", qu'en feriez-vous concrètement ? Quelle forme de résultat serait utilisable ? »
4. « Qui décide d'acheter un outil de ce type chez vous, et sur quel budget (exploitation, sinistres, conformité) ? »
5. « Quelles données pourriez-vous nous transmettre pour un pilote, avec quelles autorisations, et qu'est-ce qui vous en empêcherait ? »

## Sources

- Argotrak — Capterra : https://www.capterra.com/p/203590/Argotrak/
- Docklyne — suivi GPS de flotte : https://docklyne.com/boat-rental-software/gps-fleet-tracking
- Siren 3 — Boating Magazine (contenu sponsorisé) : https://boatingmag.com/sponsored-post/how-siren-3-is-changing-the-game-for-boat-owners/
- Windward — assurance maritime : https://windward.ai/industries/marine-insurance/
- Kpler / Spire Maritime : https://www.ship-technology.com/newsletters/kpler-acquisition-spire-maritime
- Unseenlabs : https://startup-seeker.com/company/unseenlabs~com
