@echo off
rem One-time GOG login for gogrepoc. Saves gog-token.dat next to this script.
cd /d "%~dp0"
".venv\Scripts\python.exe" "gogrepoc\gogrepoc.py" login
pause
