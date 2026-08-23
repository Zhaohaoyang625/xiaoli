@echo off
rem Open XiaoLi web page with local server (Live2D needs http://, not file://)
rem Safe to double-click anytime: starts server if needed, then opens browser.
cd /d "%~dp0"

rem Check if server is already running on port 8080
python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/XiaoLi.html', timeout=1).status==200 else 1)" >nul 2>&1
if errorlevel 1 (
    rem Server not running -> start it hidden (pythonw, no window)
    start "" /min pythonw "scripts\serve_web.py"
    ping 127.0.0.1 -n 2 >nul
)

rem 版本号 ?v=：网页改版后手动加一版（让浏览器绝不拿旧缓存）
start "" "http://127.0.0.1:8080/XiaoLi.html?v=12"
