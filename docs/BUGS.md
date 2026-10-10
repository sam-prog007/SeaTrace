# Suivi des bugs et vérifications

| # | Personne | Description | Reproduire | Gravité | Corrigé | Test ajouté |
|---|---|---|---|---|---|---|
| 1 | P2 | 0,17 fausse alerte d'intégrité par trajet propre | `python eval/run_eval.py --rapide` | moyenne | non | — |
| 2 | P2 | Isolation trop large sur S4 (31 points écartés) | `python scripts/analyser.py --trajet S4-falsification --zone Z1` | faible | non | — |
| 3 | P3 | Import par l'interface non couvert par un test automatique | — | moyenne | non | — |
