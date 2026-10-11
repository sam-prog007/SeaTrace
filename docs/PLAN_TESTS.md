# Plan de test et de correction — V1 → V2 (4 personnes)

**Objectif :** samedi 23h, tout ce qui est montré au jury est testé, et chaque chiffre cité correspond à
`eval/resultats/metriques.json`. Gel des fonctionnalités dimanche 8h.

## Règles communes

- Une branche par personne : `test/p1-donnees`, `test/p2-modele`, `test/p3-app`, `test/p4-produit`.
- Avant chaque fusion dans `main` : `python -m pytest` vert. Chaque bug corrigé = un test qui le prouve.
- On note chaque bug trouvé dans `docs/BUGS.md` (qui, quoi, comment reproduire, corrigé oui/non).
- Si un paramètre ou une règle change → **P2 relance** `python eval/run_eval.py` et **P4 met à jour** les chiffres
  des documents (README, DEFENSE, PITCH, VIDEO).
- Ne jamais lire `data/etiquettes/` depuis l'application ni la détection.
- Points de synchronisation : 17h, 19h, 21h, 23h (10 min, debout).

---

## P1 — Données et scénarios (`scenarios.py`, `geo.py`, `scripts/generer_donnees.py`)

**À tester**
1. Régénérer la démo deux fois : les fichiers doivent être identiques (même graine → même empreinte).
2. Vérifier sur une carte que **aucun** trajet vrai ne passe sur la terre (flotte complète, 120 trajets).
3. Créer `data/tests/` avec des CSV de cas limites, utilisés par P3 et P2 :
   - trajet de 5 points (trop court) ; trajet de 2 000 points ;
   - deux bateaux et plusieurs trajets dans un même fichier ;
   - positions sans `sog_kn` ni `cog_deg` ;
   - horodatages en double, désordonnés, ou à cheval sur minuit UTC ;
   - trajet hors de la baie de démo (géographie inconnue) ;
   - interruption au tout début ou à la toute fin du trajet ;
   - CSV avec séparateur `;`, en-têtes en majuscules, BOM, fins de ligne Windows.
4. Ajouter des manipulations plus difficiles au laboratoire : **positions + vitesse/cap falsifiées de façon
   cohérente**, dérive très lente (< 0,5 km), rejeu avec léger bruit ajouté.

**Bugs ou faiblesses connus à examiner**
- Le trajet S5 tombe pile à 90 % (seuil de lecture « très probable ») : rendre le scénario moins limite.
- Les scénarios de démo dépendent de la graine : documenter quelle graine produit quoi.

**Terminé quand :** les CSV de cas limites existent, chacun avec une ligne dans `docs/BUGS.md` décrivant le
comportement attendu, et les nouvelles manipulations sont branchées dans `eval/run_eval.py`.

---

## P2 — Détection et modèle (`detect.py`, `reconstruct.py`, `eval/run_eval.py`, `config/parametres.json`)

**À tester**
1. **Fausses alertes** (0,17 par trajet propre) : lister les règles qui se déclenchent sur les trajets propres
   (mouillage ? virages ? vitesse lente avec cap bruité ?) et corriger la plus fréquente.
2. **Remplacement de segment** (rappel 85 %) : étudier les cas manqués et comprendre pourquoi.
3. **Isolation trop large** : sur S4, 31 points sont écartés alors que la falsification n'en touche qu'une
   vingtaine. Essayer de resserrer la limite de la dérive cumulée sans perdre de détection.
4. **Calibration** : le modèle sous-estime le passage dans les cas intermédiaires. Étendre la grille de réglage
   (dispersion > 1,5, persistance 0,6–0,8), augmenter le nombre d'interruptions, regarder si c'est stable.
5. Cas limites du modèle : interruption d'un seul pas, départ sur une case de terre (côte), géographie inconnue
   (grille générique), observation externe hors intervalle, deux observations RF au même pas.
6. Vérifier que la proportion Monte Carlo reste cohérente avec la probabilité exacte (1 000 tirages, 5 cas).
7. Mesurer le temps de l'analyse de sensibilité dans le pire cas (6 h, semi-rigide) : doit rester < 15 s.

