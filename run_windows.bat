@echo off
REM Run everything on this Windows machine: the bounding-box service (GPU when available, own window)
REM and the main lithology server + web UI, which calls the service locally at 127.0.0.1.
REM First run creates the two venvs and installs everything (several minutes).
REM
REM   run_windows.bat                 web UI: http://127.0.0.1:8000  (and http://<tailscale ip>:8000)
REM   set APP_PORT=9000 / set BBOX_PORT=9100 before running to change the ports.
REM   DATABASE_URL etc.: put a .env file in this folder (see .env.example).
REM   Bounding-box JSONs / images: ..\dataset\bbox_annotation and ..\dataset\images are used when they exist,
REM   or set LITHO_ANNOTATION_DIRS / LITHO_IMAGE_DIRS (folders separated by ;) in .env.
setlocal
cd /d "%~dp0"
set "ROOT=%CD%"
if "%APP_PORT%"=="" set APP_PORT=8000
if "%BBOX_PORT%"=="" set BBOX_PORT=8100
REM Windows' default file encoding is not UTF-8; the code expects it
set PYTHONUTF8=1
REM Use CPU on this machine unless explicitly overridden before launching.
if "%BBOX_DEVICE%"=="" set "BBOX_DEVICE=cpu"

REM ---- 1. bounding-box service, in its own window (run_server.bat sets up its venv + CUDA torch itself)
set "PORT=%BBOX_PORT%"
start "Bounding-box service (port %BBOX_PORT%)" cmd /c ""%ROOT%\bbox-final\run_server.bat""
set PORT=

REM ---- 2. main server venv (one time)
if not exist ".venv\Scripts\python.exe" (
    echo Creating .venv for the main server and installing dependencies ^(one time, a few minutes^)...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Could not create the venv. Install Python 3.12+ from python.org ^(tick "Add python.exe to PATH"^).
        pause & exit /b 1
    )
)

REM Repair interrupted installs too: an existing venv may still lack server dependencies.
".venv\Scripts\python.exe" -c "import numpy, PIL, scipy, rapidocr, onnxruntime, pypdfium2, fastapi, uvicorn, multipart, psycopg, psycopg_pool, dotenv" 2>nul
if errorlevel 1 (
    echo Installing missing main-server dependencies...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || ".venv\Scripts\python.exe" -m pip install -e . || goto :fail
)

REM ---- 3. main server: bounding boxes from the local service, reachable from other machines
set "LITHO_BBOX_URL=http://127.0.0.1:%BBOX_PORT%"
if "%LITHO_CORS_ORIGINS%"=="" set "LITHO_CORS_ORIGINS=*"
set ARGS=--ui "%ROOT%\webui" --data-dir "%ROOT%\lithology-data" --desktop --host 0.0.0.0 --port %APP_PORT%
set ANN=
set IMG=
if exist "%ROOT%\..\dataset\bbox_annotation" set ANN="%ROOT%\..\dataset\bbox_annotation"
if exist "%ROOT%\..\example" set ANN=%ANN% "%ROOT%\..\example"
if exist "%ROOT%\..\dataset\images" set IMG="%ROOT%\..\dataset\images"
if exist "%ROOT%\..\example" set IMG=%IMG% "%ROOT%\..\example"
if defined ANN set ARGS=%ARGS% --annotations %ANN%
if defined IMG set ARGS=%ARGS% --images %IMG%
if exist "%ROOT%\..\dataset\splits\split.json" set ARGS=%ARGS% --split "%ROOT%\..\dataset\splits\split.json"

echo.
echo Main server:           http://127.0.0.1:%APP_PORT%   (API docs: /docs)
for /f %%i in ('tailscale ip -4 2^>nul') do echo From other machines:  http://%%i:%APP_PORT%
echo Bounding-box service:  %LITHO_BBOX_URL%   (check: curl %LITHO_BBOX_URL%/health)
echo If other machines can't connect, allow Python through Windows Firewall ^(private networks^).
echo.
".venv\Scripts\python.exe" -m lithology serve %ARGS%
pause
exit /b 0

:fail
echo Installing the dependencies failed. Check the pip error above and run this again.
pause
exit /b 1
