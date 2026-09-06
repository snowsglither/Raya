# RAYA V2 — setup minimal pour une nouvelle machine (Chantier 17, B12).
#
# Ce script AIDE à préparer l'environnement. Il ne télécharge rien de lourd,
# n'installe aucun logiciel système, ne touche jamais V1, n'écrase jamais de
# données utilisateur, et ne copie/n'invente aucun secret.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "RAYA V2 — setup" -ForegroundColor Cyan
Write-Host "Racine détectée : $root"

# 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "ERREUR : Python introuvable dans PATH. Installe Python >= 3.11 avant de continuer." -ForegroundColor Red
    exit 1
}
$versionOutput = & python --version 2>&1
Write-Host "Python trouvé : $versionOutput"

# 2. .env — jamais écrasé s'il existe déjà (données de configuration de l'utilisateur)
$envPath = Join-Path $root ".env"
$envExamplePath = Join-Path $root ".env.example"
if (Test-Path $envPath) {
    Write-Host ".env existe déjà — non touché."
} elseif (Test-Path $envExamplePath) {
    Copy-Item $envExamplePath $envPath
    Write-Host ".env créé à partir de .env.example — édite-le pour renseigner OLLAMA_API_KEY (voir RAYA_V2_SETUP_ANOTHER_PC.md)." -ForegroundColor Yellow
} else {
    Write-Host "AVERTISSEMENT : .env.example introuvable, .env non créé." -ForegroundColor Yellow
}

# 3. Dépendances Python (éditable install, pas de téléchargement de modèle)
Write-Host "Installation des dépendances (pip install -e .[dev])..."
& python -m pip install -e "$root[dev]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERREUR : l'installation des dépendances a échoué." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Setup terminé." -ForegroundColor Green
Write-Host "Prochaines étapes :"
Write-Host "  1. Édite .env (au minimum OLLAMA_API_KEY)."
Write-Host "  2. Lance : raya-web   (ou raya pour le CLI headless)"
Write-Host "  3. Voir RAYA_V2_SETUP_ANOTHER_PC.md pour le détail (navigateur/audio/Telegram)."
