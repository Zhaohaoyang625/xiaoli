@echo off
chcp 65001 >nul
cd /d %~dp0
echo ============================
echo   小李要上线啦～
echo ============================
echo 如果窗口闪退：说明 Python 没装或没加进 PATH
echo （浏览器再打开 web\XiaoLi.html 就能看到她的脸）
echo.
python -m xiaoli.chat --voice
pause
