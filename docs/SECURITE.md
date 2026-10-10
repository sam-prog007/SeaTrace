# Fiche de sécurité du produit (prototype)

SeaTrace protège l'**intégrité des données de navigation** utilisées par une plateforme maritime. Le produit
lui-même doit donc être robuste face à des données hostiles. Ce document dit ce qui est fait, ce qui est testé
et ce qui reste à faire. **Le prototype n'est pas « impossible à pirater » et ne doit pas être exposé sur un réseau.**

## 1. Imports (tout fichier est non fiable)

| Mesure | Implémentation | Test |
|---|---|---|
| Format strictement nécessaire | CSV UTF-8 seulement (`schema.lire_csv_securise`) | `test_fichiers_refuses` |
| Limites taille / lignes / longueur de champ | 5 Mo, 50 000 lignes, 64 caractères (configurables, bornés) | `test_fichier_trop_gros`, `test_trop_de_lignes` |
| Validation réelle du contenu | types, bornes, fuseau horaire obligatoire, dates plausibles, motif strict des identifiants, liste fermée de sources | `test_lignes_invalides_ecartees_ou_fichier_refuse` |
| Refus global si trop d'erreurs | > 20 % de lignes invalides → fichier refusé (pas d'analyse partielle trompeuse) | idem |
| Aucun contenu exécuté | lecture par le module `csv`, aucune évaluation, aucune formule ; aucun modèle chargé depuis l'utilisateur ; aucun lien ouvert | revue de code |
| Noms de fichiers | le nom fourni n'est jamais un chemin ; copie sous `import_<empreinte>.csv`, contrôle d'appartenance au dossier | `test_nom_fourni_jamais_utilise_comme_chemin` |
| Originaux préservés | copie conservée, jamais écrasée | `test_original_jamais_ecrase` |
| Affichage comme texte | champs restreints par motif ; rapport HTML intégralement échappé, avec politique de sécurité de contenu bloquant scripts et ressources externes | `test_rapport_html_echappe_les_champs` |
| Exports tableur | neutralisation des cellules commençant par `= + - @ tab CR` | `test_neutralisation_formules` |

## 2. Détecteur et modèle

- **Aucun réentraînement automatique** à partir des imports. Les paramètres réglés viennent d'un jeu de réglage séparé.
- **Versions identifiées** : logiciel, règles, modèle, paramètres + empreinte des paramètres dans chaque rapport et journal.
- **Paramètres bornés** : valeur hors bornes refusée, pas corrigée en silence (`test_parametres_hors_bornes_refuses`).
- **Séparation des données** : `data/demo` (analysé), `data/etiquettes` (évaluation seulement, jamais lu par l'application), `data/imports` (originaux).
- **Pas de résultat rassurant en cas de panne** : exception → message d'erreur et aucun résultat ; données insuffisantes, trajet impossible ou hypothèses divergentes → « estimation insuffisamment étayée » (`test_trajet_impossible_signale`, `test_qualification_insuffisante_si_hypotheses_divergent`).
- **Le secret des seuils n'est pas une protection** : les seuils sont publics dans `config/parametres.json`. La défense repose sur la cohérence physique (vitesse, cap, temps) et sur le croisement avec des sources indépendantes. Limite assumée : un attaquant qui modifie positions **et** vitesse/cap de façon cohérente échappe aux règles ; seules des sources indépendantes (traceur signé, observations externes) peuvent alors le contredire.

## 3. Données et traces

- Traitement **local** par défaut ; aucun envoi externe, aucune API, aucune télémétrie (`browser.gatherUsageStats = false`), serveur lié à `127.0.0.1`.
- **Journal** (`data/journal/journal.jsonl`) : imports, refus d'import, analyses, reconstructions, changements de paramètres, décisions humaines, exports. Données minimisées : identifiants de dossier, empreintes, longueurs de note — pas de positions, pas le texte des notes.
- **Chaînage SHA-256** du journal : détecte une modification ou suppression isolée (`test_journal_detecte_*`). **Ce n'est pas un journal inviolable** : quelqu'un qui contrôle la machine peut tout réécrire. Pour s'en rapprocher : ancrage externe régulier de la dernière empreinte (horodatage qualifié, dépôt chez un tiers).
- **Empreinte SHA-256 du fichier analysé** dans le rapport : prouve quelle copie a été analysée, **pas** que les positions étaient vraies à leur collecte.
- Conservation / suppression : supprimer `data/imports/`, `data/rapports/`, `data/journal/` efface toutes les données locales. Durées de conservation à fixer contractuellement lors d'un pilote.

## 4. Tests de robustesse réalisés

`python -m pytest` : 41 tests (imports malformés, binaires, volumineux, champs dangereux, injection HTML et tableur,
chemins arbitraires, journal altéré, paramètres hors bornes, trajets impossibles, manipulations progressives
(dérive), rejeu, identité dédoublée, sauts).

## 5. À implémenter avant tout déploiement partagé

- Authentification et autorisations **côté serveur** ; isolation entre clients (données, journaux, paramètres).
- Protection des modifications de paramètres (rôles, double validation, historique signé).
- Chiffrement des communications (TLS) et des données au repos ; gestion des secrets (coffre, jamais dans Git).
- Sauvegardes chiffrées et testées ; plan de restauration.
- Limites d'usage (débit, taille, nombre d'analyses) ; protection contre le déni de service par calculs coûteux.
- Revue des dépendances (versions figées, analyse de vulnérabilités, mises à jour de sécurité).
- Ancrage externe du journal ; politique de conservation et de suppression conforme au RGPD.
- Tests d'intrusion par un tiers avant toute exposition.

Tant que ces protections ne sont pas en place, le prototype reste **local**.
