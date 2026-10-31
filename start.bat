@echo off
REM Confero, one-click launcher.
REM Double-click this file. It opens the backend and frontend each in
REM their own window and starts them both, no typing, no activating a
REM venv by hand, no cd'ing into folders.
REM
REM %~dp0 below means "the folder this file is sitting in", so this
REM works no matter where the project folder lives on your computer.

echo Starting Confero backend...
start "Confero Backend" cmd /k "cd /d %~dp0backend && venv\Scripts\python.exe -m uvicorn api:app --reload --port 8000"

REM Small pause so the backend window opens first and isn't lost behind the next one.
timeout /t 2 /nobreak >nul

echo Starting Confero frontend...
start "Confero Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Both are starting in their own windows. The site will open in your
echo browser automatically once the frontend is ready.
echo.
echo To stop everything later: just close both of those windows (or press
echo Ctrl+C inside each one).