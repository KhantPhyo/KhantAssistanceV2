@echo off
REM Khant Assistance v2 - Windows launcher
REM Opens backend (port 8000) and frontend (port 5173) in two windows.

setlocal
set ROOT=%~dp0
cd /d "%ROOT%"

where python >nul 2>&1 || (echo [error] python not found & exit /b 1)
where npm    >nul 2>&1 || (echo [error] npm not found    & exit /b 1)

REM --- backend ---
cd /d "%ROOT%backend"
if not exist .venv (
  echo [launcher] Creating Python venv...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
pip install -q --upgrade pip
pip install -q -r requirements.txt
if not exist .env copy /Y .env.example .env >nul

REM --- frontend deps ---
cd /d "%ROOT%frontend"
if not exist node_modules (
  echo [launcher] Installing frontend deps...
  call npm install --silent
)

REM --- launch in separate windows ---
start "khant-v2-backend"  cmd /k "cd /d %ROOT%backend && call .venv\Scripts\activate.bat && uvicorn app.main:app --reload --port 8000"
start "khant-v2-frontend" cmd /k "cd /d %ROOT%frontend && npm run dev"

echo.
echo Backend  -^> http://localhost:8000
echo Frontend -^> http://localhost:5173
echo Login: khantphyo.myanmar@gmail.com / Cisco@123
endlocal
