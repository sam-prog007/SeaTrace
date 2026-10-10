"""Segmentation of paths into FAMILIES with a principal component analysis (PCA).

Idea: thousands of sampled paths often tell only two or three stories ("north of the island",
"south, crossing the zone", "anchoring then back"). Grouping them gives the analyst one probability
PER FAMILY, which is easier to read than a cloud of lines.

Method:
1. each path is resampled at `nb_points` interior instants and expressed in the frame of the direct
   line start -> end: advance along the line and lateral offset (left of the direction of travel > 0);
2. weighted PCA over the paths of all models (each model weighs the same), keeping the components that
   explain 90 % of the variance (5 at most);
3. number of families chosen with the BIC of a Gaussian mixture (a continuous cloud with no structure
   stays ONE family: we do not manufacture artificial stories), confirmed by the silhouette score;
   paths are then assigned by weighted k-means;
4. for each family and each model: probability = sum of the weights of the model's paths in the family.
   Families are shared, so models are directly comparable.
5. an INTERPRETATION of each retained component is produced from fixed rules (correlation of the
   component scores with simple path descriptors): what a high or low score means physically.
"""

from __future__ import annotations

import math

import numpy as np

CARDINALS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]


def _frame(start, end):
    ux, uy = end.x - start.x, end.y - start.y
    L = math.hypot(ux, uy)
    if L < 1e-6:
        ux, uy, L = 1.0, 0.0, 1.0
    ux, uy = ux / L, uy / L
    return ux, uy, -uy, ux, L


def vectors(paths, start, end, n_points):
    ux, uy, nx_, ny_, _ = _frame(start, end)
    f = np.linspace(0, 1, n_points + 2)[1:-1]
    out = np.zeros((len(paths), 2 * n_points))
    for i, c in enumerate(paths):
        k = np.linspace(0, 1, len(c.xs))
        x = np.interp(f, k, c.xs) - start.x
        y = np.interp(f, k, c.ys) - start.y
        out[i, :n_points] = x * ux + y * uy
        out[i, n_points:] = x * nx_ + y * ny_
    return out


