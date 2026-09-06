"""Échelle de stratégies d'exécution — EXTRACT du CONCEPT de
modules/pc_control/router.py + executor.py (classé EXTRACT dans
RAYA_V2_MIGRATION_MAP.md #51/#78 : "échelle DOM→UIA→vision"), pas la classe
V1. Ici : UIA d'abord (le plus fiable, action par pattern) → clic souris en
repli, MAIS uniquement sur un rectangle réellement résolu par UIA (jamais une
coordonnée devinée). Décide COMMENT agir sur une cible déjà choisie par
l'appelant (`tools/catalog/pc.py`, qui reçoit l'objectif de Cognition) —
décide zéro fois QUOI cliquer.

Vision explicitement NON implémentée en Phase 4 (limitation documentée,
RAYA_V2_PHASE4_IMPLEMENTATION_REPORT.md §"Known limitations") : un vrai
fallback vision nécessiterait que le Device Agent remonte une demande
structurée à Harness/Cognition pour un appel modèle (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.13 — un Device Agent n'appelle jamais lui-même `models`), ce qui est une
intégration Harness↔Device au-delà du périmètre "petit nombre de capacités
réelles" (consigne §38) choisi pour cette phase.
"""

from __future__ import annotations

from .mechanisms import keyboard as _keyboard
from .mechanisms import mouse as _mouse
from .mechanisms import uia as _uia


def click_element(window: str, selector: dict) -> dict:
    """UIA invoke d'abord ; repli clic souris sur le rectangle réel résolu
    par UIA (get_center) si invoke n'a pas pu activer l'élément."""
    result = _uia.invoke(window, selector)
    if result.get("status") == "not_found":
        return {"status": "not_found", "method": "uia", "detail": result}
    if result.get("invoked"):
        return {"status": "ok", "method": "uia_invoke", "verified": result.get("verified"), "detail": result}

    center = _uia.get_center(window, selector)
    if center.get("status") == "ok" and center.get("found"):
        clicked = _mouse.click_at(center["x"], center["y"])
        return {"status": "ok" if clicked else "error", "method": "mouse_at_uia_rect",
                "x": center["x"], "y": center["y"], "verified": None}

    return {"status": "error", "method": "none", "reason": "élément introuvable (UIA et repli souris)",
            "uia_detail": result}


def type_into_element(window: str, selector: dict, text: str) -> dict:
    """UIA ValuePattern d'abord (le plus fiable) ; repli clic-pour-focus puis
    frappe clavier réelle."""
    result = _uia.set_value(window, selector, text)
    if result.get("status") == "not_found":
        return {"status": "not_found", "method": "uia", "detail": result}
    if result.get("set"):
        return {"status": "ok", "method": "uia_set_value", "detail": result}

    center = _uia.get_center(window, selector)
    if center.get("status") == "ok" and center.get("found"):
        _mouse.click_at(center["x"], center["y"])
        ok = _keyboard.type_text(text)
        return {"status": "ok" if ok else "error", "method": "click_then_keyboard",
                "x": center["x"], "y": center["y"]}

    return {"status": "error", "method": "none", "reason": "élément introuvable (UIA et repli clavier)",
            "uia_detail": result}
