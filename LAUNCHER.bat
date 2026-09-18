@echo off
rem GUI launcher without a console window.
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (
  start "" pyw -3 launcher.py
  goto :eof
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw launcher.py
  goto :eof
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 launcher.py
  goto :eof
)
python launcher.py
