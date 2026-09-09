@echo off
rem FOMO Whale Intelligence launcher.
rem Double-click to install what is missing and open the window.
rem Or pass a command: run.bat gui ^| menu ^| research ^| collect ^| api ^| worker ^| check ^| test ^| seed ^| reinstall
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
title FOMO Whale Intelligence
set "PYTHONIOENCODING=utf-8"

set "VENV_DIR=%CD%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"
set "STAMP=%VENV_DIR%\.setup-complete"

set "INTERACTIVE=0"
set "ACTION=%~1"
if not defined ACTION set "ACTION=gui"
if /I "%ACTION%"=="menu" (
    set "INTERACTIVE=1"
    set "ACTION="
)

:loop
if "%INTERACTIVE%"=="1" call :menu
if not defined ACTION goto end

call :setup
if errorlevel 1 goto failed

call :dispatch
set "RESULT=%errorlevel%"

if "%INTERACTIVE%"=="1" (
    echo.
    pause
    set "ACTION="
    cls
    goto loop
)
exit /b %RESULT%


:menu
cls
echo.
echo   FOMO Whale Intelligence
echo   =======================
echo.
echo     1^)  gui         open the launcher window
echo     2^)  research    scan the whole site into a text report
echo     3^)  collect     one crawl cycle into the database
echo     4^)  api         REST API on http://127.0.0.1:8000
echo     5^)  worker      continuous collection worker
echo     6^)  check       provider health check
echo     7^)  test        pytest and ruff
echo     8^)  seed        fill the database with mock data (offline)
echo     9^)  reinstall   rebuild the environment from scratch
echo     0^)  exit
echo.
set "CHOICE="
set /p "CHOICE=Choose [1]: "
if not defined CHOICE set "CHOICE=1"
if "%CHOICE%"=="1" set "ACTION=gui"
if "%CHOICE%"=="2" set "ACTION=research"
if "%CHOICE%"=="3" set "ACTION=collect"
if "%CHOICE%"=="4" set "ACTION=api"
if "%CHOICE%"=="5" set "ACTION=worker"
if "%CHOICE%"=="6" set "ACTION=check"
if "%CHOICE%"=="7" set "ACTION=test"
if "%CHOICE%"=="8" set "ACTION=seed"
if "%CHOICE%"=="9" set "ACTION=reinstall"
if "%CHOICE%"=="0" set "ACTION="
exit /b 0


:setup
if /I "%ACTION%"=="reinstall" goto install
if not exist "%STAMP%" goto install
"%VENV_PY%" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [!] The existing Python environment is invalid or not Python 3.12.
    set "ACTION=reinstall"
    goto install
)
rem The stamp only proves pip ran. The browser lives in a per-user cache outside the
rem project, so a copied folder or a new Windows account can lose it. This costs about
rem half a second when Chromium is already there, and repairs it when it is not.
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo [X] Chromium could not be installed. Crawl mode needs it.
    exit /b 1
)
rem gmgn-cli is optional (GMGN_ENABLED gates it) and installs itself on first use,
rem so we only pre-install here when the user has already opted in.
findstr /I /C:"GMGN_ENABLED=true" ".env" >nul 2>&1
if not errorlevel 1 (
    where gmgn-cli >nul 2>&1 || (
        where npm >nul 2>&1 && (
            echo Installing gmgn-cli for the GMGN integration...
            call npm install -g gmgn-cli --silent
        )
    )
)
exit /b 0

:install
set "HOST_PY="
rem Prefer the stable Python 3.12 installation over Python 3.14 beta.
where py >nul 2>&1 && py -3.12 -c "import sys; sys.exit(0)" >nul 2>&1 && set "HOST_PY=py -3.12"
if not defined HOST_PY where python >nul 2>&1 && set "HOST_PY=python"
if not defined HOST_PY (
    echo [X] Python was not found on PATH.
    echo     Install Python 3.12 or newer from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH" during setup.
    exit /b 1
)

%HOST_PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [X] Python 3.12 or newer is required.
    %HOST_PY% --version
    exit /b 1
)

if /I "%ACTION%"=="reinstall" if exist "%VENV_DIR%" (
    echo [1/5] Removing the old environment...
    rmdir /s /q "%VENV_DIR%"
)

