@echo off
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo [dev] .venv not found. Create it: python -m venv .venv
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0tasks.py" %*
exit /b %ERRORLEVEL%
