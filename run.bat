@echo off
cd /d "%~dp0"
if exist "dist\1.3.1\ASCII Studio\ASCII Studio.exe" (
    start "" "dist\1.3.1\ASCII Studio\ASCII Studio.exe" %*
    exit /b
)
if exist "dist\1.3.0\ASCII Studio\ASCII Studio.exe" (
    start "" "dist\1.3.0\ASCII Studio\ASCII Studio.exe" %*
    exit /b
)
if exist "dist\1.2.1\ASCII Studio\ASCII Studio.exe" (
    start "" "dist\1.2.1\ASCII Studio\ASCII Studio.exe" %*
    exit /b
)
if exist "dist\1.2.0\ASCII Studio\ASCII Studio.exe" (
    start "" "dist\1.2.0\ASCII Studio\ASCII Studio.exe" %*
    exit /b
)
if exist "dist\ASCII Studio\ASCII Studio.exe" (
    start "" "dist\ASCII Studio\ASCII Studio.exe" %*
    exit /b
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py" %*
    exit /b
)
echo Run setup.bat first, then open run.bat again.
pause
