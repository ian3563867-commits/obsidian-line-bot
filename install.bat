@echo off
cd /d "%~dp0"
echo 安裝依賴套件...
pip install -r requirements.txt
echo.
echo 完成！請複製 .env.example 為 .env 並填入 LINE 憑證。
pause
