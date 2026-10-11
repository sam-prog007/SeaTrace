# SeaTrace

**Check the integrity of vessel tracks and estimate, with explicit uncertainty, where a vessel may have gone
when its positions are missing or have been altered.**

Prototype built for the Secure Horizons 2026 hackathon (ESSEC), Open Track "AI × Cybersecurity, defence side".

> ⚠️ **An investigation aid, not proof.** SeaTrace never proves fraud and never decides on a penalty or a claim
> refusal. The models are calibrated on synthetic data only.

---

## The problem

An insurer, a rental company or an authority sometimes needs to know where a vessel really went. But the
positions it transmits (AIS, tracker) can be **missing** (coverage, battery, transponder switched off) or
**altered** (shifted, replayed, cloned identity). The question is:

> "During this silence, could the vessel have entered a restricted zone? With what probability, and under which
> assumptions?"

## What SeaTrace does

1. **Checks the integrity of the positions**: impossible jumps, inconsistent speed and course, replays, the same
   identity seen in two places, signal gaps. Each alert is explained, together with the other possible causes.
2. **Reconstructs the possible paths** during a gap with **five models** that account for coastlines, current and
   wind, then estimates the **probability of passing through a regulated zone**.
3. **Gives an anomaly score from 0 to 100** to every trip and every vessel of the fleet, so you know what to look
   at first. The same score is used when analysing one trip and in the fleet view.

All of this in an English web dashboard designed for non-technical users.

---

## Quick start

Python 3.10 or later. The commands below are for Windows (PowerShell); on macOS / Linux, replace
`.venv\Scripts\activate` with `source .venv/bin/activate`, and use `python3` to create the environment.

```powershell
git clone https://github.com/sam-prog007/Seatrace-Hackathons.git
cd Seatrace-Hackathons
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -r requirements-data.txt
```

**Prepare the local data** (once, about a minute; these files are not on GitHub). The order matters: the first
export downloads the coastline map that the next two scripts need.

```powershell
python src/13_seatrace_export.py    # coastline, zones and test boats in SeaTrace format
python src/10_meteo.py              # real wind and waves (Open-Meteo)
python src/12_simulate_context.py   # demonstration current (simulated, labelled as such)
python src/13_seatrace_export.py    # same export, now with current and wind
```

**Start the dashboard:**

```powershell
python serveur.py                   # then open http://127.0.0.1:8050
```

If an old version shows up: stop every server (`Get-Process python | Stop-Process -Force` on Windows), start it
again, then reload the page with **Ctrl+Shift+R**. `python serveur.py --port 8090` starts the server on another
port.

---

## Using the dashboard

| Tab | What it is for |
|---|---|
| **Investigate** | Pick a dataset and a vessel (★ to keep it as a favourite), see its alerts and score, choose a gap and a zone, then **Reconstruct**: the five models side by side, path families, every path, the decision and the exports (HTML, JSON, CSV). |
| **Fleet** | Every vessel of the Kattegat fleet, scored the same way and sorted from most to least suspicious, with the share of the fleet flagged. One click opens the vessel in Investigate. |
| **Kattegat map** | The team's study map: real AIS tracks of fishing vessels, regulated zones, current, wind and waves, and SeaTrace results vessel by vessel. |
| **Method** | Model evaluation, data sources, action log. |

### The anomaly score (0–100)

The box goes from **green (0)** to **red (100)**. The score combines three signals (`seatrace/score.py`):

| Signal | Max weight | Detail |
|---|---|---|
| Integrity alerts | 100 | Position jump 30, speed/course inconsistency 20, drift 20, replay 40, duplicated identity 40 |
| Silence while moving | 40 (at 6 h) | Longest gap during which the vessel moved more than 1 km; a vessel that stayed in port or at anchor does not count |
| Likely passage through a zone | 80 | Probability estimated by the models, known after **Reconstruct** (or from the fleet batch run) |

`score = 100 × (1 − (1 − integrity) × (1 − silence) × (1 − zone))`. A vessel is **flagged** from 50 upwards.
The score says what to look at first; it does not measure anyone's guilt.

### Datasets

| Dataset | Content |
|---|---|
| **Demo (fictitious bay)** | Seven synthetic trips in an invented bay (normal trip, detour, lost reception, falsification, replay…). |
| **Kattegat test boats** | Four test boats with fictitious MMSIs (999…), each with a single simulated event to find. |
| **Kattegat fishing fleet** | The 208 real fishing vessels of the map (Danish AIS, 15–17 September 2026). |
| **Kattegat map export** | The same data as the map, in SeaTrace format (created by `src/13_seatrace_export.py`). |

**Test boats** (the answer key is only read by the evaluation, never by the detector):

| Boat | Simulated event | What SeaTrace shows |
|---|---|---|
| **A** | Transponder off for 45 min while crossing a closed zone | Silence found within 5 s; passage through the zone ≈ 100 % |
| **B** | Positions shifted by 10 km for 1 h | Positions set aside, then the path is reconstructed (0.03–0.5 km error); score 100 |
| **C** | Innocent signal loss, far from any zone | Gap found, no zone within reach |
| **D** | 5 h 30 gap right after leaving the Limfjord, near zone DK-MSFD-A; the real path trawls inside it | The models disagree (0 % to 77 %): a genuinely uncertain case |

