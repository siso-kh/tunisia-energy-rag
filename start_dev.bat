@echo off
setlocal
title Tunisia Energy RAG - Dev Launcher
cd /d "%~dp0"

REM ============================================================
REM  Tunisia Energy RAG - local dev launcher
REM  Starts the FastAPI backend (:8000) and Vite frontend (:5173)
REM  in two separate windows, then opens the browser.
REM
REM  Environment variables defined in .env are respected.
REM  Dev-only fallbacks below are applied ONLY when .env does
REM  not define them.
REM ============================================================

REM --- Pre-flight checks -------------------------------------------------
if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment not found.
    echo         Run:  python -m venv .venv  then re-run this file.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo [ERROR] Frontend dependencies missing.
    echo         Run:  cd frontend ^&^& npm install  then re-run this file.
    pause
    exit /b 1
)

REM --- Backend DB: prefer .env, else the docker-compose Postgres (:5433) ---
REM The dev runtime uses the SAME Postgres as production (one dialect, no drift).
REM Start it with:  docker compose up -d postgres
findstr /B "DATABASE_URL=" .env >nul 2>&1
if errorlevel 1 set "DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/energie_tunisie"

REM --- Dev-only auth/admin fallbacks (set real values in .env) -----------
findstr /B "JWT_SECRET=" .env >nul 2>&1
if errorlevel 1 set "JWT_SECRET=dev-jwt-secret-2026-change-me"
findstr /B "ADMIN_API_KEY=" .env >nul 2>&1
if errorlevel 1 set "ADMIN_API_KEY=dev-admin-2026"

REM --- Apply schema migrations, then launch backend ----------------------
REM The schema is owned by Alembic; upgrade head is idempotent (no-op when
REM already migrated, stamps create_all-era DBs).
echo Applying schema migrations (alembic upgrade head) ...
call .venv\Scripts\activate.bat && python -m alembic upgrade head
if errorlevel 1 (
    echo [WARNING] Migration step failed - the backend may not have its tables.
)
echo Starting backend (FastAPI) on http://localhost:8000 ...
start "TunisiaEnergy-Backend" cmd /k "call .venv\Scripts\activate.bat && python -m uvicorn src.api.main:app --port 8000"

REM --- Launch frontend (Vite dev server) ---------------------------------
echo Starting frontend (Vite) on http://localhost:5173 ...
start "TunisiaEnergy-Frontend" cmd /k "cd /d frontend && npm run dev"

REM --- Open the browser once Vite is up ----------------------------------
echo Opening browser in 3 seconds...
timeout /t 3 /nobreak >nul
start "" http://localhost:5173

echo.
echo ============================================================
echo   Backend:   http://localhost:8000   (API docs: /docs)
echo   Frontend:  http://localhost:5173   (Admin: /admin)
echo   Close each window to stop that server.
echo ============================================================
endlocal
pause
