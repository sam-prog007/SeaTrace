# Étude de marché et positionnement concurrentiel

> Recherche documentaire du 10/10/2026, faite **uniquement sur des pages publiques** : aucun entretien, aucun
> contact, aucun achat. Les tailles de marché viennent d'éditeurs d'études qui ne définissent pas le marché de la
> même façon : ce sont des **ordres de grandeur**, pas des chiffres à citer seuls. Tout ce qui concerne les prix,
> les volumes et le chiffre d'affaires de SeaTrace est une **hypothèse à valider** (voir `docs/PILOTES.md`). Les
> performances de SeaTrace citées ici viennent de données **synthétiques**. Les numéros entre crochets renvoient
> aux sources en bas de page.

## En bref

- **Le marché n'existe pas encore sous ce nom.** SeaTrace se place à l'intersection de trois marchés établis : le
  suivi des flottes de bateaux de location, l'assurance plaisance, et la surveillance maritime avec le contrôle des
  pêches. Il y occupe une brique qu'aucun acteur consulté ne met en avant : **dire, après coup, par où une petite
  embarcation a pu passer quand ses positions manquent ou sont suspectes, avec une incertitude chiffrée.**
- **Pourquoi maintenant :**
  1. Le brouillage GNSS et la manipulation de l'AIS sont reconnus comme une menace par 14 États européens
     (janvier 2026) et ils augmentent : la station de Bornholm a compté 30 incidents en 2026, contre 16 sur toute
     l'année 2025 [9][10].
  2. **À partir du 10 janvier 2028, tous les navires de pêche de l'UE de moins de 12 m doivent être suivis**
     (règlement (UE) 2023/2842). Leurs appareils enregistrent hors couverture et transmettent plus tard : les
     données auront des trous dès le départ [15].
  3. Les zones à accès restreint se multiplient (zones de pêche fermées, herbiers, calanques) [16].
- **La concurrence est forte, mais elle travaille ailleurs.** Windward, Kpler et Pole Star font de l'intégrité AIS
  pour les grands navires et vendent des données. Les fournisseurs de traceurs (Argotrak, Docklyne, Siren, CLS,
  Satlink) vendent le capteur et la carte. Les plateformes gratuites (Global Fishing Watch, Skylight) visent la
  pêche illégale à grande échelle.
- **Ce que SeaTrace apporte de différent :**
  - une reconstruction conditionnée avant ET après le trou ;
  - une probabilité de passage dans une zone, avec ses hypothèses et sa sensibilité ;
  - cinq modèles comparés, jamais moyennés ;
  - une distinction nette entre problème de disponibilité et manipulation ;
  - un rapport explicable, avec une décision humaine journalisée ;
  - un fonctionnement local, sans API externe ;
  - une indépendance vis-à-vis du traceur.
- **Le point faible :** la location seule est une niche. Pour la couche logicielle, on estime l'ordre de grandeur à
  1 à 4 M€ par an en Europe (hypothèse). Le volume se trouve du côté de la **pêche de moins de 12 m** et des
  **assureurs**. La voie la plus réaliste se fait en deux temps :
  1. un **pilote loueur + assureur** pour faire la preuve sur données réelles ;
  2. ensuite, une **distribution par un fournisseur de traceurs ou de VMS** (intégration en marque blanche ou par
     API), plutôt qu'une vente directe à des milliers de petits clients.

## 1. Sur quel marché entre-t-on ?

SeaTrace ne vend ni capteur, ni données, ni carte de suivi en temps réel. C'est une **couche logicielle d'analyse
après coup** qui se branche sur des positions déjà collectées (traceur, AIS, VMS) et répond à une question
précise :

> « Pendant cette période où les positions sont manquantes ou suspectes, où ce bateau aurait-il pu passer, avec
> quelle incertitude et sur quelles hypothèses ? »

Il touche trois marchés existants, qui ont chacun leurs acheteurs :

| Marché | Qui achète | Ce qu'ils achètent aujourd'hui | Ce que SeaTrace y ajoute |
|---|---|---|---|
| **Location de bateaux** | loueurs professionnels, plateformes de réservation | traceur GPS et logiciel de flotte : carte, géorepérage, historique | contrôle d'intégrité après la sortie, reconstruction des trous, rapport transmissible à l'assureur |
| **Assurance plaisance** (sinistres, souscription) | assureurs, courtiers, experts maritimes | expertise humaine ; pour les grands navires, plateformes de renseignement maritime | rapport d'investigation explicable sur le trajet contesté d'une petite embarcation |
| **Surveillance maritime et contrôle des pêches** | centres de surveillance des pêches (en France : CNSP), gestionnaires d'aires protégées, préfectures maritimes | VMS, AIS, imagerie satellite, analystes | priorisation des contrôles : quels trous de données sont compatibles avec un passage dans une zone fermée, et avec quelle probabilité |

## 2. Taille du marché

### 2.1 Marchés de référence (monde, ordres de grandeur)

| Marché | Estimation | Croissance annuelle | Source |
|---|---|---|---|
| Location de bateaux | 16 à 23 Md$ (2024–2025) | ≈ 5 à 6 % | [1] |
| Assurance bateaux et yachts (primes) | 0,9 à 3 Md$ selon le périmètre ; ≈ 3,0 Md$ en 2025 pour l'estimation la plus large | ≈ 4 à 6 % | [2] |
| IA appliquée à la connaissance de la situation maritime | 3,8 Md$ (2025), puis 9,4 Md$ (2034) | ≈ 12 % | [3] |
| Surveillance maritime (périmètre large) | 21,7 Md$ (2025), puis 48,3 Md$ (2034) | ≈ 9 % | [3] |
| Systèmes de suivi des navires de pêche (VMS) | 0,5 à 3,5 Md$ selon le périmètre | ≈ 4 à 8 % | [4] |

