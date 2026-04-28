@echo off
setlocal
cd /d "%~dp0"

if "%NGROK_EXE%"=="" (
  if exist "%~dp0tools\ngrok.exe" (
    set "NGROK_EXE=%~dp0tools\ngrok.exe"
  ) else (
    set "NGROK_EXE=ngrok"
  )
)

echo Using ngrok: %NGROK_EXE%
call "%NGROK_EXE%" http 8000
echo.
echo ngrok has stopped. Check the error message above.
pause
