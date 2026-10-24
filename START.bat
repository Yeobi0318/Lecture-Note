@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "VPY=%~dp0.venv\Scripts\python.exe"
set "VPYW=%~dp0.venv\Scripts\pythonw.exe"

rem ---- First run (or broken install): run setup automatically ----
if not exist "%VPY%" goto do_setup
"%VPY%" -c "import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in ['faster_whisper', 'easyocr', 'torch', 'docx']) else 1)" >nul 2>nul
if errorlevel 1 goto do_setup
goto launch

:do_setup
echo ============================================================
echo  First run: installing required programs (10-30 minutes).
echo  Please keep this window open until it finishes.
echo ============================================================
call "%~dp0setup.bat" nopause
if errorlevel 1 exit /b 1

:launch
rem ---- Check that the program can start (shows errors if not) ----
"%VPY%" -c "import lecturekit.gui"
if errorlevel 1 (
  echo.
  echo The program could not start. See the message above.
  echo If it mentions tkinter, reinstall Python 3.11 from python.org and run setup.bat again.
  pause
  exit /b 1
)
start "" "%VPYW%" -m lecturekit.gui
exit /b 0