Ces marchés croissent, mais SeaTrace n'en couvre qu'une fine tranche : le logiciel d'analyse d'intégrité et de
reconstruction pour les petites embarcations. Les chiffres ci-dessus situent le contexte ; ils ne servent pas à
estimer un chiffre d'affaires.

### 2.2 Les volumes qui comptent pour nous

| Indicateur | Valeur | Source |
|---|---|---|
| Navires de plaisance immatriculés en eaux maritimes, France métropolitaine | 1 063 608 au 31/08/2025, plus 43 841 outre-mer ; 74 % à moteur, 56 % de moins de 5 m ; environ +12 000 par an | [5] |
| Flotte de location (charter) Grèce + Croatie | ≈ 5 600 à 6 300 bateaux selon la source (2025 : Grèce 3 030, Croatie 2 531 ; 2026 : Croatie 3 761, Grèce 2 542) | [6] |
| Offre d'une grande plateforme de location (SamBoat) | « plus de 50 000 » bateaux annoncés (chiffre auto-déclaré, monde, particuliers compris) | [7] |
| Polices d'assurance plaisance émises en Grèce en 2025 | 53 766 | [2] |
| Flotte de pêche de l'UE (2024) | 68 863 navires ; la petite pêche côtière (moins de 12 m, sans engin traînant) représente environ 75 % de la flotte | [8] |
| Part de la petite pêche européenne déjà équipée d'un traceur | ≈ 12 % (estimation académique) | [8] |
| Kattegat, 3 jours d'AIS de septembre 2026 (notre pipeline) | 247 navires candidats « pêche », dont 62 déclarent moins de 12 m | `reports/data_quality.md` |

### 2.3 Estimation par le bas (hypothèses à valider)

Calcul indicatif pour la seule **couche logicielle** de SeaTrace, traceurs et communications exclus. Les prix sont
des hypothèses, calées sur les prix publics des traceurs (section 4.4). Le loueur paie déjà environ 150 à 200 $ par
an et par bateau pour son traceur : notre couche doit rester une fraction de ce montant.

| Segment (Europe) | Volume retenu (hypothèse) | Prix (hypothèse) | Revenu annuel adressable |
|---|---|---|---|
| A. Bateaux de location professionnels équipés ou équipables d'un traceur | 15 000 à 30 000 bateaux | 60 à 120 € par bateau et par an | ≈ 0,9 à 3,6 M€ |
| B. Navires de pêche de moins de 12 m à suivre d'ici 2028–2030 | ≈ 50 000 navires (75 % de 68 863), dont environ 45 000 pas encore équipés | 20 à 60 € par navire et par an, vendus à l'État ou par le fournisseur de VMS | ≈ 1 à 3 M€ |
| C. Assureurs : rapports d'investigation à l'acte | inconnu (le nombre de sinistres où le trajet est contesté reste à mesurer en entretien) | à tester en entretien | non estimé |

**Ordre de grandeur réaliste à 3 ans** (hypothèse) : 2 à 5 clients (loueurs ou assureurs) et un partenariat avec
un fournisseur, soit 500 à 3 000 bateaux suivis et environ 30 k€ à 360 k€ de revenu récurrent annuel.

**Lecture honnête** : sur la location seule, SeaTrace est une fonctionnalité de niche, pas une entreprise à grande
échelle. Pour changer d'échelle, il faut un de ces trois leviers :

1. le rapport à l'acte pour les assureurs, où se trouve la valeur d'un dossier ;
2. la pêche de moins de 12 m, en Europe puis au-delà ;
3. une licence de marque blanche à des fournisseurs de traceurs ou de VMS, où un contrat couvre des milliers de
   bateaux.

## 3. Pourquoi maintenant : les moteurs du marché

### 3.1 Le brouillage et la falsification de position sont une menace reconnue

- **26 janvier 2026** : 14 États (dont la France) publient une lettre à la communauté maritime internationale. Ils
  demandent de reconnaître le brouillage GNSS et la manipulation de l'AIS comme des menaces pour la sécurité de la
  navigation [9].
- **Bornholm** : la station danoise a enregistré 30 brouillages ou leurrages en 2026 (au 10 août), contre 16 sur
  toute l'année 2025 et 11 en 2024 [10].
- **Gdańsk** : une étude menée en 2025 mesure un positionnement GNSS indisponible environ 17 % du temps fin juin et
  en juillet [11].
- **2e trimestre 2026** : Windward recense 171 286 navires touchés au moins une fois par un brouillage GPS. C'est
  un chiffre de fournisseur [12].
- **Juin 2026** : *Yachting Monthly* alerte les plaisanciers sur le brouillage et le leurrage [13].
- **Usurpation AIS** : Pole Star a mis au jour plus de 236 jours de positions falsifiées sur un seul pétrolier
  [14].

**Conséquence pour SeaTrace** : les historiques de position deviennent moins fiables, plaisance comprise. Les
trous, les sauts et les positions incohérentes vont devenir courants dans les dossiers de sinistre et de contrôle.

### 3.2 Le suivi des navires de pêche de moins de 12 m devient obligatoire (règlement (UE) 2023/2842)

Ce qui suit a été lu directement dans le texte publié sur EUR-Lex [15] :

