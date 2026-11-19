@echo off
REM Copyright 2026 Google. This software is provided as-is, without
REM warranty or representation for any use or purpose. Your use of it is
REM subject to your agreement with Google.
setlocal enabledelayedexpansion

REM ========================================================================
REM Virtual environment detection (uses active VIRTUAL_ENV or local .\venv or .\.venv)
REM ========================================================================
if defined VIRTUAL_ENV (
    set "VENV=%VIRTUAL_ENV%"
) else if exist "venv\Scripts\python.exe" (
    set "VENV=venv"
) else if exist ".venv\Scripts\python.exe" (
    set "VENV=.venv"
) else (
    set "VENV="
)

if "%VENV%"=="" (
    set "PYTHON=python"
    set "PIP=python -m pip"
    set "RUFF=ruff"
    set "MYPY=mypy"
    set "PYTEST=pytest"
) else (
    set "PYTHON=%VENV%\Scripts\python.exe"
    set "PIP=%VENV%\Scripts\python.exe -m pip"
    set "RUFF=%VENV%\Scripts\ruff.exe"
    set "MYPY=%VENV%\Scripts\mypy.exe"
    set "PYTEST=%VENV%\Scripts\pytest.exe"
)

if not defined GCP_REGION set "GCP_REGION=europe-west4"
if not defined GCP_PROJECT if defined GOOGLE_CLOUD_PROJECT set "GCP_PROJECT=%GOOGLE_CLOUD_PROJECT%"
if not defined GCP_PROJECT (
    for /f "delims=" %%i in ('gcloud config get-value project 2^>nul') do set "GCP_PROJECT=%%i"
)
if not defined AR_REPOSITORY set "AR_REPOSITORY=agentops-python-repo"

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=help"
shift

if /I "%TARGET%"=="help" goto help
if /I "%TARGET%"=="set-project" goto set-project
if /I "%TARGET%"=="set-env" goto set-env
if /I "%TARGET%"=="venv" goto venv
if /I "%TARGET%"=="install" goto install
if /I "%TARGET%"=="format" goto format
if /I "%TARGET%"=="lint" goto lint
if /I "%TARGET%"=="typecheck" goto typecheck
if /I "%TARGET%"=="test" goto test
if /I "%TARGET%"=="build-sdk" goto build-sdk
if /I "%TARGET%"=="publish-sdk" goto publish-sdk
if /I "%TARGET%"=="clean" goto clean

echo Unknown target: %TARGET%
goto help

:help
echo ========================================================================
echo                          AgentOps SDK ^& Tools
echo ========================================================================
echo Usage: make.bat ^<target^>
echo.
echo Available Targets:
echo   help         - Show this help menu with all available options
echo   set-project  - Configure gcloud project ^& ADC quota for %GCP_PROJECT%
echo   install      - Install SDK in editable mode with [dev,all] ^& MCP deps
echo   format       - Auto-format codebase using ruff format
echo   lint         - Run ruff check and ruff format --check
echo   typecheck    - Run strict mypy type checks on SDK and MCP server
echo   test         - Run unit and contract pytest suites
echo   venv         - Create and setup local Python virtual environment (.\venv)
echo   build-sdk    - Clean and build Python wheel/sdist packages for SDK
echo   publish-sdk  - Build and publish SDK package to Google Artifact Registry
echo   clean        - Remove build artifacts, caches, and temporary files
echo.
echo Configuration Variables (Override with set VAR=value before running):
echo   GCP_PROJECT   (default: %GCP_PROJECT%)
echo   GCP_REGION    (default: %GCP_REGION%)
echo   AR_REPOSITORY (default: %AR_REPOSITORY%)
echo ========================================================================
goto :eof

