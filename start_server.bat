@echo off
title TikTok Live Stream Hub (Snake Live 3D)
cd /d "%~dp0"

if not exist ".env" (
  echo ========================================================
  echo  [!] File .env belum ada.
  echo.
  echo  Salin .env.example menjadi .env, lalu isi:
  echo    ADMIN_USERNAME=...
  echo    ADMIN_PASSWORD=...
  echo ========================================================
  pause
  exit /b 1
)

echo ========================================================
echo  Membuka TikTok Live Stream Hub di browser...
echo  Login       : http://localhost:8001/login.html
echo  Game default: engine.html
echo  Legacy      : snake.html
echo ========================================================
start http://localhost:8001
python -u server.py %*
pause
