"""Re-tune the boosting hyperparameters with Optuna (without re-running the whole comparison).

    python scripts/regler_boosting.py              # 40 trials (config: modeles.boosting.essais_optuna)
    python scripts/regler_boosting.py --trials 100

Reads data/modeles/boosting_entrainement.csv (written by eval/comparer_modeles.py), minimises the Brier
score in 5-fold cross-validation GROUPED BY BOAT, and writes data/modeles/boosting_params.json, which the
boosting model reads at start-up (Optuna is not needed to use the result).
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from seatrace import boosting as BO, config  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    params = config.charger()
    pb = params["modeles"]["boosting"]
    donnees = pd.read_csv(RACINE / pb["fichier_entrainement"])
    n = args.trials or int(pb["essais_optuna"])
    r = BO.regler(donnees, n_essais=n, graine=args.seed, hp_reference=BO.hp_depuis_config(pb))
    (RACINE / pb["fichier_reglage"]).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    gain = 100 * (r["reference_cv_brier"] - r["best_cv_brier"]) / r["reference_cv_brier"]
    print(f"{r['n_trials']} trials, CV Brier {r['reference_cv_brier']} -> {r['best_cv_brier']} ({gain:.0f} % lower)")
    print("best hyperparameters:", json.dumps(r["best_params"]))


if __name__ == "__main__":
    main()
