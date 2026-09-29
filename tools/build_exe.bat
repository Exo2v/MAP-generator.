@echo off
rem One-click Windows build.  Produces dist\mapgen.exe
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
    echo Python was not found on PATH.  Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH".
    pause
    exit /b 2
)

echo Installing build + runtime requirements ...
python -m pip install --upgrade pip
python -m pip install numpy scipy pillow pyinstaller
python -m pip install pywebview  || echo   ^(pywebview optional - the exe then opens a browser tab^)

echo.
echo Pre-flight ...
python tools\build_exe.py --check || goto :fail

echo.
echo Building ...
python tools\build_exe.py %*
if errorlevel 1 goto :fail

echo.
echo Done: %CD%\dist\mapgen.exe
pause
exit /b 0

:fail
echo.
echo Build failed.  See the messages above.
pause
exit /b 1
