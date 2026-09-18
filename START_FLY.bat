@echo off
rem Starts FLY without a lingering console window (pythonw).
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 main.py
  goto :eof
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw main.py
  goto :eof
)
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
echo [FLY] Python not found. Run launcher.exe instead - it installs Python automatically.
echo [FLY] Or install Python 3.11+ manually: https://www.python.org/downloads/
pause
