# Pitch — 8 slides (5 min) · version courte 3 min pour la finale

## 1. Le problème
**« Après un incident, où était vraiment ce bateau ? »**
Un loueur, son assureur ou une autorité doit répondre à partir de positions de traceur parfois manquantes,
parfois altérées, parfois faussées par un brouillage du signal satellite. Aujourd'hui : reconstitution à la
main, sans incertitude explicite.
Phrase-problème : *SeaTrace aide les assureurs maritimes et les autorités à juger la fiabilité des trajets de
petites embarcations, et à estimer où un bateau a pu passer quand les données manquent ou ont été altérées.*

## 2. Pourquoi ce problème (sources vérifiées)
- Janvier 2026 : 14 États côtiers de la Baltique et de la mer du Nord (dont la France) demandent de reconnaître
  le brouillage GNSS et la manipulation de l'AIS comme des menaces pour la sécurité maritime. [1]
- 2e trimestre 2026 : Windward a recensé **171 286 navires** affectés au moins une fois par un brouillage GPS ;
  les historiques de position des zones touchées contiennent positions injectées, périodes manquantes et faux
  événements. [2]
- Juin 2026 : *Yachting Monthly* alerte les plaisanciers sur le brouillage et le leurrage en Baltique, en
  Méditerranée orientale et en mer Noire ; une enquête du Royal Institute of Navigation (navires de commerce)
  rapporte que plus de 10 % des répondants ont été conduits dans des situations dangereuses ou illicites. [3]
- Les assureurs maritimes intègrent déjà la manipulation de position dans la souscription et la gestion des
  sinistres des grands navires [2][4] ; les flottes de location, elles, s'appuient sur des traceurs sans
  contrôle d'intégrité mis en avant par les offres consultées [5][6].
- *Témoignage client : à obtenir pendant le hackathon (voir questions d'entretien, `docs/PILOTES.md`).*

## 3. Pour qui
Premier segment : **flottes de bateaux de location touristique équipées de traceurs, et leurs assureurs**.
Ensuite : autorités chargées de zones réglementées ; extension aux bateaux de pêche (feuille de route).

## 4. La solution
Importer → **Détecter** les incohérences (explicables) → **Isoler** les positions suspectes → **Reconstruire**
les trajets compatibles (modèle de Markov caché à cap persistant, conditionné avant ET après l'interruption)
→ **Estimer** la probabilité de passage dans une zone, avec hypothèses et sensibilité → **Rapport** →
**l'humain décide**. Traitement local, imports sécurisés, journal chaîné.

## 5. Démo (voir `docs/VIDEO.md`)
Trajet falsifié en laboratoire détecté et isolé ; interruption compatible avec un passage (90 %) ; cas où
l'outil **ne conclut pas** (35 %) ; croisement avec une observation RF simulée (85 % → 92 %).

## 6. Preuves (données synthétiques — pas une validation opérationnelle)
Rappel 96 % · précision 97 % · 85 % des trajets propres sans alerte · Brier 0,051 contre 0,089 pour
l'interpolation · région à 90 % couvrant la position réelle 97 % du temps. Limites affichées : sous-estimation
dans les cas intermédiaires, attaquant cohérent sur positions + vitesse/cap.

## 7. Modèle économique et concurrence
Abonnement annuel par bateau (analyse post-sortie, surveillance de la collecte, maintenance et mises à jour
de sécurité, suivi des performances, traitement des faux positifs, conservation des rapports) ; rapport
d'investigation à l'acte pour l'assureur. **Prix : hypothèses à tester en entretien.**
Concurrence : traceurs de location (Argotrak, Docklyne), surveillance de bateaux (Siren Marine), renseignement
maritime (Windward, Kpler, Unseenlabs). Notre pari : la couche d'**intégrité et de reconstruction
probabiliste** au-dessus des traceurs existants — que ces fournisseurs pourraient aussi construire (risque et
piste de partenariat).

## 8. Et ensuite (6 mois) + équipe
- **M1–M2** : 15 entretiens (loueurs, assureurs plaisance, une autorité) ; un pilote signé.
- **M2–M4** : pilote sur une saison de données réelles d'un loueur (5–15 bateaux) ; recalibrage des seuils ;
  mesure du temps gagné et des faux positifs.
- **M4–M6** : intégration directe avec un fournisseur de traceurs ; essai d'observations externes
  (accès à négocier) ; authentification, isolation entre clients, hébergement sécurisé.
- **Besoins** : un loueur et un assureur pilotes, accès à des données réelles avec base légale, un partenariat
  données, financement d'amorçage.
- Équipe : 4 personnes (données, modèle, application et sécurité, produit).

## Sources
1. Pole Star — 14 European States Warn of Russian GPS Interference : https://www.polestarglobal.com/resources/14-states-russian-maritime-gps-interference/
2. Windward via Hellenic Shipping News — What Marine Underwriters Are Now Pricing Against : https://www.hellenicshippingnews.com/what-marine-underwriters-are-now-pricing-against/
3. Yachting Monthly (30/06/2026) — Navigating blind: the urgent threat of GPS spoofing to sailors : https://www.yachtingmonthly.com/cruising/navigating-blind-the-urgent-threat-of-gps-spoofing-to-sailors-105646
4. Windward — Marine insurance : https://windward.ai/industries/marine-insurance/
5. Argotrak — Capterra : https://www.capterra.com/p/203590/Argotrak/
6. Docklyne — GPS fleet tracking : https://docklyne.com/boat-rental-software/gps-fleet-tracking
