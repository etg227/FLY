@echo off
rem Debug launch: keeps a console so you can see log output and errors.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 main.py
  pause
  goto :eof
)
python main.py
pause
