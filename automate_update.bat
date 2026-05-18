@echo off
REM Planificateur de taches Windows : lancer ce fichier ou :
REM   python "%~dp0update_from_site.py"
cd /d "%~dp0"
python update_from_site.py %*
if errorlevel 1 exit /b 1
exit /b 0