- **Art. 9(2)** : chaque navire de pêche de l'Union doit avoir à bord un dispositif de suivi pleinement
  fonctionnel, qui transmet sa position à intervalles réguliers.
- **Art. 9(3)** : un navire de moins de 12 m peut emporter un appareil non fixé à bord. **Hors couverture réseau,
  l'appareil enregistre les positions et les transmet dès que le réseau revient**, au plus tard avant l'arrivée au
  port. Il y aura donc des trous et des données arrivées en retard dès le départ.
- **Art. 8(2)** (dispositions transitoires) : pour les navires de moins de 12 m, l'article 9 s'applique **à partir
  du 10 janvier 2028**.
- **Art. 9(4)** : un État peut exempter **jusqu'au 31 décembre 2029** les navires de moins de 9 m qui remplissent
  certaines conditions (engins passifs, moins de 6 milles des côtes, etc.).
- **Art. 90 et annexe IV** :
  - la **falsification de données** est une infraction grave en soi (art. 90(2)) ;
  - ne pas enregistrer ou transmettre correctement les données du VMS devient une infraction grave (art. 90(3))
    notamment en cas d'**interférence avec le VMS ou l'AIS, y compris son extinction** (sauf autorisation), ou
    quand aucune donnée n'est envoyée au centre de surveillance.

**Conséquence pour SeaTrace** : environ 45 000 navires vont commencer à produire des positions trouées. Les
autorités devront faire la part entre une perte de couverture et une coupure volontaire, sans rien décider
automatiquement. C'est exactement le cadre de SeaTrace :

- une interruption est signalée comme un problème de **disponibilité**, jamais comme une attaque ;
- l'outil donne la **probabilité d'un passage dans une zone fermée pendant le trou**, avec ses hypothèses.

SeaTrace ne prouve pas l'intention. Une suppression de positions ne se distingue pas d'une perte de réception
(98 % des suppressions sont signalées comme des interruptions). L'outil sert à **prioriser les contrôles**, pas à
sanctionner.

### 3.3 Plus de zones à accès restreint

- **Kattegat** : le règlement délégué (UE) 2022/952 interdit les engins de fond mobiles ou toute pêche selon les
  zones. Le pipeline `src/03_zones.py` lit déjà ces zones depuis EUR-Lex.
- **Méditerranée française** : une stratégie commune protège les herbiers de posidonie. Des arrêtés encadrent le
  mouillage des navires de 20 ou 24 m et plus. Dans les calanques d'En-Vau et de Port-Pin, le mouillage, l'arrêt et
  la dérive sont interdits en permanence à tout navire [16].

**Conséquence pour SeaTrace** : la question « le bateau est-il passé dans la zone ? » se pose de plus en plus
souvent. Elle intéresse le loueur (sa responsabilité), l'assureur (ses exclusions géographiques) et l'autorité (ses
contrôles). Aujourd'hui, SeaTrace estime le *passage*. Estimer un *arrêt* ou un *mouillage* dans la zone serait une
extension naturelle : le modèle à cap persistant prévoit déjà des arrêts.

### 3.4 Les données maritimes se concentrent

- **Kpler** a racheté MarineTraffic, puis Spire Maritime (241 M$, annoncé en novembre 2024). Des clients ont
  alerté les autorités de concurrence. Après le rachat de MarineTraffic, certaines sociétés d'analyse avaient déjà
  perdu leur accès aux données [17].
- **Windward** a été racheté par FTV Capital (environ 270 M$, finalisé le 18/03/2025), puis a racheté Prominent
  Edge, qui travaille pour la défense américaine [18].
- **HawkEye 360** est entré en bourse en mai 2026 (416 M$ levés). Son chiffre d'affaires du 2e trimestre 2026
  atteint 49,8 M$, en hausse de 87 % [19].
- **Unseenlabs** (Rennes) a levé 85 M€ en 2024 pour agrandir sa constellation de satellites d'écoute
  radiofréquence [20].

**Conséquence pour SeaTrace** :

- les grands acteurs consolident et se tournent vers la défense, les États et les grands navires : le segment des
  petites embarcations reste peu servi ;
- dépendre de l'AIS d'un acteur dominant serait un risque. Il vaut mieux s'appuyer sur les traceurs des clients et
  sur les données ouvertes (DMA, NOAA), que le prototype sait déjà lire.

### 3.5 Côté autorités, des outils gratuits existent

**Skylight** (Allen Institute for AI) est gratuit et utilisé par plus de 300 organisations dans environ 70 pays.
Il a lancé en 2026 un agent conversationnel et s'est associé à Global Fishing Watch le 23/09/2026 [21].

**Conséquence pour SeaTrace** : la pression sur les prix est forte pour le segment des autorités. SeaTrace doit
être **complémentaire** et non concurrent frontal. Par exemple, le prototype lit déjà les détections SAR de Global
Fishing Watch comme observations externes.

### 3.6 Les assureurs s'appuient déjà sur la donnée de position

- **Grands navires** : Windward aide les souscripteurs et les gestionnaires de sinistres à vérifier l'activité
  quand l'AIS est incomplet, manipulé ou contesté. Il met en avant des données confirmées par satellite pour
  refuser des indemnisations injustifiées ou gonflées [22].
- **Plaisance** : depuis décembre 2022, Markel fournit à ses clients loueurs de bateaux un traceur Boat Fix sans
  surcoût, avec une réduction de prime [23].
- **Location en France** : le bateau loué est déjà assuré. Le locataire supporte une franchise, souvent égale à
  la caution, qu'il peut racheter [24]. En cas de dégât, savoir où était le bateau devient un enjeu financier
  direct.

