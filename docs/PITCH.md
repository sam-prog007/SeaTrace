# Pitch SeaTrace — draft (10 slides, 5 min)

Open Track « AI × Cybersecurity, côté défense ». Angle : infrastructure critique (transport maritime, NIS2) et
preuve vérifiable (piste 3) ; fonctions NIST CSF Detect et Respond. Tous les chiffres et leurs sources sont dans
`docs/PITCH_MATIERE.md` (niveaux ✅ / 🟡). Résultats de démo : dernier calcul sur la flotte du Kattegat (208 navires).

## Le pitch, slide par slide
Chaque slide donne le texte à l'écran, puis ce qu'on dit et le temps qu'on y passe.

### 1. Titre (10 s)
**À l'écran :** « SeaTrace — Quand la position d'un navire ment, nous reconstruisons la vérité, avec une probabilité. »
Sous-titre : défense des données de position maritime · IA probabiliste · preuve vérifiable.
**À dire :** « Bonjour, nous sommes SeaTrace. »

### 2. Accroche (30 s) — 3 chiffres plein écran, sans tableau
- **1 poisson sur 5** pêché illégalement : jusqu'à **23,5 Md$** par an (FAO). ✅
- **59 %** des zones marines protégées d'Europe sont chalutées (*Science*, 2018). ✅
- **Plus de 100 000 morts** par an dans la pêche, et 65 millions d'emplois directs (FAO, Pew). ✅

