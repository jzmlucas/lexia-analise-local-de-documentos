@echo off
cd /d "%~dp0"
where uv >nul 2>&1
if errorlevel 1 (
 echo O comando uv nao foi encontrado. Instale uv e consulte o README.md.
 pause
 exit /b 1
)
start "" http://127.0.0.1:8000
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