**Conséquence pour SeaTrace** : les traceurs entrent dans la relation entre l'assureur et le loueur. Mais aucune
des offres consultées ne met en avant la **vérification de l'intégrité** de ces données ni la **reconstruction**
des trous.

## 4. Concurrence

### 4.1 Les acteurs, en quatre familles

**Famille 1. Traceurs et logiciels de flotte pour la location ou la plaisance**

| Acteur | Offre (pages publiques) | Lien avec SeaTrace |
|---|---|---|
| **Argotrak** | Suivi GPS spécialisé pour les loueurs et les clubs : géorepérage, alerte d'usage non autorisé, surveillance de la cale ; à partir de 15 $ par utilisateur et par mois [25] | Canal de distribution et source de données ; concurrent s'il ajoute une couche d'analyse |
| **Docklyne** | Logiciel de réservation pour loueurs, suivi GPS fourni par un tiers, historique de position par location, photos au départ et au retour, place de marché [26] | Idem |
| **Siren Marine** | Bateau connecté (batterie, cale, GPS, géorepérage) ; matériel ≈ 599 $, abonnement ≈ 180 $ par an [27] | Vise surtout les propriétaires |
| **Nautic-On** (Brunswick) | Bateau connecté ; ≈ 600 $, plus 149 $ par an [27] | Idem |
| **Boat Fix** | Traceur fourni par Markel aux loueurs qu'il assure [23] | Montre que l'assureur est prêt à payer pour la donnée |

**Famille 2. Renseignement maritime et analytique pour l'assurance**

| Acteur | Offre (pages publiques) | Lien avec SeaTrace |
|---|---|---|
| **Windward** (FTV Capital) | Plateforme d'IA maritime ; offre dédiée aux assureurs ; détection de manipulation AIS ; télédétection (optique, radar SAR, radiofréquence) quand l'AIS est incomplet, manipulé ou contesté [18][22] | Le concurrent le plus proche sur l'intégrité, mais pour les navires équipés d'AIS |
| **Kpler** (avec MarineTraffic et Spire Maritime) | Données AIS terrestres et satellitaires, analyses de risque et de conformité [17] | Fournisseur de données dominant ; risque de dépendance |
| **Pole Star** | Suivi de navires, conformité aux sanctions, enquêtes d'usurpation AIS menées au cas par cas par des analystes [14] | Approche d'enquête humaine, pour les grands navires |
| **Concirrus** | Analytique d'assurance maritime (corps et marchandises), mouvements des navires toutes les 15 min, données de sinistres [28] | Souscription des grands navires ; pas les petites embarcations |

**Famille 3. Observation indépendante depuis l'espace**

| Acteur | Offre (pages publiques) | Lien avec SeaTrace |
|---|---|---|
| **Unseenlabs** | Géolocalisation des navires par leurs émissions radio, y compris les navires « sombres » ; clients : États, agences, assureurs, pêche, ONG [20] | **Fournisseur potentiel** : SeaTrace intègre déjà des observations externes avec une probabilité d'attribution |
| **HawkEye 360** | Données et analyses radiofréquence depuis l'espace ; tourné vers la défense et le renseignement [19] | Idem |

**Famille 4. Surveillance des pêches et ONG**

| Acteur | Offre (pages publiques) | Lien avec SeaTrace |
|---|---|---|
| **CLS** (Toulouse) | Plateforme VMS Themis pour les administrations, multi-sources (Argos, Iridium, Inmarsat, GPRS, AIS) ; balise NEMO pour les petits bateaux [29] | Opérateur installé du VMS ; partenaire possible pour les moins de 12 m |
| **Satlink** (Espagne) | Traceurs ; système agréé au Royaume-Uni pour les moins de 12 m, transmission par réseau cellulaire [30] | Fournisseur de traceurs pour moins de 12 m ; partenaire possible |
| **Pelagic Data Systems** | Traceurs solaires pour petits bateaux, avec tableau de bord pour les agents des pêches [31] | Idem |
| **OceanMind** (ONG britannique) | Renseignement sur les pêches en service, pour les États côtiers, du port et du pavillon : satellites, IA et analystes [32] | Approche fondée sur des analystes |
| **Global Fishing Watch, Skylight** | Plateformes gratuites : AIS, VMS, détections SAR et optiques, navires « sombres » [21] | Gratuits ; complémentaires (SeaTrace lit déjà les détections SAR de GFW) |

### 4.2 Matrice comparative

Légende : ● mis en avant sur les pages publiques · ◐ partiel ou indirect · ○ non mis en avant sur les pages
consultées (**ce qui ne veut pas dire absent**) · ? non vérifié. Pour SeaTrace, ● veut dire « présent dans le
prototype, testé sur données synthétiques ».

