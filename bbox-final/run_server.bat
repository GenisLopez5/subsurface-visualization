@echo off
REM Start the bounding-box service on this Windows machine (port 8100, reachable from other machines).
REM First run creates .venv and installs everything (a few minutes). Double-click it or run: run_server.bat
REM Other port: set PORT=9000 before running.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8100

if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv and installing dependencies ^(one time, a few minutes^)...
    py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not create the venv. Install Python 3.12 from python.org ^(tick "Add python.exe to PATH"^).
        pause & exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu || goto :fail
    ".venv\Scripts\python.exe" -m pip install -r requirements-server.txt || goto :fail
)

REM NVIDIA GPU: swap the CPU-only torch for the CUDA build (once; models-pt\ needs the .pt weights)
where nvidia-smi >nul 2>nul && (
    ".venv\Scripts\python.exe" -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul || (
        echo NVIDIA GPU found: installing CUDA PyTorch ^(one time, a few minutes^)...
        ".venv\Scripts\python.exe" -m pip uninstall -y torch torchvision
        ".venv\Scripts\python.exe" -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
    )
)

echo.
echo Bounding-box service on port %PORT%. Test from the app machine:
echo     curl http://100.110.83.73:%PORT%/health
echo.
".venv\Scripts\python.exe" server.py --host 0.0.0.0 --port %PORT%
pause
exit /b 0

:fail
echo Installing the dependencies failed. Delete the .venv folder and run this again.
rmdir /s /q .venv
pause
exit /b 1
