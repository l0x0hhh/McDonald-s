@echo off
setlocal
cd /d "%~dp0"

rem Reuse only the expected Vite entry. Never start a backend or port 8000.
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; try { $r=Invoke-WebRequest -Uri 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 3; if($r.StatusCode -eq 200 -and $r.Content.Contains('/src/main.tsx')) { Write-Output 'FRONTEND_READY: http://127.0.0.1:5173/'; exit 0 } } catch {}; exit 1"
if not errorlevel 1 exit /b 0
if /i "%~1"=="--check" exit /b 1

echo Starting frontend at http://127.0.0.1:5173/
echo Keep this window open while using the local preview.
call npm.cmd run dev -- --port 5173 --strictPort
exit /b %errorlevel%