| Critère | SeaTrace | Traceurs de location (Argotrak, Docklyne, Siren) | Windward | Kpler (MarineTraffic, Spire) | Pole Star | Écoute radio depuis l'espace (Unseenlabs, HawkEye 360) | VMS pêche (CLS, Satlink, PDS) | GFW, Skylight, OceanMind |
|---|---|---|---|---|---|---|---|---|
| Cible : petites embarcations sans AIS | ● | ● | ○ | ○ | ○ | ? | ● | ◐ |
| Fournit le capteur ou les données | ○ | ● | ◐ | ● | ● | ● | ● | ◐ |
| Contrôle d'intégrité des positions (sauts, rejeu, identité dédoublée) | ● | ○ | ● | ◐ | ● | ◐ | ? | ◐ |
| Reconstruction des trous avec incertitude chiffrée | ● | ○ | ◐ | ○ | ○ | ○ | ○ | ○ |
| Probabilité de passage dans une zone, avec hypothèses et sensibilité | ● | ◐ (géorepérage sur les positions reçues seulement) | ○ | ○ | ○ | ○ | ◐ (géorepérage) | ○ |
| Plusieurs modèles comparés, désaccord affiché | ● | ○ | ○ | ○ | ○ | ○ | ○ | ○ |
| Rapport explicable et décision humaine journalisée | ● | ○ | ? | ○ | ◐ (analystes) | ○ | ? | ◐ (analystes) |
| Fonctionne hors ligne, sur site | ● | ? | ? | ? | ? | ? | ? | ○ (services en ligne) |
| Prix public | hypothèse | dès 15 $/mois ; ≈ 150–200 $/an + matériel | sur devis | sur devis | sur devis | sur devis | matériel + communications | gratuit |

### 4.3 Carte de positionnement

```
                     Réponse probabiliste et explicable sur une période sans données fiables
                                               ▲
                                               │      ★ SeaTrace (prototype)
                    Windward, Pole Star        │
                    (comblent les trous par    │
                    télédétection ou analystes)│
                                               │
  Grands navires ──────────────────────────────┼────────────────────────────── Petites embarcations
  (AIS, LRIT, SAR, radio)                      │                               (traceurs, < 12 m, sans AIS)
                                               │
       Kpler, Unseenlabs, HawkEye 360,         │      Argotrak, Docklyne, Siren,
       GFW, Skylight, OceanMind                │      CLS, Satlink, PDS
                                               │
                                               ▼
                           Position observée, alerte ou score de risque
```

Le quadrant en haut à droite est vide parmi les acteurs consultés. C'est une **opportunité** si le besoin existe,
ou un **signal d'alerte** s'il n'existe pas. C'est ce que les entretiens doivent trancher.

### 4.4 Repères de prix publics

| Produit | Prix public | Source |
|---|---|---|
| Argotrak | dès 15 $ par utilisateur et par mois | [25] |
| Siren Marine (Siren 3 Pro) | matériel ≈ 599 $ ou plus ; abonnement ≈ 180 $ par an | [27] |
| Nautic-On (Brunswick) | ≈ 600 $, plus 149 $ par an | [27] |
| Windward, Kpler, Pole Star, Unseenlabs, HawkEye 360, CLS | sur devis | — |
| Global Fishing Watch, Skylight | gratuit (Skylight : États, organisations régionales de pêche, ONG éligibles) | [21] |

## 5. Ce que SeaTrace apporte de différent

En une phrase : **les autres disent où le bateau a été vu, ou si un navire est suspect. SeaTrace dit ce qu'on peut
raisonnablement conclure sur la période où on ne l'a pas vu, et quand on ne peut pas conclure.**

Nous ne prétendons pas que cette combinaison est unique : ce n'est pas vérifié, et des fonctions proches peuvent
exister sans être mises en avant. Voici les différences, chacune avec sa preuve dans le dépôt.

| # | Différence | Chez les concurrents consultés | Chez SeaTrace | Preuve dans le dépôt |
|---|---|---|---|---|
| 1 | **La question posée** | Suivi en temps réel (traceurs), détection d'anomalies et score de risque (renseignement maritime), détection de navires « sombres » (GFW, Skylight) | Reconstruction **après coup**, conditionnée par la position **avant ET après** le trou. Le drapeau « déjà passé dans la zone » donne exactement la probabilité d'au moins une entrée, sous hypothèses et à la résolution de la grille | `seatrace/reconstruct.py`, README § Modèle |
| 2 | **Une incertitude chiffrée et vérifiée, et le droit de ne pas conclure** | Alerte binaire ou score de risque | Score de Brier 0,051 contre 0,089 pour l'interpolation ; la région à 90 % contient la position réelle dans 97 % des cas ; statut « étayée / fragile / insuffisamment étayée » ; dans le scénario S4, l'outil **ne conclut pas** (35 %) | `eval/resultats/metriques.json`, `docs/DEFENSE.md` |
| 3 | **Cinq modèles comparés, jamais moyennés** | Un seul moteur, en général non documenté publiquement | HMM, modèle appris, filtre particulaire avec lissage, pont brownien, boosting. Quand ils divergent, l'outil dit que la réponse dépend des hypothèses. Un contradicteur doit expliquer pourquoi des modèles aux hypothèses différentes sont d'accord | `seatrace/comparatif.py`, `docs/MODELES.md` |
| 4 | **Intégrité et disponibilité séparées ; aucun score de culpabilité** | Un « navire suspect » ou un score de risque global | Une interruption est un problème de *disponibilité*, jamais une attaque. Les scores sont séparés : anomalie, qualité, passage, écart au trajet prévu. Sur données synthétiques : rappel 96 %, précision 97 %. Cela correspond à l'article 90 du règlement pêche : séparer la perte de couverture de la coupure, sans présumer l'intention | `seatrace/detect.py`, `docs/DEFENSE.md` |
| 5 | **Pensé pour les petites embarcations, indépendant du traceur** | Construit sur l'AIS des grands navires, ou lié au matériel du vendeur | Import CSV de n'importe quel traceur, AIS DMA ou NOAA, classe B. Vitesse maximale selon le type de bateau, terres infranchissables. Adapté aux appareils des moins de 12 m qui transmettent en différé | `seatrace/sources.py`, `scripts/importer_ais.py` |
| 6 | **Fusion d'observations indépendantes avec attribution incertaine** | La télédétection est vendue comme une preuve (« satellite-confirmed ») | Une détection n'est **jamais attribuée d'office** au bateau : elle entre dans le calcul avec une probabilité d'attribution (scénario S6 : 85 % sans, 92 % avec). Les satellites radio deviennent des **fournisseurs**, pas des concurrents | `seatrace/pipeline.py`, `docs/DONNEES_OUVERTES.md` § 5 |
| 7 | **Explicabilité et traçabilité pensées pour la preuve** | Explications portées par des analystes (Pole Star, OceanMind) ou non mises en avant | Chaque alerte affiche les valeurs calculées qui la justifient. Les textes d'interprétation sont produits par des règles fixes, sans modèle de langage. Journal chaîné SHA-256, décision et note humaines journalisées, paramètres versionnés. Avec des graines fixées, les mêmes données et les mêmes paramètres redonnent le même résultat | `seatrace/audit.py`, `seatrace/report.py`, `config/parametres.json` |
| 8 | **Local, hors ligne, sans API externe** | Services en ligne (pour la plupart, non vérifié un par un) | Bibliothèques de carte et de graphiques embarquées, aucune API externe. Important pour les autorités (hébergement souverain) et pour le RGPD (trajets rattachables à des locataires) | README § Installation |
| 9 | **Léger à faire tourner** | — | Analyse d'un trajet en ≈ 17 ms, reconstruction en ≈ 0,2 à 0,4 s par interruption. Peut donc s'intégrer à la plateforme d'un fournisseur de traceurs pour un coût marginal faible, ce qui soutient l'offre en marque blanche | README § Métriques |
| 10 | **Zones réglementaires réelles déjà outillées** | — | Zones du règlement 2022/952 lues depuis EUR-Lex, avec des contrôles qui arrêtent le script si la structure du texte change ; AIS réel de la pêche au Kattegat ; temps passé en zone et en tampon | `src/03_zones.py`, `src/07_crossing.py`, `reports/zones_check.md` |

