# Script de la vidéo de démonstration (2 min 30)

Préparation : `streamlit run app.py`, exemple de démonstration chargé, fenêtre en plein écran.
Enregistrer aussi la version de secours : `python scripts/analyser.py --trajet S4-falsification --zone Z1`
puis ouvrir le rapport HTML produit.

| Temps | À l'écran | Voix off |
|---|---|---|
| 0:00–0:15 | Bandeau « données synthétiques », carte de la baie fictive | « Après un incident, un assureur doit savoir où était vraiment un bateau de location. Mais les données de traceur manquent parfois, ou ont été modifiées. SeaTrace vérifie leur intégrité et estime ce qui a pu se passer. » |
| 0:15–0:35 | Bateau B101, trajet S1-normal : aucune alerte. Puis B102, S2-detour : écart au trajet prévu, aucune alerte | « Un trajet normal : rien. Un détour vers un mouillage : l'écart au trajet prévu est visible, mais aucune alerte — un détour n'est pas une faute. » |
| 0:35–1:05 | B104, S4-falsification : alertes « vitesse/cap incohérents » et « dérive cumulée », croix orange, chronologie | « Ici, dans notre laboratoire, on a remplacé les positions d'un passage par un faux trajet. Le déplacement calculé contredit la vitesse et le cap déclarés par le traceur : SeaTrace l'explique avec les valeurs, et écarte ces positions sans les effacer. » |
| 1:05–1:30 | Période P1, zone Z1, « Reconstruire » : corridor violet et gris, 35 %, « plusieurs chemins restent plausibles » | « Sans ces positions, plusieurs chemins restent possibles : 35 %. SeaTrace ne conclut pas — c'est une limite affichée, pas cachée. » |
| 1:30–1:50 | B105, S5-trou-zone : 90 %, sensibilité stable | « Ici, l'interruption est courte et la route directe traverse la zone : 90 %, stable quelles que soient les hypothèses. Ce n'est pas une probabilité de fraude : seulement de passage. » |
| 1:50–2:10 | B106, S6-ambigu : 85 %, puis case « observations RF simulées » : 92 % | « Une source indépendante, ici une observation radio simulée d'attribution incertaine, resserre l'estimation. Elle n'est jamais attribuée automatiquement au bateau. » |
| 2:10–2:30 | Décision de l'analyste, export du rapport HTML, onglet Journal (chaînage cohérent), onglet Évaluation | « L'analyste décide et note ; tout est journalisé et exporté dans un rapport explicable. Sur données synthétiques : 96 % des modifications de positions détectées — les suppressions apparaissent comme des interruptions — et une erreur de prévision du passage environ 40 % plus faible qu’une simple interpolation. Prochaine étape : un pilote avec un loueur et son assureur. » |