def _cardinal(dx, dy):
    a = math.degrees(math.atan2(dy, dx)) % 360
    return CARDINALS[int(((a + 22.5) % 360) // 45)]


def _wcorr(a, b, w):
    ma, mb = np.average(a, weights=w), np.average(b, weights=w)
    va = np.average((a - ma) ** 2, weights=w)
    vb = np.average((b - mb) ** 2, weights=w)
    if va <= 1e-12 or vb <= 1e-12:
        return 0.0
    return float(np.average((a - ma) * (b - mb), weights=w) / math.sqrt(va * vb))


def _silhouette_word(s):
    if s is None:
        return "no clear separation"
    if s >= 0.7:
        return "very clear separation"
    if s >= 0.5:
        return "clear separation"
    if s >= 0.25:
        return "weak separation"
    return "no real separation"


def interpret_components(X, Z, ratio, q, weights, K, L, families, bics, silhouettes, selected_silhouette):
    """Fixed-rule reading of the retained components. Returns (interpretation, sign flips)."""
    lat = X[:, K:]
    adv = X[:, :K]
    f = (np.arange(1, K + 1) / (K + 1))[None, :]
    peak_idx = np.argmax(np.abs(lat), axis=1)
    features = {
        "lateral_side": ("which side of the straight line the path takes", lat.mean(axis=1),
                         "passes on the LEFT of the straight start→end line (looking in the direction of travel)",
                         "passes on the RIGHT of the straight start→end line"),
        "bulge": ("how far the path bulges away from the straight line", np.abs(lat).max(axis=1),
                  "makes a WIDER detour away from the straight line",
                  "stays CLOSER to the straight line"),
        "timing": ("when the path advances along the line", (adv - f * L).mean(axis=1),
                   "advances EARLY (far along the line before mid-time) then slows",
                   "advances LATE (lingers near the start, then rushes)"),
        "wiggle": ("how irregular the path is", lat.std(axis=1),
                   "is more IRREGULAR (changes side or meanders)", "is SMOOTHER"),
    }
    flips = np.ones(q)
    comps = []
    for k in range(q):
        corr = {name: _wcorr(Z[:, k], v[1], weights) for name, v in features.items()}
        best = max(corr, key=lambda n: abs(corr[n]))
        r = corr[best]
        if r < 0:  # PCA signs are arbitrary: orient each component so that its main meaning reads positive
            flips[k] = -1.0
            corr = {n: -c for n, c in corr.items()}
            r = -r
        title, _, high, low = features[best]
        strength = "strongly" if abs(r) >= 0.8 else ("moderately" if abs(r) >= 0.5 else "only loosely")
        comps.append({
            "name": f"PC{k + 1}", "variance_pct": round(100 * float(ratio[k]), 1),
            "main_descriptor": best, "correlation": round(float(r), 2),
            "correlations": {n: round(float(c), 2) for n, c in corr.items()},
            "meaning": f"PC{k + 1} ({100 * ratio[k]:.0f} % of the variance) is {strength} tied to {title} "
                       f"(correlation {r:+.2f}).",
            "high_means": f"A high PC{k + 1} score: the path {high}.",
            "low_means": f"A low (negative) PC{k + 1} score: the path {low}.",
        })
    cum = float(np.sum(ratio[:q]))
    reading = [
        "Each dot in the scatter plot is one path; it is placed by its scores on the first two components. "
        "Paths that look alike lie close together; clusters are the families.",
        f"{q} component(s) are kept because together they explain {100 * cum:.0f} % of how the paths differ "
        f"from each other; the first two explain {100 * float(np.sum(ratio[:2])):.0f} %.",
        "The sign of a component is arbitrary in a PCA; here each one is oriented so that its main meaning "
        "(see the table) reads positive.",
        "Distances in this plane are in kilometres of path difference, not probabilities. The probability of a "
        "family comes from the weight of its paths (bar chart), not from how large the cluster looks.",
    ]
    if len(families) == 1:
        reading.append("A single family was kept: the paths form one continuous cloud with no separate groups "
                       "(BIC and silhouette found no better split). The uncertainty is a spread around one route, "
                       "not a choice between routes.")
    else:
        reading.append(f"{len(families)} families were kept: the BIC of a Gaussian mixture is lowest for "
                       f"{len(families)} groups and the silhouette score is "
                       f"{selected_silhouette:.2f} ({_silhouette_word(selected_silhouette)}). "
                       "The uncertainty is mainly a choice between routes.")
    sides = [fam for fam in families if abs(fam["mean_lateral_offset_km"]) >= 0.3]
    if len(sides) >= 2:
        lefts = [fam["id"] for fam in sides if fam["mean_lateral_offset_km"] > 0]
        rights = [fam["id"] for fam in sides if fam["mean_lateral_offset_km"] < 0]
        if lefts and rights:
            reading.append(f"{', '.join(lefts)} pass on the left of the straight line and {', '.join(rights)} on the "
                           "right: the first component separates them.")
    return {"components": comps, "reading": reading, "cumulative_variance_pct": round(100 * cum, 1)}, flips


def segment(paths_by_model: dict, start, end, params, seed=0):
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score
    from sklearn.mixture import GaussianMixture

    ps = params["modeles"]["segmentation"]
    K = int(ps["nb_points"])
    allp, weights, models = [], [], []
    active = {m: cs for m, cs in paths_by_model.items() if cs}
    if not active:
        return None
    for m, cs in active.items():
        s = sum(c.poids for c in cs) or 1.0
        for c in cs:
            allp.append(c)
            weights.append(c.poids / s / len(active))
            models.append(m)
    weights = np.asarray(weights)
    X = vectors(allp, start, end, K)
    mean = np.average(X, axis=0, weights=weights)
    Xc = X - mean
    U, S, Vt = np.linalg.svd(Xc * np.sqrt(weights)[:, None], full_matrices=False)
    var = S ** 2
    ratio = var / var.sum() if var.sum() > 0 else np.zeros_like(var)
    q = int(np.clip(np.searchsorted(np.cumsum(ratio), 0.9) + 1, 1, min(5, len(ratio))))
    Z = Xc @ Vt[:q].T
    best_k, best_s, labels = 1, None, np.zeros(len(allp), int)
    rng = np.random.default_rng(seed)
    sample = rng.choice(len(Z), size=min(len(Z), 1500), replace=False)
    scores, bics = {}, {}
    if len(Z) >= 10 and ratio[0] > 0:
        # sample drawn according to the weights (the Gaussian mixture accepts no weights)
        Zb = Z[rng.choice(len(Z), size=min(1500, 3 * len(Z)), p=weights / weights.sum())]
        Zb = Zb + rng.normal(0, 1e-6 * (Zb.std() + 1e-12), Zb.shape)
        for k in range(1, int(ps["familles_max"]) + 1):
            if k >= len(Zb):
                break
            gm = GaussianMixture(n_components=k, covariance_type="full", n_init=2, random_state=seed,
                                 reg_covar=1e-4 * float(np.var(Zb)) + 1e-9).fit(Zb)
            bics[k] = float(gm.bic(Zb))
        k_bic = min(bics, key=bics.get) if bics else 1
        for k in range(2, k_bic + 1):
            km = KMeans(n_clusters=k, n_init=5, random_state=seed).fit(Z, sample_weight=weights)
            lab = km.labels_
            if len(np.unique(lab[sample])) < 2:
                continue
            s = float(silhouette_score(Z[sample], lab[sample]))
            scores[k] = round(s, 3)
            # a tiny family (< 2 % of the mass) is not a distinct story
            masses = np.bincount(lab, weights=weights, minlength=k)
            if masses.min() < 0.02:
                continue
            if s >= float(ps["silhouette_min"]) and (best_s is None or s > best_s + 0.02):
                best_k, best_s, labels = k, s, lab
    masses = np.bincount(labels, weights=weights, minlength=best_k)
    order = np.argsort(-masses)
    renum = {int(old): new for new, old in enumerate(order)}
    labels = np.array([renum[int(e)] for e in labels])
    ux, uy, nx_, ny_, L = _frame(start, end)
    families = []
    for f in range(best_k):
        sel = labels == f
        w = weights[sel]
        lat = X[sel, K:]
        lat_mean = float(np.average(lat.mean(axis=1), weights=w))
        lat_peak = float(np.average(lat[:, np.argmax(np.abs(lat).mean(axis=0))], weights=w))
        ox, oy = lat_peak * nx_, lat_peak * ny_
        if abs(lat_peak) < 0.3:
            desc = "close to the direct line (less than 300 m away)"
        else:
            desc = f"up to {abs(lat_peak):.1f} km {_cardinal(ox, oy)} of the direct line"
        xs_m, ys_m = np.zeros(K + 2), np.zeros(K + 2)
        av = np.average(X[sel, :K], axis=0, weights=w)
        la = np.average(X[sel, K:], axis=0, weights=w)
        xs_m[1:-1] = start.x + av * ux + la * nx_
        ys_m[1:-1] = start.y + av * uy + la * ny_
        xs_m[0], ys_m[0], xs_m[-1], ys_m[-1] = start.x, start.y, end.x, end.y
        by_model = {}
        for m, cs in active.items():
            mass = sum(c.poids for c, e, mm in zip(allp, labels, models) if mm == m and e == f)
            tot = sum(c.poids for c in cs) or 1.0
            by_model[m] = round(mass / tot, 4)
        crossing = [c.passe_zone for c, e in zip(allp, labels) if e == f and c.passe_zone is not None]
        families.append({
            "id": f"F{f + 1}", "description": desc,
            "shared_probability": round(float(w.sum() / weights.sum()), 4),
            "probability_by_model": by_model,
            "share_crossing_zone": round(float(np.mean(crossing)), 3) if crossing else None,
            "mean_lateral_offset_km": round(lat_mean, 3),
            "lateral_spread_km": round(float(np.sqrt(np.average((lat.mean(axis=1) - lat_mean) ** 2, weights=w))), 3),
            "n_paths": int(sel.sum()),
            "mean_path": {"xs": xs_m.tolist(), "ys": ys_m.tolist()},
        })
    for c, e in zip(allp, labels):
        c.famille = f"F{e + 1}"
    interpretation, flips = interpret_components(X, Z, ratio, q, weights, K, L, families, bics, scores, best_s)
    Zo = Z * flips[None, :]
    cloud = []
    for m in active:
        idx = [i for i, mm in enumerate(models) if mm == m]
        idx = idx if len(idx) <= 300 else list(rng.choice(idx, 300, replace=False))
        for i in idx:
            cloud.append({"model": m, "pc1": round(float(Zo[i, 0]), 4),
                          "pc2": round(float(Zo[i, 1]), 4) if q > 1 else 0.0,
                          "family": f"F{labels[i] + 1}", "path": allp[i].identifiant})
    return {
        "families": families, "n_families": best_k,
        "explained_variance": [round(float(v), 4) for v in ratio[:5]],
        "retained_components": q, "silhouettes": scores,
        "selected_silhouette": best_s, "pca_cloud": cloud,
        "bic": {k: round(v, 1) for k, v in bics.items()},
        "interpretation": interpretation,
        "method": "Weighted PCA (frame of the direct line); number of families chosen by the BIC of a Gaussian "
                  "mixture and the silhouette score; assignment by k-means",
    }


# French-named alias kept so older callers keep working
segmenter = segment