### 5.1 Ce qui n'est pas (encore) un avantage

- **Pas de données propres ni de capteur.** SeaTrace dépend des données du client ou des données ouvertes, alors
  que les grands acteurs ont leurs réseaux de stations et de satellites.
- **Calibration sur données synthétiques seulement** ; aucun client, aucun pilote, aucun entretien à ce jour.
- **Pas de temps réel**, pas d'authentification ni d'isolation entre clients (feuille de route).
- **Contournable.** Un attaquant qui modifie de façon cohérente les positions *et* la vitesse ou le cap échappe aux
  règles. Une suppression ne se distingue pas d'une perte de réception.
- **Facile à imiter sur le principe.** Windward, Kpler ou un fournisseur de traceurs pourraient ajouter une
  reconstruction des trous. Ce qui nous protège, ce ne serait pas l'idée, mais :
  - une calibration sur données réelles ;
  - une explicabilité qui inspire confiance ;
  - des partenariats d'intégration signés tôt.
- **Concurrence gratuite côté autorités** (Skylight, Global Fishing Watch).

## 6. Synthèse forces, faiblesses, opportunités, menaces

| | Positif | Négatif |
|---|---|---|
| **Interne** | **Forces** : incertitude chiffrée et calibrée ; plusieurs modèles ; distinction disponibilité / manipulation ; rapport explicable et journal chaîné ; local et indépendant du traceur ; calcul rapide ; pipeline réglementaire réel (Kattegat) | **Faiblesses** : aucune donnée réelle de petites embarcations ; calibration synthétique ; pas de temps réel ni de multi-clients ; équipe de 4 issue d'un hackathon ; aucune preuve de disposition à payer |
| **Externe** | **Opportunités** : suivi obligatoire des moins de 12 m au 10/01/2028 ; brouillage GNSS en hausse et reconnu par les États ; plus de zones restreintes ; assureurs déjà tournés vers la donnée de position ; grands acteurs tournés vers les grands navires et la défense ; consolidation des plateformes de location (moins d'acheteurs, mais plus gros) [33] | **Menaces** : un fournisseur de traceurs ou de VMS construit la fonction lui-même ; outils gratuits pour les autorités ; dépendance aux données d'un acteur dominant ; faible valeur perçue si les sinistres contestés sont rares ; poids juridique incertain d'une probabilité dans un dossier |

## 7. Implications : où et comment entrer

### 7.1 Choix du segment d'attaque

| Critère | Loueur + assureur | Pêche de moins de 12 m (autorités) | Aires protégées, préfectures maritimes |
|---|---|---|---|
| Douleur explicite | moyenne (fréquence des sinistres contestés inconnue) | forte à partir de 2028 (obligation de suivi, infractions graves) | moyenne |
| Accès à des données réelles | rapide (export CSV du traceur) | lent (données régaliennes) | lent |
| Cycle de vente | court (semaines à mois) | long (marché public) | long |
| Volume | faible (≈ 1 à 4 M€ par an en Europe, hypothèse) | moyen (≈ 50 000 navires) | faible à moyen |
| Concurrence directe | faible : les traceurs ne mettent pas d'analyse en avant | CLS, Satlink, OceanMind ; GFW et Skylight gratuits | idem, plus les analystes internes |
| Adéquation du prototype | bonne : parcours location, rapport | bonne : Kattegat, règlement réel, AIS pêche | bonne : zones importées ou dessinées |

### 7.2 Recommandation

1. **Prouver avec un pilote loueur + assureur** (pilote A de `docs/PILOTES.md`). C'est le chemin le plus court
   vers des données réelles. Il permet de recalibrer les seuils, de mesurer le temps gagné par dossier et de
   tester qui paie.
2. **Distribuer par un fournisseur de traceurs ou de VMS**, en marque blanche ou par API :
   - côté location : Argotrak, Docklyne ;
   - côté pêche de moins de 12 m : Satlink, CLS, Pelagic Data Systems.

   Un contrat couvre alors des milliers de bateaux. Cela neutralise aussi le principal risque, que le fournisseur
   construise la fonction lui-même.
3. **Viser l'échéance du 10 janvier 2028 pour la pêche de moins de 12 m.** L'idée est de proposer aux centres de
   surveillance, par le partenaire VMS, une **priorisation** des trous de données compatibles avec un passage en
   zone fermée. On peut commencer là où les données sont ouvertes et déjà outillées : AIS danois et zones du
   Kattegat.
4. **Facturer la valeur là où elle se trouve** :
   - un rapport à l'acte pour l'assureur ;
   - un abonnement léger par bateau pour le loueur, sans dépasser une fraction du coût de son traceur ;
   - une licence par flotte suivie pour le partenaire.
5. **Rester complémentaire des plateformes gratuites et des satellites** : importer leurs détections au lieu de
   les concurrencer.

### 7.3 Message de positionnement

- **FR** : *SeaTrace ne suit pas les bateaux. Il dit ce que l'on peut conclure, et ce que l'on ne peut pas
  conclure, quand leur trajet a des trous ou a été altéré.*
- **EN** (site) : *SeaTrace doesn't track boats. It tells you what you can, and cannot, conclude when a track has
  gaps or has been tampered with.*

### 7.4 Hypothèses à valider en priorité

Ces hypothèses s'appuient sur les questions d'entretien de `docs/PILOTES.md`.

1. **Fréquence** des dossiers où le trajet d'une petite embarcation est incomplet ou contesté, chez les loueurs et
   chez les assureurs.
2. **Qui paie** (le loueur ou l'assureur), sur quel budget, et combien : à situer par rapport aux 150 à 200 $ par
   an du traceur.
3. **Données des traceurs de location** : fournissent-ils la vitesse, le cap et la précision ? À quelle
   fréquence ?
4. **Intérêt des fournisseurs** de traceurs ou de VMS pour intégrer une couche d'analyse tierce.
5. **Besoin des centres de surveillance des pêches** de reconstruire les trous des moins de 12 m, et forme
   juridiquement acceptable du résultat.
6. **Acceptabilité d'une probabilité** dans un dossier de sinistre ou de contrôle, aux yeux d'un juriste.

## Sources

Consultées le 10/10/2026. Les chiffres d'éditeurs d'études et de fournisseurs sont des déclarations, pas des
mesures indépendantes.

1. Location de bateaux : Global Industry Analysts via GII, https://m.giikorea.co.kr/report/go1643471-boat-rentals.html ; ResearchAndMarkets via GlobeNewswire (04/07/2025), https://www.globenewswire.com/fr/news-release/2025/07/04/3110302/0/en/Boat-Rental-Market-Outlook-Report-2025-2034-Peer-to-Peer-Platforms-Transforming-the-Industry-Tourism-and-Technology-Innovations-Drive-Expansion-Globally.html
2. Assurance bateaux et yachts : The Business Research Company, https://www.thebusinessresearchcompany.com/report/boats-and-yachts-insurance-global-market-report ; Global Market Insights, https://www.gminsights.com/industry-analysis/boat-and-yacht-insurance-market ; Intel Market Research, https://www.intelmarketresearch.com/boat-insurance-market-10881 ; Association hellénique des compagnies d'assurance, *Review Yacht 2025*, https://www.eaee.gr/wp-content/uploads/2026/05/Review_Yacht_2025_eng.pdf
3. Connaissance de la situation et surveillance maritimes : Market Intelo, https://marketintelo.com/report/maritime-domain-awareness-ai-market/amp ; ResearchAndMarkets, https://www.researchandmarkets.com/reports/6185326/maritime-surveillance-market-outlook-market
4. VMS pêche : WiseGuy Reports, https://www.wiseguyreports.com/reports/vessel-monitoring-system-for-fishery-market ; Strategic Revenue Insights, https://www.strategicrevenueinsights.com/industry/vessel-monitoring-system-market
5. DGAMPA, *Chiffres clés du nautisme 2025*, https://www.mer.gouv.fr/sites/default/files/2026-01/chiffres-cles-nautisme-2025-donnees_0.pdf ; ministère de la Mer, https://www.mer.gouv.fr/le-secteur-de-la-plaisance-et-des-loisirs-nautiques-en-mer. *Domaine inaccessible depuis l'environnement de travail : chiffres repris des extraits indexés, à revérifier sur le PDF.*
6. Flottes de charter : Riginos Yachts 2025 via Greek Trip Planner, https://greektriplanner.me/insights/greece-yacht-charter-marine-tourism ; charter.hr 2026 via Lider, https://en.lider.media/?p=220092
7. SamBoat, https://www.samboat.com/latesail?country=us
8. Flotte de pêche : Commission européenne, *Key Figures 2025*, via The Fishing Daily, https://thefishingdaily.com/eu-fishing-industry-news/eu-fishing-fleet-shrinks-as-catches-remain-concentrated/ ; ClientEarth, https://www.clientearth.org/media/p3jdr4iv/small-scale-fishers-least-supported-recipients-of-eu-funds.pdf ; université de St Andrews, *Towards a European small-scale fishing vessel tracking programme*, https://research-portal.st-andrews.ac.uk/en/publications/towards-a-european-small-scale-fishing-vessel-tracking-programme-/
9. Lettre des 14 États (26/01/2026) : gouvernement finlandais, https://valtioneuvosto.fi/en/-/1410829/itameren-ja-pohjanmeren-rantavaltiot-gnss-hairinta-on-lisannyt-merenkulun-turvallisuusriskeja-1 ; Pole Star, https://www.polestarglobal.com/resources/14-states-russian-maritime-gps-interference/
10. Bornholm (10/08/2026) : https://www.eurointegration.com.ua/eng/news/2026/08/10/7243248/
11. Étude GPSPatron et université maritime de Gdynia : The Maritime Executive, https://maritime-executive.com/article/study-baltic-gps-disruption-comes-from-a-tactically-controlled-network
12. Windward via Hellenic Shipping News, https://www.hellenicshippingnews.com/what-marine-underwriters-are-now-pricing-against/
13. *Yachting Monthly* (30/06/2026), https://www.yachtingmonthly.com/cruising/navigating-blind-the-urgent-threat-of-gps-spoofing-to-sailors-105646
14. Pole Star, enquête pour le registre de Saint-Marin, https://www.polestarglobal.com/resources/ais-spoofing-investigation-san-marino-ship-register/
15. Règlement (UE) 2023/2842, texte consolidé sur EUR-Lex (CELEX 32023R2842), lu le 10/10/2026 : https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32023R2842 (art. 1 point 6, qui remplace l'art. 9 du règlement 1224/2009 ; art. 8(2) ; art. 90 et annexe IV du règlement 1224/2009 modifié)
16. Mouillage en Méditerranée : Made in Marseille, https://madeinmarseille.net/93611-calanque-mouillage-bateau-regelementation-arrete-prefectoral/ ; Figaro Nautisme, https://figaronautisme.meteoconsult.fr/actus-nautisme-bateaux/2020-12-16/57518-var-interdiction-de-mouillage-pour-les-navires-de-24m-et-plus-dans-certaines-zones
17. Kpler et Spire Maritime : The Maritime Executive, https://maritime-executive.com/article/kpler-acquires-spire-maritime-to-enhance-maritime-data-and-analytics ; Baird Maritime, https://www.bairdmaritime.com/shipping/spires-ship-tracking-customers-raise-antitrust-concerns-over-potential-sale-to-kpler
18. Windward : FTV Capital, https://ftvcapital.com/2025/ftv-capital-completes-acquisition-of-maritime-ai-leader-windward/ ; Prominent Edge, https://orangeslices.ai/windward-acquires-prominent-edge-to-deepen-u-s-defense-and-national-security-capabilities/
19. HawkEye 360 : Renaissance Capital, https://www.renaissancecapital.com/IPO-Center/News/118834/signal-processing-platform-hawkeye-360-prices-ipo-at-26-the-high-end ; résultats du 2e trimestre 2026, https://lifestyle.kbew98country.com/story/382031/hawkeye-360-announces-second-quarter-2026-financial-results/
20. Unseenlabs, levée de 85 M€ (27/02/2024), https://unseenlabs.com/en/news/record-breaking-fundraising-of-85-million/
21. Skylight (Ai2) : GeekWire, https://www.geekwire.com/2026/ai2s-skylight-project-launches-shippy-an-ai-agent-that-dives-into-ocean-data/
22. Windward, assurance maritime, https://windward.ai/industries/marine-insurance/
23. Markel et Boat Fix (22/12/2022), https://www.marinadockage.com/?p=17481
24. Assurance de la location en France : Réassurez-moi, https://reassurez-moi.fr/guide/assurance-bateau/location ; LeComparateurAssurance, https://www.lecomparateurassurance.com/assurance-bateau/type-bateau/assurance-location-bateau
25. Argotrak sur Capterra, https://www.capterra.com/p/203590/Argotrak/
26. Docklyne, https://docklyne.com/boat-rental-software/gps-fleet-tracking
27. Siren Marine : fiche Siren 3 Pro, https://zoro.com/static/cms/enhanced_pdf/CWR%20Electronics%20Corp_85426BRxxfcd43e.pdf ; Siren et Nautic-On : Salt Water Sportsman, https://www.saltwatersportsman.com/?p=53625
28. Concirrus : Insurance Business, https://www.insurancebusinessmag.com/uk/news/marine/marine-insurance-set-full-steam-ahead-for-data-and-analytics-251989.aspx
29. CLS : Themis (revendeur Propmech), https://vms.propmech.com/fmc.html ; balise NEMO (Oceana), https://europe.oceana.org/?p=14510
30. Satlink : SeafoodSource, https://www.seafoodsource.com/news/processing-equipment/satlink-gains-approval-for-sale-of-tracking-system-to-uk-artisanal-fishers
31. Pelagic Data Systems : Global Fishing Watch, https://globalfishingwatch.org/?p=1099
32. OceanMind, https://oceanmind.global/what-we-do/fisheries-compliance/monitoring-control-and-surveillance
33. Consolidation des plateformes de location : FrenchWeb (rachats de SamBoat et HeyCaptain), https://www.frenchweb.fr/consolidation-sur-le-marche-de-la-location-de-bateaux-en-ligne-avec-les-rachats-de-samboat-et-heycaptain/339957 ; Silicon Canals (Click&Boat rachète Nautal), https://siliconcanals.com/clickboat-airbnb-of-seas-acquires-european-competitor-nautal/
