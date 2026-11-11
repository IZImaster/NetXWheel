@echo off
rem NetXWheel installer for Windows 10/11. Double-click it.
setlocal
set "REPO=%~dp0.."
pushd "%REPO%"
set "REPO=%CD%"
popd

echo == NetXWheel: installing from %REPO%
echo.

set "PY=py"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python is not installed. Get it from https://www.python.org/downloads/
    echo During its setup, tick "Add python.exe to PATH". Then double-click install.bat again.
    pause
    exit /b 1
)

echo == 1/3 SDL for Python (reads the wheel, plays its forces)
%PY% -m pip install --upgrade pysdl2 pysdl2-dll
if errorlevel 1 (
    echo Could not install pysdl2. Check your internet connection and try again.
    pause
    exit /b 1
)

echo.
echo == 2/3 Your settings
%PY% -c "import sys; sys.path.insert(0, r'%REPO%'); from netxwheel import settings; print(settings.ensure())"

echo.
echo == 3/3 Start NetXWheel by itself when you log in?
set /p AUTO="Type Y and Enter for yes, or just Enter for no: "
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\NetXWheel.bat"
if /i "%AUTO%"=="Y" (
    > "%STARTUP%" echo @start "NetXWheel" /min %PY% "%REPO%\netxwheel_server.py"
    echo Yes: it starts minimized when you log in. To stop that, delete:
    echo   %STARTUP%
) else (
    echo No: start it with windows\start.bat when you want to play.
)

echo.
echo Done. Notes:
echo  - Logitech wheels need Logitech G HUB installed for force feedback.
echo  - The first time the server starts, Windows asks about the firewall: click Allow
echo    (private networks), so the game can reach it.
echo  - Commands: open a Command Prompt in the NetXWheel folder and type: netxwheel help
echo.
pause