A, B and C are copies of real trawler tracks (`src/synthetic.py`); D is fully simulated (`src/boat_d.py`).

---

## The models

The five models run on the same gap and are **compared, never averaged** in the analysis: the tool shows how far
apart they are and says when they disagree. Details: [`docs/MODELES.md`](docs/MODELES.md).

| Model | Idea | Brier* |
|---|---|---|
| Grid HMM with persistent heading | Grid around the gap; the vessel tends to keep its course; exact passage probability | 0.070 |
| Learned from historical tracks | Same grid, speeds and turns learned from real trips | 0.087 |
| Particle filter + smoothing | Thousands of simulated paths, kept according to their consistency | 0.077 |
| Brownian bridge | Random walk between the two known positions | 0.108 |
| Stochastic boosting | Predicts the probability directly from features (tuned with Optuna) | 0.050 |

\* Brier score on 159 gaps of unseen synthetic boats (lower is better; straight-line interpolation scores 0.126).
These figures come from synthetic data and are not a validation on real data.

The paths of all models are grouped into **families** ("to the north, through the zone"…) with PCA, and every
generated path is kept with its probability.

---

## Data

- **Demonstration**: synthetic trips and fictitious geography (`data/demo/`).
- **Real Kattegat AIS**: public daily files from the Danish Maritime Authority, filtered to fishing vessels; zones
  of Delegated Regulation (EU) 2022/952; Natural Earth coastline; Open-Meteo wind and waves; Copernicus Marine
  current (or a simulated one when unavailable, labelled as such). Full pipeline (`src/01` to `src/13`):
  [`docs/PIPELINE_KATTEGAT.md`](docs/PIPELINE_KATTEGAT.md).
- **Other open sources that can be imported** (NOAA AIS, zone GeoJSON, satellite detections…):
  [`docs/DONNEES_OUVERTES.md`](docs/DONNEES_OUVERTES.md).

Large data (`data/raw`, `data/interim`, `data/processed`, `data/ais`…) can be regenerated and is not versioned.

---

## Repository layout

```
serveur.py            starts the dashboard (Flask), http://127.0.0.1:8050
web/                  JSON API, HTML page, JavaScript and styles (Leaflet and Chart.js bundled)
seatrace/             core: safe import, detection, five models, score, segmentation, reports, log
src/                  Kattegat data pipeline (01 to 13) and test boats (synthetic.py, boat_d.py)
eval/                 evaluation of the models and the test boats
scripts/              command-line tools (AIS import, comparison, boosting tuning)
config/               versioned parameters (config/parametres.json)
data/                 demo, test boats, reference zones and coastline, learned models
reports/              Kattegat map and data quality reports
tests/                unit, security and web API tests
docs/                 detailed documentation (see below)
app.py                former Streamlit interface (V1): streamlit run app.py
```

## Tests and evaluation

```powershell
python -m pytest                      # ~110 tests: models, import security, web API, score
python eval/comparer_modeles.py       # comparison of the 5 models (~6 min; --rapide: ~3 min)
python eval/evaluer_kattegat.py       # test boats run through the algorithm
python src/13_reconstruct.py          # recomputes the screening of the whole fleet (~5 min)
```

---

## Limitations and next steps

- Models calibrated on synthetic data only; they need recalibrating on a season of real data.
- Gaps longer than 6 h are not reconstructed (no extrapolation).
- The fleet screening is a batch run; a continuously running service is still to be built.
- Before a pilot: GDPR, authentication and client isolation, official source for the zones.

## Documentation

The detailed documents are in French.

| Document | Content |
|---|---|
| [`docs/MODELES.md`](docs/MODELES.md) | The five models, their comparison, path families |
| [`docs/CARTE_ALGORITHME.md`](docs/CARTE_ALGORITHME.md) | Running the algorithm on the Kattegat map |
| [`docs/PIPELINE_KATTEGAT.md`](docs/PIPELINE_KATTEGAT.md) | Real-data pipeline, step by step |
| [`docs/DONNEES_OUVERTES.md`](docs/DONNEES_OUVERTES.md) · [`docs/DONNEES.md`](docs/DONNEES.md) | Data sources and format |
| [`docs/PARAMETRES.md`](docs/PARAMETRES.md) | Parameters and assumptions |
| [`docs/SECURITE.md`](docs/SECURITE.md) | Tested protections |
| [`docs/DEFENSE.md`](docs/DEFENSE.md) | Project defence sheet |
| [`docs/ETUDE_MARCHE.md`](docs/ETUDE_MARCHE.md) · [`docs/PILOTES.md`](docs/PILOTES.md) | Market, competition, pilots |
| [`docs/PITCH.md`](docs/PITCH.md) · [`docs/VIDEO.md`](docs/VIDEO.md) | Pitch and video |
| [`docs/EQUIPE.md`](docs/EQUIPE.md) | Team, roles, schedule |
