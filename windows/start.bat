@echo off
rem Starts the NetXWheel server. Keep this window open while you play (close it to stop).
title NetXWheel server
set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% "%~dp0..\netxwheel_server.py"
pause