**Phrase de chute** : « Et pour surveiller tout ça, l'Europe compte sur un seul signal radio, l'AIS, que n'importe quel navire peut éteindre. »
(Variante plus locale, en réserve : dans le Kattegat, 30 chalutiers sur 31 l'éteignaient.)

**À dire** : « Un poisson sur cinq est pêché illégalement. Plus de la moitié des zones protégées d'Europe sont chalutées. Et le seul outil qui permet de le voir, l'AIS, s'éteint d'un bouton. »

### 3. Pourquoi ce problème — slide obligatoire (45 s) : pourquoi un AIS se tait
**Titre** : « Un trou dans la trace AIS peut être une fraude, une erreur cachée ou une simple panne. Aujourd'hui, personne ne sait faire la différence. »

| Pour pêcher là où c'est interdit | Pour cacher une erreur ou un accident | Sans aucune malveillance |
|---|---|---|
| Couper l'AIS avant d'entrer dans une zone fermée. Les coupures volontaires cachent jusqu'à **6 %** de la pêche mondiale (*Science Advances*, 2022). Certains cachent aussi leurs coins de pêche aux concurrents. | Après une collision, un équipage a tenté de modifier ses données pour masquer son implication (rapport du MAIB, 2009). | Trous de réception, panne d'émetteur, et **brouillage GPS** venu de l'extérieur : 14 États, dont la France, le reconnaissent comme une menace (janvier 2026). |

**Bandeau** : « L'AIS n'a **aucune authentification** : on peut le couper, le brouiller ou le falsifier. »

**À dire** : « Un pêcheur coupe son AIS pour trois raisons. Pour pêcher là où c'est interdit. Pour cacher une erreur, comme une collision. Ou sans rien cacher du tout : une panne, une zone mal couverte, un brouillage. Pour un assureur ou un contrôleur, ces trois cas se ressemblent. Accuser un innocent coûte aussi cher que laisser filer un fraudeur. »

Ce cadrage annonce directement les scénarios A, B et C de la démo.

### 4. Pourquoi les outils actuels ne suffisent pas (25 s)
**À l'écran :**
- Les plateformes de renseignement maritime (Windward, Kpler, Global Fishing Watch) **signalent** qu'un navire a disparu. Elles ne disent pas **où il a pu aller**.
- Devant un juge, la trace brute ne suffit pas. Au Royaume-Uni, des pêcheurs poursuivis sur la seule base du VMS ont été acquittés ; à Svalbard, l'affaire du câble a été classée malgré l'AIS.
- Mais une trace bien analysée gagne : *Lida Suzanna* (Irlande, 2025), l'armateur qui niait est condamné à environ 400 000 € grâce à l'AIS.

**À dire :** « Il manque la brique entre la donnée brute et la décision : une reconstruction chiffrée, explicable, présentable à un expert. »

### 5. La solution (30 s)
**À l'écran :** un pipeline en 5 étapes.
1. **Détecter** : contrôles d'intégrité (saut de position, rejeu, identité dédoublée, dérive).
2. **Isoler** les positions suspectes.
3. **Reconstruire** : modèle de Markov caché sur grille de mer, conditionné par la position **avant et après** le trou ; 5 modèles comparés, jamais moyennés.
4. **Estimer** la probabilité de passage dans chaque zone, avec ses hypothèses.
5. **Prouver** : rapport explicable et journal chaîné (SHA-256). **L'humain décide.**

**À dire :** « SeaTrace n'accuse pas : il dit "sous ces hypothèses, 98 % de chances d'être passé dans la zone", ou "0 %, le trou s'explique par la réception". C'est de l'IA défensive, auditable. »

### 6. Démo (90 s, en direct ou vidéo de secours)
Sur la carte et le tableau de bord, données réelles du Kattegat :
1. **Explorateur de la flotte** : 208 navires triés par niveau d'alerte, analysés en 10 minutes.
2. **Navire A** : 45 minutes de silence. La carte de présence et le contour à 90 % traversent la zone fermée ; P(zone) 98,6 % ; niveau « à examiner ».
3. **Navire B** : saut de 10 km détecté, positions isolées puis reconstruites.
4. **Navire C** : 45 minutes de silence, mais 4 navires voisins perdent aussi le signal. P(zone) 0 %, niveau le plus bas, « trou de réception ». **L'outil n'accuse pas à tort.**
5. **Tableau de bord** : les 5 modèles comparés sur la période de A, puis export du rapport.

**À dire en conclusion :** « Trois trajets réels modifiés en aveugle : l'algorithme a retrouvé les trois, sans aucun traitement particulier pour eux. »

**Cas d'usage réels à citer pendant la démo**
On garde les étapes du draft. Chaque navire est relié à un **cas réel** (une phrase à l'oral, sans slide de plus) :
- **A, silence en zone (pêche interdite)** → « c'est ce que faisaient les chalutiers à perche dans les eaux danoises » (TV 2 / NRC 2021).
- **B, saut de position (falsification)** → « comme les identités et positions falsifiées documentées dans le Golfe et la Baltique ».
- **C, trou innocent (réception)** → « l'outil ne l'accuse pas : P(zone) 0 %, plusieurs navires voisins perdent le signal ».
- **Conclusion sur la valeur de preuve** → « en Irlande, la trace AIS bien analysée a fait condamner un armateur qui niait (*Lida Suzanna*, environ 400 000 €). En Espagne, la position du navire a fait refuser 1,2 M€ d'indemnisation (*Dadimar Dos*). »

### 7. Preuves d'efficacité (30 s)
**À l'écran :**
- **Kattegat, données réelles** :
  - 3 scénarios sur 3 retrouvés ;
  - 7 navires sur 205 seulement à examiner (3,4 %) : on concentre les contrôles ;
  - 115 trous reconstruits, 27 trous de plus de 6 h refusés honnêtement (« non reconstruit »).
- **Banc synthétique (baie fictive)** :
  - détection : rappel 96 %, précision 97 % ;
  - erreur de probabilité (score de Brier) : 0,051, contre 0,089 pour l'interpolation ;
  - la zone à 90 % contient la vraie position dans 97 % des cas.
- **Limites affichées** : modèles calibrés sur données synthétiques ; une suppression de positions ressemble à une perte de réception.

**À dire :** « On mesure, et on montre aussi nos limites. »

### 8. Marché et modèle économique (35 s)
**À l'écran :**
- **Assureurs** : 42,6 Md$ de primes d'assurance maritime (2025), dont 46,5 % en Europe.
  - Le Lloyd's perd de l'argent sur sa ligne maritime (ratio combiné 104,3 %) : 1 point de sinistres évité vaut environ 426 M$ par an.
  - Un sinistre contesté coûte de 0,15 à 5 M€ (*Dadimar Dos* : 1,2 M€ refusés selon la position du navire).
  - **Offre** : rapport d'investigation à l'acte, 2 000 à 5 000 € (hypothèse), plus un score de risque par flotte.
- **États** : le 10 janvier 2028, suivi obligatoire de tous les navires de pêche de l'UE (environ 49 000 petits navires de plus).
  - Au moins 15 % du fonds européen de la pêche (6,1 Md€) va au contrôle et aux données.
  - **Offre** : licence par navire suivi (20 à 60 € par an, hypothèse), vendue aux centres de surveillance ou via les fournisseurs de VMS.
- **Ce que vaut une donnée maritime fiable** : Windward racheté 216 M£, Spire Maritime vendu environ 241 M$.
- **Concurrence** : ces acteurs détectent les coupures, nous reconstruisons et prouvons. Ils sont des partenaires possibles.

### 9. Et ensuite ? — slide obligatoire (30 s)
**Premiers clients :**
1. un assureur de navires de pêche ou un club P&I (rapports d'investigation sur sinistres contestés) ;
2. un centre de surveillance des pêches (CNSP en France, Fiskeristyrelsen au Danemark) pour prioriser les contrôles ;
3. un fournisseur de VMS ou de traceurs en marque blanche.

**Feuille de route à 6 mois :**
- **M1–M2** : 15 entretiens (assureurs, centres de surveillance) et 1 pilote signé.
- **M2–M4** : pilote sur une saison de données réelles ; recalibrage du modèle sur de vrais cas ; mesure des faux positifs et du temps gagné.
- **M4–M6** : rapport conforme pour un expert ou un tribunal ; intégration VMS ; hébergement sécurisé ; préparation de l'échéance de 2028.

**Ce qu'il nous faut :** un assureur et une autorité pilotes, un accès à des données VMS avec base légale, un financement d'amorçage (Bpifrance) et des crédits cloud (AWS).

### 10. Conclusion (10 s)
**À l'écran :** « Une zone protégée, un sinistre, un câble sous-marin : tous reposent sur une position. SeaTrace la rend vérifiable. »

---

## Version 3 minutes
Garder les slides 2, 3, 5, la démo (60 s, navires A et C seulement), 8 et 9.

## Questions du jury à préparer
- **« Ce n'est pas une preuve de fraude ? »** Non : une probabilité sous hypothèses, et l'humain décide. C'est voulu.
- **« Pourquoi pas Windward ? »** Il signale le trou ; nous reconstruisons le trajet et chiffrons le passage en zone. C'est complémentaire.
- **« Données réelles ? »** Oui, 208 navires d'AIS danois. Les 3 trajets de démo sont de vrais trajets, modifiés pour l'exercice ; leurs identifiants sont fictifs et non attribués.
- **« Et le scénario du pêcheur qui falsifie après un accident ? »** C'est un scénario type, appuyé par des cas réels (*Alam Pintar*, *Lida Suzanna*, *Dadimar Dos*). Ne jamais le présenter comme un fait.
- **« Le modèle est-il calibré ? »** Sur données synthétiques ; le pilote sert justement à le recalibrer sur données réelles.

---
