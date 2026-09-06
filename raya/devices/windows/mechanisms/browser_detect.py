"""Détection du navigateur par défaut Windows — passe "Targeted Fix" (Sujet
3 : "RAYA privilégie Edge alors que le navigateur personnel de l'utilisateur
est Chrome"). Mécanisme GÉNÉRIQUE, purement informationnel : lit le choix
utilisateur réel via le registre Windows, jamais une préférence codée en dur
pour un navigateur particulier.

DÉLIBÉRÉMENT NON câblé à une décision de comportement dans cette passe — le
Browser Device Agent continue d'utiliser EXCLUSIVEMENT un profil Edge dédié
isolé (`devices/browser/session.py`, choix Phase 4 documenté et justifié :
garantit que RAYA ne touche jamais le navigateur/les sessions personnelles de
l'utilisateur). Une vraie distinction PERSONAL_BROWSER vs AUTOMATION_BROWSER
nécessiterait de décider COMMENT piloter en sécurité le navigateur personnel
sans risquer de corrompre son profil — décision volontairement hors périmètre
de cette passe (consigne explicite). Cette fonction ne fait qu'exposer le
FAIT observable "quel est le navigateur par défaut de cet utilisateur",
réutilisable plus tard par Cognition/le modèle si une telle distinction est
un jour construite — jamais une importation depuis devices/browser/."""

from __future__ import annotations

# ProgId Windows connus -> nom générique. Purement une table de LECTURE
# (traduction d'un identifiant technique en nom lisible), jamais une
# préférence — n'importe quel ProgId inconnu est renvoyé tel quel plutôt que
# de faire échouer la détection.
_KNOWN_PROG_IDS = {
    "MSEdgeHTM": "edge",
    "ChromeHTML": "chrome",
    "FirefoxURL": "firefox",
    "FirefoxURL-308046B0AF4A39CB": "firefox",
    "BraveHTML": "brave",
    "OperaStable": "opera",
    "AppXq0fevzme2pys62n3e0fbqa7peapykr8v": "edge",  # Edge UWP (versions anciennes)
}


def detect_default_browser() -> str | None:
    """Lit le choix RÉEL de l'utilisateur (registre Windows, jamais une
    supposition) pour les liens http/https. Retourne un nom générique connu
    (ex: "chrome", "edge", "firefox"), le ProgId brut si inconnu, ou `None`
    si indétectable (clé absente, plateforme non-Windows, permission refusée)
    — jamais une exception, jamais une valeur inventée par défaut."""
    try:
        import winreg
    except ImportError:
        return None  # plateforme non-Windows — dégradation honnête

    try:
        key_path = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        return None

    if not prog_id:
        return None
    return _KNOWN_PROG_IDS.get(prog_id, prog_id)
