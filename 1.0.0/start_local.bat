@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ========================================
echo   影片库 本地启动（无 Docker）
echo ========================================
echo.
echo [1/2] 检查依赖...
python -c "import flask,requests,bs4,lxml" >nul 2>&1
if errorlevel 1 (
  echo 正在安装依赖（阿里源）...
  python -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
)
echo [2/2] 启动服务...
echo.
echo 打开浏览器访问: http://localhost:5000
echo 默认密码: admin （可用环境变量 APP_PASSWORD 修改）
echo 按 Ctrl+C 停止
echo.
python app.py
pause
