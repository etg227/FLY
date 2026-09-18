@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 main.py
  if errorlevel 1 pause
  goto :eof
)
where python >nul 2>nul
if %errorlevel%==0 (
  python main.py
  if errorlevel 1 pause
  goto :eof
)
echo [FLY] Python not found. Please install Python 3.11+ ...
echo [FLY] and tick "Add python.exe to PATH" during setup.
echo [FLY] Download: https://www.python.org/downloads/
pause
