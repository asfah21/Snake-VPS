@echo off
title TikTok Live Stream Hub (Snake Live 3D)
cd /d "%~dp0"
echo ========================================================
echo  Membuka TikTok Live Stream Hub di browser...
echo ========================================================
start http://localhost:8001
python server.py %*
pause
