# Données ouvertes : télécharger, importer, utiliser

> Aucune donnée réelle n'est livrée dans le dépôt (les fichiers convertis vont dans `data/ais/`, ignoré par git).
> Les licences ci-dessous sont un rappel : **vérifier les conditions sur le site de chaque source** avant tout
> usage, en particulier commercial. Liste complète et statut réel : onglet « Sources de données » de
> l'interface, ou `seatrace/catalogue.py`.

## 1. Trajectoires AIS

### Autorité maritime danoise (DMA)
- Page : https://www.dma.dk/safety-at-sea/navigational-information/ais-data — fichiers quotidiens
  `aisdk-AAAA-MM-JJ.zip` (plusieurs centaines de Mo, toutes les eaux danoises).
- Colonnes reconnues : `# Timestamp` (JJ/MM/AAAA HH:MM:SS, UTC), `Type of mobile`, `MMSI`, `Latitude`,
  `Longitude`, `SOG`, `COG`, `Name`, `Ship type`. Seules les positions `Class A`/`Class B` sont gardées
  (stations de base et balises écartées) ; `Sailing` → voilier, `Pleasure` → plaisance, `Fishing` → pêche.
- Conversion (lecture par morceaux, filtrage avant chargement) :
  ```bash
  python scripts/importer_ais.py aisdk-2024-06-15.zip --bbox 54.6 55.4 10.2 11.2 --types ais_voilier ais_plaisance
  ```

### NOAA / MarineCadastre (États-Unis)
- Page : https://www.coast.noaa.gov/digitalcoast/data/vesseltraffic.html — fichiers quotidiens
  `AIS_AAAA_MM_JJ.zip`.
- Colonnes reconnues : `MMSI, BaseDateTime, LAT, LON, SOG, COG, VesselName, VesselType` et la variante en
  minuscules (`mmsi, base_date_time, latitude, longitude, …`). Codes 36 voile, 37 plaisance, 30 pêche.
- `python scripts/importer_ais.py AIS_2024_01_01.zip --bbox 29 31 -91 -88 --types ais_plaisance`

Dans les deux cas : SOG 102,3 et COG 360 (« non disponible ») sont neutralisés, un trajet = un MMSI sans
interruption de plus de 30 min, positions espacées d'au moins 30 s, MMSI à 9 chiffres uniquement, noms nettoyés.
Le fichier produit (`data/ais/<nom>.csv` + `.json` des bateaux) repasse par l'import sécurisé et apparaît dans la
liste « Jeu de données » de l'interface. Un petit fichier peut aussi être importé directement dans l'interface
(détection automatique du format).

**Attention** : les petites embarcations de location n'émettent souvent **pas** d'AIS. Les données AIS servent
à tester la détection et la reconstruction sur des trajectoires réelles et à apprendre le modèle appris :
```bash
python scripts/apprendre_historique.py --fichier data/ais/aisdk-2024-06-15.csv --sortie data/modeles/appris_dma.npz
```
puis `modeles.appris.fichier` dans `config/parametres.json`.

## 2. Terres et zones (GeoJSON)

| Usage | Source | Comment l'obtenir |
|---|---|---|
| Terres fines | OpenStreetMap land polygons (osmdata.openstreetmap.de) | Shapefile → `ogr2ogr -f GeoJSON -clipsrc 10.2 54.6 11.2 55.4 terres.geojson land_polygons.shp` |
| Terres (démo) | Natural Earth `ne_10m_land` | idem ; précision insuffisante dans les passages étroits |
| Limites maritimes FR | Shom — délimitations maritimes (Licence Ouverte 2.0) | export GeoJSON depuis data.shom.fr |
| Zones de pêche | CNSP — réglementation des pêches cartographiée (data.gouv.fr) | GeoJSON ; vérifier dates et textes |
| Aires protégées | Protected Planet / WDPA | non commercial sans autorisation |

Import : section 3 de l'interface (« Terres » ou « Zones »). Polygon et MultiPolygon sont lus (anneaux
extérieurs ; les trous sont ignorés), projetés dans le repère local du jeu de données et **découpés sur
l'emprise d'étude** ; maximum 20 Mo et 2 millions de sommets (découper au préalable avec `-clipsrc`). Le nom de
chaque zone vient des propriétés `nom`, `name`, `NAME`, `libelle`… On peut aussi **dessiner une zone** sur la
carte (aucune valeur réglementaire). Rappel : une limite maritime ou une interdiction de pêche n'est pas une
interdiction de passage.

## 3. Densité de trafic EMODnet

Cartes de densité (heures de présence par case), agrégées : pas de trajectoires individuelles. Convertir la
GeoTIFF en XYZ (`gdal_translate -of XYZ densite.tif densite.xyz`) ; `sources.lire_densite_xyz` la lit comme
a priori de fréquentation (même rôle que la densité apprise du modèle appris). Licence à vérifier sur le produit.

## 4. Courants Copernicus Marine

Exporter `uo`/`vo` (m/s) en CSV sur l'emprise et la période (`copernicusmarine subset … --output-filetype csv`),
puis l'importer comme couche « Courant » : le courant moyen (km/h) est ajouté aux déplacements du HMM, du modèle
appris et du filtre particulaire (sans effet sur le pont brownien, fixé à ses deux extrémités). On peut aussi
saisir le courant à la main. Gratuit, y compris commercial, avec les mentions requises.

## 5. Observations externes (Sentinel-1/2, Global Fishing Watch SAR, RF)

CSV : `obs_id, timestamp, lat, lon, precision_m, proba_attribution, source[, trip_id]` avec `source` parmi
`sentinel1, sentinel2, gfw_sar, rf, rf_simulee, autre`. Une détection n'est **jamais attribuée d'office** au
bateau : elle entre dans les modèles avec la probabilité `proba_attribution` (sinon, elle n'apporte aucune
information). La détection des petites embarcations sur Sentinel-1/2 n'est pas garantie (taille, nuages,
revisite).

## 6. Sources proposées en plus

Kystverket (AIS Norvège), Digitraffic (AIS Finlande, CC BY 4.0), GSHHG (trait de côte haute résolution),
GEBCO / EMODnet Bathymétrie (hauts-fonds selon le tirant d'eau), Marine Regions (ZEE mondiales), ERA5 (vent,
utile pour les voiliers), Global Fishing Watch SAR (navires sans AIS). Statut : feuille de route ou import manuel
(voir le catalogue).
