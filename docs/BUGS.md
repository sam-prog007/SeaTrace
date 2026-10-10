# Suivi des bugs et vérifications

| # | Personne | Description | Reproduire | Gravité | Corrigé | Test ajouté |
|---|---|---|---|---|---|---|
| 1 | P2 | 0,17 fausse alerte d'intégrité par trajet propre | `python eval/run_eval.py --rapide` | moyenne | non | — |
| 2 | P2 | Isolation trop large sur S4 (31 points écartés) | `python scripts/analyser.py --trajet S4-falsification --zone Z1` | faible | non | — |
| 3 | P3 | Import par l'interface non couvert par un test automatique | — | moyenne | oui pour l'interface V2 (Flask) ; non pour Streamlit | `test_web_import_ais_zone_dessinee_et_reconstruction` |
| 4 | V2 | Estimateur de la variance du pont brownien biaisé d'un facteur 2 (distance² divisée deux fois) | `pytest tests/test_v2_modeles.py -k sigma` | moyenne | oui | `test_estimation_sigma_m_retrouve_la_mobilite` |
| 5 | V2 | Conversion AIS : avec pandas 3, horodatages en microsecondes ; l'écart entre positions était faux et presque tout était écarté | `pytest tests/test_v2_sources_web.py -k dma` | haute | oui | `test_conversion_dma`, `test_conversion_noaa_et_import_securise` |
| 6 | V2 | Segmentation : un nuage de chemins continu était coupé en deux familles artificielles (silhouette seule) | `pytest tests/test_v2_modeles.py -k structure` | moyenne | oui (BIC + silhouette) | `test_segmentation_une_seule_famille_si_pas_de_structure` |
| 7 | V2 | Projection fixée sur la baie de démo : données réelles (ex. Danemark) déformées de ~20 % | `pytest tests/test_v2_sources_web.py -k projection` | haute | oui (origine par jeu de données) | `test_projection_locale_aller_retour` |
| 8 | V2 | A priori de densité du modèle appris appliqué à chaque pas : effet cumulé trop fort (S6 ≈ 0 %) | `python eval/comparer_modeles.py --rapide` | moyenne | oui (exposant réglé sur le jeu de réglage, 0,1) | réglage publié dans `comparatif.json` |
| 9 | V2 | Le pont brownien échoue quand une île barre la ligne directe | `python scripts/comparer.py --trajet S6-ambigu --zone Z1` | faible (limite du modèle) | non : échec déclaré, jamais de résultat rassurant | — |

## Corrections V2.1 (bateaux de test Kattegat)

- **Règle de rejeu** : fausses alertes et lenteur (55 s) sur des traces AIS réelles. Texture jugée sur la vitesse (m/s) et appariement vectorisé (cKDTree) ; seuil `rejeu_texture_min_ms = 0.2` (choisi sur le rappel synthétique, vérifié sur les 3 traces réelles : faible risque de surajustement).
- **Reconstruction sans zone** : plantage (`np.mean` sur None) dans `notes_comparatif` — corrigé, test ajouté.
- **Boosting** : renvoyait un nombre même quand le HMM ne trouvait aucun chemin faisable ; il déclare maintenant l'échec.
- **Pandas 3** : décimation des horodatages en µs au lieu de s — utiliser `.dt.total_seconds()`.
- Nouveau jeu « Kattegat test boats A/B/C » (`seatrace/kattegat_test.py`), évaluation : `python eval/evaluer_bateaux_test.py` ; métrique « temps attendu en zone ».