**Bugs ou faiblesses connus à examiner**
- Les paramètres retenus sont en bord de grille de réglage.
- Erreur plus forte pour les interruptions de 30 à 90 min.
- Un attaquant qui falsifie positions **et** vitesse/cap de façon cohérente passe sous les règles : le
  mesurer avec les nouvelles manipulations de P1 et l'écrire honnêtement.

**Terminé quand :** `eval/run_eval.py` relancé avec les corrections, `metriques.json` à jour, chaque
changement de paramètre justifié dans `docs/PARAMETRES.md`.

---

## P3 — Application et sécurité (`web/app.py`, `schema.py`, `audit.py`, `pipeline.py`, `report.py`)

**À tester (à la main, dans le navigateur)**
1. Le parcours complet 1 → 10 sur les 7 scénarios, puis avec chaque CSV de cas limites de P1.
2. Import **par l'interface** de fichiers malveillants : binaire renommé en `.csv`, fichier de 6 Mo, ligne de
   10 000 caractères, `=HYPERLINK(...)`, `<script>` dans `boat_id`, nom de fichier `../../x.csv`.
   Attendu : refus clair, rien de cassé, entrée « import_refuse » dans le journal.
3. Changement de bateau, de trajet ou de paramètres : aucun résultat ancien ne doit rester affiché
   (carte, probabilité, décision).
4. Export : ouvrir le rapport HTML dans le navigateur (aucun script, aucune requête externe), ouvrir le CSV dans
   Excel ou LibreOffice (aucune formule exécutée), vérifier l'empreinte du fichier dans le rapport.
5. Journal : modifier une ligne de `data/journal/journal.jsonl` à la main → l'onglet Journal doit afficher l'erreur.
6. Débrancher le wifi : l'application doit tout faire sans réseau.
7. `pip install pip-audit && pip-audit -r requirements.txt` : noter les vulnérabilités des dépendances.

**Bugs ou faiblesses connus à examiner**
- Le chargement par l'interface (`file_uploader`) n'est couvert par aucun test automatique.
- Les données hors de la baie de démo n'ont pas été essayées dans l'interface.
- Les trajets reconstruits dessinés sont très zigzagants : envisager de les lisser à l'affichage (sans
  changer le calcul), avec la mention « trajets estimés ».

**Terminé quand :** chaque point ci-dessus est coché dans `docs/BUGS.md`, les bugs bloquants sont corrigés,
et un test automatique couvre chaque correction de sécurité.

---

## P4 — Validation produit, rapport et pitch (`report.py`, `docs/`, deck, vidéo)

**À tester**
1. Dérouler la démo de `docs/VIDEO.md` en chronométrant : elle doit tenir en 2 min 30, sans surprise.
2. Relire un rapport HTML exporté comme si on était un assureur : tout est-il compréhensible sans nous ?
   Les mentions « synthétique », « estimé », « pas une probabilité de fraude » sont-elles visibles ?
3. Vérifier **chaque chiffre** du README, de DEFENSE, PITCH et VIDEO contre `eval/resultats/metriques.json`
   (à refaire après chaque relance de P2).
4. Vérifier sur attack.mitre.org l'identifiant ICS (T0856 ou T1692.002) et les techniques T1565.001 / T1565.002.
5. Relire chaque lien de source des docs (encore accessible ? dit bien ce qu'on lui fait dire ?).
6. **Obtenir un témoignage** (loueur, assureur plaisance, gestionnaire de port) avec les 5 questions de
   `docs/PILOTES.md`, sans contact réel non validé par l'équipe.
7. Construire le deck de 8 slides depuis `docs/PITCH.md` ; répéter le pitch de 5 min et la version de 3 min.

**Terminé quand :** le deck existe, les chiffres sont vérifiés, la vidéo de secours est enregistrée
(dimanche 9h au plus tard), et les questions probables du jury ont une réponse écrite.

---

## Questions probables du jury (P4 rédige, tout le monde relit)

- « Si votre modèle se trompe, qui est responsable ? »
- « Pourquoi pas Windward, Argotrak ou les fournisseurs de traceurs ? »
- « Vos chiffres sont synthétiques : que valent-ils ? »
- « Que se passe-t-il si l'attaquant falsifie aussi la vitesse et le cap ? »
- « Qui paie, et combien ? »
