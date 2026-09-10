@echo off
REM Agent4Market 工作台启动脚本
REM 使用方法：双击此文件即可启动工作台

cd /d "%~dp0"

echo ============================================
echo   Agent4Market 销售总监工作台
echo ============================================
echo.
echo 正在启动工作台服务器...
echo.

python ui\server.py --port 5678

pause
