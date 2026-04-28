@echo off
setlocal
cd /d "%~dp0"

set "PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING"') do set "PORT_PID=%%P"
if not "%PORT_PID%"=="" (
  echo Port 8000 is already used by PID %PORT_PID%. Close the old Bot window first.
  pause
  exit /b 1
)

echo Starting ngrok...
start "Vault LINE Bot - ngrok" cmd /k call "%~dp0run-ngrok.bat"
echo Waiting for ngrok...
timeout /t 2 /nobreak >nul
echo Starting Vault LINE Bot...
start "Vault LINE Bot - server" cmd /k call "%~dp0run-bot.bat"
