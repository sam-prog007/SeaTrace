"""Journal local des imports, analyses, changements de paramètres et décisions.

Chaque entrée contient l'empreinte de l'entrée précédente (chaînage SHA-256).
Ce que cela apporte : une modification ou suppression isolée d'une entrée est
détectée à la vérification.
Ce que cela N'apporte PAS : quelqu'un qui contrôle la machine peut réécrire tout
le journal et recalculer la chaîne. Le journal n'est donc pas « inviolable ».
Pour s'en rapprocher, il faudrait ancrer régulièrement la dernière empreinte
hors de la machine (horodatage qualifié, dépôt chez un tiers) : voir la feuille
de route.

Les données personnelles sont minimisées : on journalise des identifiants de
dossier et des empreintes, pas les positions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

GENESE = "0" * 64


def _hash_entree(entree: dict) -> str:
    contenu = {k: v for k, v in entree.items() if k != "hash"}
    texte = json.dumps(contenu, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(texte.encode("utf-8")).hexdigest()


class Journal:
    def __init__(self, chemin: Path):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)

    def entrees(self) -> list:
        if not self.chemin.exists():
            return []
        out = []
        with open(self.chemin, encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.strip()
                if ligne:
                    out.append(json.loads(ligne))
        return out

    def dernier_hash(self) -> str:
        e = self.entrees()
        return e[-1]["hash"] if e else GENESE

    def ajouter(self, type_evenement: str, details: dict) -> dict:
        entree = {
            "horodatage": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "type": type_evenement,
            "details": details,
            "hash_precedent": self.dernier_hash(),
        }
        entree["hash"] = _hash_entree(entree)
        with open(self.chemin, "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")
        return entree

    def verifier(self) -> tuple[bool, int | None, str]:
        """Retourne (valide, indice de la première entrée incohérente, message)."""
        try:
            entrees = self.entrees()
        except json.JSONDecodeError:
            return False, None, "Journal illisible (ligne JSON corrompue)."
        precedent = GENESE
        for i, e in enumerate(entrees):
            if e.get("hash_precedent") != precedent:
                return False, i, f"Chaînage rompu à l'entrée {i}."
            if _hash_entree(e) != e.get("hash"):
                return False, i, f"Contenu modifié à l'entrée {i}."
            precedent = e["hash"]
        return True, None, f"{len(entrees)} entrée(s), chaînage cohérent."
