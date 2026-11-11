@echo off
rem The netxwheel command on Windows: run it from this folder, e.g.  netxwheel status
set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% "%~dp0netxwheel_cli.py" %*