if not exist "%VENV_PY%" (
    echo [1/5] Creating the virtual environment...
    %HOST_PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [X] Could not create the virtual environment.
        exit /b 1
    )
) else (
    echo [1/5] Virtual environment already exists.
)

echo [2/5] Updating pip...
"%VENV_PY%" -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [X] pip could not be updated. Check your internet connection or proxy.
    exit /b 1
)

echo [3/5] Installing project dependencies...
"%VENV_PY%" -m pip install -e ".[dev]" --quiet
if errorlevel 1 (
    echo [X] Dependency installation failed.
    exit /b 1
)

echo [4/5] Installing Chromium for crawl mode (large download on first run)...
"%VENV_PY%" -m playwright install chromium
if errorlevel 1 (
    echo [X] Chromium could not be installed. Crawl mode needs it.
    echo     Retry, or set BROWSER_EXECUTABLE_PATH in .env to an existing Chrome.
    exit /b 1
)

echo [5/5] Checking configuration...
if not exist ".env" (
    copy /y ".env.example" ".env" >nul
    echo       Created .env from .env.example. Crawl mode needs no API key.
) else (
    echo       .env already exists, leaving it untouched.
)

> "%STAMP%" echo setup completed
echo.
echo Setup complete.
echo.
exit /b 0


:dispatch
if /I "%ACTION%"=="setup"     exit /b 0
if /I "%ACTION%"=="reinstall" exit /b 0
if /I "%ACTION%"=="gui"       goto do_gui
if /I "%ACTION%"=="research"  goto do_research
if /I "%ACTION%"=="collect"   goto do_collect
if /I "%ACTION%"=="api"       goto do_api
if /I "%ACTION%"=="worker"    goto do_worker
if /I "%ACTION%"=="check"     goto do_check
if /I "%ACTION%"=="test"      goto do_test
if /I "%ACTION%"=="seed"      goto do_seed
echo Unknown command: %ACTION%
echo Use: run.bat [gui ^| menu ^| research ^| collect ^| api ^| worker ^| check ^| test ^| seed ^| reinstall]
exit /b 1

:do_gui
"%VENV_PY%" "scripts\launcher.py" --selftest >nul 2>&1
if errorlevel 1 (
    echo [X] A window cannot be opened; Tk is unavailable in this Python install.
    echo     Reinstall Python with the "tcl/tk and IDLE" option ticked, then run: run.bat reinstall
    echo     Falling back to the text menu.
    echo.
    set "INTERACTIVE=1"
    set "ACTION="
    exit /b 0
)
echo Opening the launcher window...
if exist "%VENV_PYW%" (
    start "" "%VENV_PYW%" "scripts\launcher.py"
) else (
    start "" "%VENV_PY%" "scripts\launcher.py"
)
exit /b 0

:do_research
echo Scanning every public widget and writing a report...
echo.
"%VENV_PY%" scripts\research.py
exit /b %errorlevel%

:do_collect
echo Running one collection cycle...
echo.
"%VENV_PY%" scripts\collect_once.py
exit /b %errorlevel%

:do_api
echo API starting. Open http://127.0.0.1:8000/docs
echo Press Ctrl+C to stop.
echo.
"%VENV_PY%" -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:do_worker
echo Worker starting. Press Ctrl+C to stop.
echo.
"%VENV_PY%" scripts\run_worker.py
exit /b %errorlevel%

:do_check
echo Checking the configured provider...
echo.
"%VENV_PY%" scripts\check_provider.py
exit /b %errorlevel%

:do_test
set "PYTHONPATH=."
"%VENV_PY%" -m pytest -q
set "TEST_RESULT=%errorlevel%"
echo.
"%VENV_PY%" -m ruff check .
if not "%TEST_RESULT%"=="0" exit /b %TEST_RESULT%
exit /b %errorlevel%

:do_seed
echo Seeding fictional mock data...
echo.
"%VENV_PY%" scripts\seed_mock_data.py
exit /b %errorlevel%


:failed
echo.
echo Setup did not finish. Nothing was run.
if "%INTERACTIVE%"=="1" pause
exit /b 1

:end
endlocal
exit /b 0
