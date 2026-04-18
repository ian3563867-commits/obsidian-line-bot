@echo off
cd /d "%~dp0"
if "%NGROK_EXE%"=="" set NGROK_EXE=ngrok
echo 啟動 ngrok...
start "ngrok" cmd /k "%NGROK_EXE% http 8000"
echo 等待 ngrok 啟動...
timeout /t 2 /nobreak >nul
echo 啟動 Vault LINE Bot...
python -m uvicorn main:app --host 0.0.0.0 --port 8000
pause
