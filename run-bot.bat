@echo off
setlocal
cd /d "%~dp0"

echo Starting Vault LINE Bot server...
python -m uvicorn main:app --host 0.0.0.0 --port 8000
echo.
echo Bot server has stopped. Check the error message above.
pause
