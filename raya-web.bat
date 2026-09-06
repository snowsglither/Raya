@echo off
REM ============================================================================
REM  RAYA V2 — Cockpit web local. Double-clique ce fichier, ou tape "raya-web"
REM  depuis un cmd ouvert dans ce dossier. Utilise le Python de RAYA/venv
REM  (partage avec V1 — memes dependances deja installees), pas besoin d'un
REM  venv separe. Affiche le lien http://127.0.0.1:8765 (ou RAYA_WEB_HOST/PORT
REM  si definis dans .env) a ouvrir dans un navigateur.
REM ============================================================================
setlocal
set "RAYAV2_DIR=%~dp0"
set "PY=%RAYAV2_DIR%..\RAYA\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%RAYAV2_DIR%"
"%PY%" -m raya.runtime.entrypoints.web

echo.
echo (Cockpit arrete — appuie sur une touche pour fermer cette fenetre)
pause >nul
endlocal
