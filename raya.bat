@echo off
REM ============================================================================
REM  RAYA V2 — CLI local. Double-clique ce fichier, ou tape "raya" depuis un
REM  cmd ouvert dans ce dossier. Utilise le Python de RAYA/venv (partage avec
REM  V1 — memes dependances deja installees), pas besoin d'un venv separe.
REM ============================================================================
setlocal
set "RAYAV2_DIR=%~dp0"
set "PY=%RAYAV2_DIR%..\RAYA\venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

cd /d "%RAYAV2_DIR%"
"%PY%" -m raya.runtime.entrypoints.cli

echo.
echo (RAYA s'est arrete — appuie sur une touche pour fermer cette fenetre)
pause >nul
endlocal