:set-project
if "%GCP_PROJECT%"=="" (
    echo GCP_PROJECT is not set; set GCP_PROJECT=^<project-id^> or run 'gcloud config set project ^<id^>'.
    exit /b 1
)
if "%GCP_PROJECT%"=="(unset)" (
    echo GCP_PROJECT is not set; set GCP_PROJECT=^<project-id^> or run 'gcloud config set project ^<id^>'.
    exit /b 1
)
call gcloud config set project "%GCP_PROJECT%" || exit /b 1
call gcloud auth application-default set-quota-project "%GCP_PROJECT%" || ver >nul
goto :eof

:set-env
call :set-project
goto :eof

:venv
if not exist "venv\Scripts\python.exe" if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment at .\venv...
    python -m venv venv && "venv\Scripts\python.exe" -m pip install --upgrade pip || exit /b 1
)
goto :eof

:install
if "%VIRTUAL_ENV%"=="" if not exist "venv\Scripts\python.exe" if not exist ".venv\Scripts\python.exe" (
    call :venv || exit /b 1
    set "VENV=venv"
    set "PYTHON=venv\Scripts\python.exe"
    set "PIP=venv\Scripts\python.exe -m pip"
    set "RUFF=venv\Scripts\ruff.exe"
    set "MYPY=venv\Scripts\mypy.exe"
    set "PYTEST=venv\Scripts\pytest.exe"
)
if "%VENV%"=="venv" (
    "venv\Scripts\python.exe" -m pip install -e ".[dev,all]" || exit /b 1
    if exist ".\mcp_servers\requirements.txt" (
        "venv\Scripts\python.exe" -m pip install -r .\mcp_servers\requirements.txt || exit /b 1
    )
) else (
    %PIP% install -e ".[dev,all]" || exit /b 1
    if exist ".\mcp_servers\requirements.txt" (
        %PIP% install -r .\mcp_servers\requirements.txt || exit /b 1
    )
)
goto :eof

:format
%RUFF% format sdk/src/ mcp_servers/src/ tests/ tools/ examples/
goto :eof

:lint
%RUFF% check sdk/src/ mcp_servers/src/ tests/ tools/ examples/ || exit /b 1
%RUFF% format --check sdk/src/ mcp_servers/src/ tests/ tools/ examples/ || exit /b 1
goto :eof

:typecheck
%MYPY% --strict sdk/src/agentops_sdk mcp_servers/src
goto :eof

:test
%PYTEST% -v tests/unit tests/contract --eval-mode=in_memory
goto :eof

:build-sdk
call :clean || exit /b 1
%PIP% install --upgrade build || exit /b 1
%PYTHON% -m build . || exit /b 1
goto :eof

:publish-sdk
if "%GCP_PROJECT%"=="" (
    echo GCP_PROJECT is not set; set GCP_PROJECT=^<project-id^> or run 'gcloud config set project ^<id^>'.
    exit /b 1
)
if "%GCP_PROJECT%"=="(unset)" (
    echo GCP_PROJECT is not set; set GCP_PROJECT=^<project-id^> or run 'gcloud config set project ^<id^>'.
    exit /b 1
)
call :build-sdk || exit /b 1
%PIP% install --upgrade twine keyrings.google-artifactregistry-auth || exit /b 1
%PYTHON% -m twine upload --repository-url "https://%GCP_REGION%-python.pkg.dev/%GCP_PROJECT%/%AR_REPOSITORY%/" dist\* || exit /b 1
goto :eof

:clean
for /d /r %%d in (__pycache__) do @if exist "%%d" rd /s /q "%%d"
for /d /r %%d in (*.egg-info) do @if exist "%%d" rd /s /q "%%d"
for /d /r %%d in (.pytest_cache) do @if exist "%%d" rd /s /q "%%d"
for /d /r %%d in (.ruff_cache) do @if exist "%%d" rd /s /q "%%d"
for /d /r %%d in (.mypy_cache) do @if exist "%%d" rd /s /q "%%d"
if exist "build" rd /s /q "build"
if exist "dist" rd /s /q "dist"
if exist "sdk\build" rd /s /q "sdk\build"
if exist "sdk\dist" rd /s /q "sdk\dist"
if exist ".coverage" del /f /q ".coverage"
goto :eof