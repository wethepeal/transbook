@echo off
REM =====================================================================
REM  transbook launcher for the "unzip and double-click" package.
REM
REM  KEEP THIS FILE PURE ASCII. Do not add Chinese here.
REM
REM  cmd.exe decodes batch-file bytes using the active code page, so Chinese
REM  text in a .cmd turns into mojibake on machines whose code page is not
REM  UTF-8. (Measured: three encodings produced identical redirected bytes,
REM  so this cannot be made safe by choosing an encoding.) All user-facing
REM  Chinese therefore lives in Python -- `tp setup` and `tp serve` -- which
REM  uses the Windows console Unicode API and is code-page independent.
REM  Chinese documentation is in README.txt next to this file.
REM =====================================================================
title transbook
cd /d "%~dp0"

echo.
echo   transbook - ebook translation pipeline
echo   =====================================
echo.

REM -- 1. locate the bundled wheel -------------------------------------
set "WHL="
for %%f in ("%~dp0*.whl") do set "WHL=%%~ff"
if not defined WHL goto :no_whl
if not exist "%WHL%" goto :no_whl

REM -- 2. locate uv; install it if missing -----------------------------
REM     uv installs into the user profile, so no administrator rights.
set "UV="
for %%p in (uv.exe) do if not defined UV set "UV=%%~$PATH:p"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if defined UV goto :have_uv

echo   First run: installing uv (Python package manager, about 30 MB)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; irm https://astral.sh/uv/install.ps1 | iex"
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV goto :no_uv

:have_uv

REM -- 3. install / upgrade transbook ----------------------------------
echo   Installing transbook...
"%UV%" tool install --force "%WHL%"
if errorlevel 1 goto :fail

REM     Ask uv where it put the executables rather than hardcoding it.
set "BIN=%USERPROFILE%\.local\bin"
for /f "delims=" %%d in ('"%UV%" tool dir --bin 2^>nul') do set "BIN=%%d"
set "TP=%BIN%\tp.exe"
if not exist "%TP%" set "TP=tp"

REM -- 4. first-run configuration (asks for the API key, in Chinese) ---
"%TP%" setup
if errorlevel 1 goto :fail

REM -- 5. serve, then open the browser ---------------------------------
if not exist "work" mkdir "work"
echo.
echo   Starting... your browser will open http://127.0.0.1:8321/
echo   THIS BLACK WINDOW IS THE SERVER. Closing it stops the service.
echo.
"%TP%" serve --root "work" --open

echo.
echo   Server stopped.
pause
exit /b 0

:no_whl
echo   [ERROR] No .whl file found next to start.cmd.
echo   Keep start.cmd and transbook-*.whl in the same folder.
goto :fail

:no_uv
echo   [ERROR] Could not install uv (network problem?).
echo   Install it manually and run start.cmd again:
echo     https://docs.astral.sh/uv/getting-started/installation/
goto :fail

:fail
echo.
pause
exit /b 1
