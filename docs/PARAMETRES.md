# Paramètres essentiels et justification

Tous les paramètres sont dans `config/parametres.json`, validés par `seatrace/config.py` (bornes strictes).
Toute modification depuis l'application est journalisée, et l'empreinte des paramètres figure dans chaque rapport.
**Aucun seuil n'est universel** : les vitesses dépendent du type de bateau.

## Types de bateau (location touristique)

| Type | Vitesse max (nœuds) | Vitesse typique (nœuds) | Origine |
|---|---|---|---|
| Voilier de location | 9 | 5 | Hypothèse de prototype (ordre de grandeur d'un voilier de croisière de location) |
| Semi-rigide de location | 35 | 18 | Hypothèse de prototype |
| Vedette de location | 25 | 14 | Hypothèse de prototype |

À remplacer, lors d'un pilote, par les caractéristiques réelles de chaque bateau (fiche constructeur, motorisation).

## Détection

| Paramètre | Valeur | Rôle |
|---|---|---|
| `marge_vitesse` | 1,25 | Saut signalé si la vitesse calculée dépasse 1,25 × la vitesse max, **après** déduction de l'incertitude de position |
| `ecart_vitesse_min_kn` / `ecart_vitesse_relatif` | 3 nd / 35 % | Écart toléré entre vitesse déclarée et vitesse calculée |
| `ecart_vitesse_points_consecutifs` | 3 | Nombre d'intervalles consécutifs incohérents avant alerte (évite les fausses alertes ponctuelles) |
| Écart de cap | 45° (si vitesse > 3 nd) | Cap déclaré incompatible avec le déplacement |
| Dérive cumulée | 0,3 km + 15 % de la distance, fenêtre de 10 points | Décalage progressif entre positions et estime (vitesse × cap) |
| `facteur_interruption` | 5 × l'intervalle attendu (60 s) | Interruption signalée (disponibilité, pas intégrité) |
| `dedoublement_fenetre_s` / `distance_m` | 30 s / 500 m | Même identifiant à deux endroits incompatibles |
| `rejeu_longueur_fenetre` / `tolerance_m` | 6 déplacements / 2 m | Suite de déplacements identique à une suite antérieure (des mesures indépendantes ne se répètent pas au mètre près) |
| `rejeu_texture_min_ms` | 0,2 m/s | Une fenêtre n'est comparée que si la VITESSE de ses pas varie d'au moins cette valeur : une ligne droite à vitesse constante ne porte pas d'empreinte, même avec un rythme d'émission irrégulier (sur de vrais chalutiers AIS, de tels tronçons se ressemblent au mètre près et déclenchaient de fausses alertes) |

Ces seuils ont été fixés a priori (physique + scénarios de démonstration), **pas** réglés sur le jeu
d'évaluation. Ils restent à recalibrer sur des données réelles de traceurs (bruit réel, fréquence réelle).

## Modèle de reconstruction

| Paramètre | Valeur | Origine |
|---|---|---|
| `taille_case_km` | 0,2 | Compromis précision / temps de calcul |
| `pas_temps_s` | 120 (au plus 90 pas) | Compromis précision / temps de calcul |
| `nb_caps` | 8 | Directions discrètes du cap |
| `persistance_cap` | 0,7 | **Réglé** sur le jeu de réglage synthétique (score de Brier) |
| `dispersion_relative` | 1,5 (écart-type de vitesse = 1,5 × vitesse typique, tronqué à la vitesse max) | **Réglé** sur le jeu de réglage synthétique. Le réglage favorise une loi de vitesse très large, presque uniforme ; la valeur retenue est en bord de grille et les écarts de score entre 0,7 et 1,5 sont faibles (voir `eval/resultats/metriques.json`) |
| `part_arret` | 0,1 | Probabilité d'être à l'arrêt ou très lent pendant un pas (mouillage) |
| `incertitude_bornes_m` | 50 | Incertitude ajoutée à la précision déclarée des positions encadrantes |
| `duree_max_interruption_h` | 6 | Au-delà : « estimation insuffisamment étayée » |
| `modeles.courant_kmh` / `modeles.vent_kmh` | 0 / 0 (remplacés par calcul) | Courant de surface et vent à 10 m (km/h, est et nord, vers où ils vont). Pour les jeux du Kattegat : moyennes sur la zone atteignable et la durée du trou (± 30 min) des tables exportées par `src/13_seatrace_export.py --context-only` — mêmes sources que la carte : vent réel Open-Meteo, courant Copernicus s'il est téléchargé, sinon **simulé** (et étiqueté comme tel) |
| `modeles.derive_vent` | 0,03 | **Hypothèse** : part de la vitesse du vent qui pousse le bateau sous le vent (glissement du bateau par rapport à l'eau). Dérive du modèle = courant + 0,03 × vent. Le courant d'un modèle océanique contient déjà l'entraînement de l'eau par le vent : pas de double compte. La sensibilité du HMM montre le résultat sans cette part et avec la part doublée |
| `poids_habitudes` | 0 (désactivé) | Un a priori « habitudes de trafic » a été testé ; il **dégrade** le score et pénalise mécaniquement les zones que les bateaux évitent habituellement — dont les zones réglementées. Désactivé. |

## Qualification de l'estimation

| Statut | Critère |
|---|---|
| Étayée | Résultat stable sous les hypothèses testées (étendue ≤ 25 points) |
| Fragile | Étendue > 25 points, ou bornes proches de données suspectes, ou interruption > 2 h |
| Insuffisamment étayée | Étendue > 50 points, ou trajet impossible sous une hypothèse raisonnable, ou durée > 6 h, ou observations incompatibles |

Lecture fixe de la probabilité : ≥ 90 % « passage très probable sous les hypothèses » ; ≤ 10 % « passage peu
probable » ; entre les deux « plusieurs chemins restent plausibles : l'outil ne permet pas de conclure ».
