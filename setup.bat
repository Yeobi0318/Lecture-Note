@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "PYEXE="
set "VPY=%~dp0.venv\Scripts\python.exe"

echo ============================================
echo  Lecture Notes - setup (first time only)
echo ============================================

if exist "%VPY%" (
  echo [1/5] Existing virtual environment found. Skipping Python install.
  goto install_pkgs
)

rem ---- 1) Find an installed Python 3.10 - 3.12 ----
call :try_py 3.11
if not defined PYEXE call :try_py 3.12
if not defined PYEXE call :try_py 3.10
if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if defined PYEXE goto make_venv

rem ---- 2) Not found: download the official installer from python.org (no winget) ----
echo [1/5] Python not found. Downloading Python 3.11.9 from python.org ...
set "PYINST=%TEMP%\python-3.11.9-amd64.exe"
curl -L --fail -o "%PYINST%" "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
if errorlevel 1 (
  echo curl failed. Trying PowerShell download...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile $env:PYINST"
)
if not exist "%PYINST%" goto fail_py

echo       Installing Python for the current user - no admin rights needed. Please wait...
"%PYINST%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 InstallLauncherAllUsers=0 Include_test=0
if errorlevel 1 goto fail_py
set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYEXE%" goto fail_py
del "%PYINST%" >nul 2>nul

:make_venv
echo [2/5] Creating virtual environment with: %PYEXE%
"%PYEXE%" -m venv .venv
if errorlevel 1 goto fail
if not exist "%VPY%" goto fail

:install_pkgs
echo [3/5] Upgrading pip...
"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto fail

echo [4/5] Installing PyTorch + torchvision with CUDA - about 3 GB, takes a while...
"%VPY%" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124
if errorlevel 1 goto fail

echo [5/5] Installing faster-whisper, EasyOCR, ffmpeg, python-docx...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail

echo.
echo ============ Checking GPU ============
"%VPY%" -m lecturekit.cli check
if errorlevel 3 goto check_other
if errorlevel 2 goto add_cuda_libs
goto done

:add_cuda_libs
echo.
echo GPU libraries were not found. Installing extra CUDA libraries and retrying...
"%VPY%" -m pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
"%VPY%" -m lecturekit.cli check
if errorlevel 1 goto gpu_fail
goto done

:check_other
echo.
echo The check could not finish - see the message above. A network problem is the usual cause.
echo Installation itself is done. The program will try to download models when it runs.
call :local_ai
pause
exit /b 0

:done
call :local_ai
echo.
echo Setup finished. From now on, just double-click START.bat
if /i not "%~1"=="nopause" pause
exit /b 0

:gpu_fail
echo.
echo GPU test still fails. Update the NVIDIA driver, reboot, and use "Check installation" in the Help tab.
echo The program still works on CPU, but speech recognition will be very slow.
call :local_ai
pause
exit /b 0

:fail_py
echo.
echo Python download or install failed.
echo Install Python 3.11 manually from https://www.python.org/downloads/release/python-3119/
echo   - check "Add python.exe to PATH" in the installer - then run setup.bat again.
pause
exit /b 1

:fail
echo.
echo Installation failed. Copy the error message above and ask for help.
pause
exit /b 1

rem ---- subroutine: install local AI (Ollama + model). Failure is not fatal. ----
:local_ai
echo.
echo ============ Local AI: checking this PC and installing a model that fits ============
"%VPY%" -m lecturekit.cli ai-setup --auto
if errorlevel 1 echo Local AI setup failed. You can retry later with the "Install local AI" button in the program.
exit /b 0

rem ---- subroutine: set PYEXE if "py -X.Y" works ----
:try_py
py -%1 -c "import sys" >nul 2>nul || exit /b 0
for /f "delims=" %%P in ('py -%1 -c "import sys; print(sys.executable)"') do set "PYEXE=%%P"
exit /b 0
