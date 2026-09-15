@echo off
title ChargeFlow 一键启动
cd /d "%~dp0"

echo ============================================================
echo   ChargeFlow 充电站 IoT 运营平台 · 一键启动
echo   广东 21 市 · 29 站 · 168 桩模拟器
echo   提示：请勿点击窗口内部（会暂停输出）；若卡住不动，按 Esc 恢复
echo ============================================================
echo.

REM ---------- 0. 基础环境检查 ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 python，请先安装 Python 3.11+ 并加入 PATH
    pause
    exit /b 1
)
where node >nul 2>nul
if errorlevel 1 (
    echo [错误] 未找到 node，请先安装 Node.js 18+ 并加入 PATH
    pause
    exit /b 1
)

REM ---------- 1. 后端依赖 ----------
python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, aiomqtt, amqtt, fakeredis, httpx, jwt" >nul 2>nul
if errorlevel 1 (
    echo [1/5] 首次运行：安装后端依赖（清华镜像，屏幕会滚动下载进度）...
    python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 60
    if errorlevel 1 (
        echo [错误] 后端依赖安装失败，请检查网络后重试，或手动执行：
        echo        python -m pip install -r backend\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        pause
        exit /b 1
    )
    python -c "import fastapi, uvicorn, sqlalchemy, aiosqlite, aiomqtt, amqtt, fakeredis, httpx, jwt" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 依赖安装后校验仍失败，请将上方报错信息反馈给开发者
        pause
        exit /b 1
    )
    echo       依赖安装完成
) else (
    echo [1/5] 后端依赖已就绪
)

REM ---------- 2. 数据库 ----------
if not exist "backend\chargeflow.db" (
    echo [2/5] 初始化数据库：广东 21 市 29 站 168 桩 + 演示账号...
    pushd backend
    python -c "from app.db import init_db; from app.seed import seed; import asyncio; asyncio.run(init_db()); asyncio.run(seed())"
    popd
    if errorlevel 1 (
        echo [错误] 数据库初始化失败，请查看上方报错
        pause
        exit /b 1
    )
) else (
    echo [2/5] 数据库已就绪
)

REM ---------- 3. AI 配置软提示 ----------
if not exist ".env" (
    echo [提示] 未找到 .env 文件，AI 功能将降级运行，核心充电与计费不受影响
) else (
    findstr /r /c:"^DEEPSEEK_API_KEY=sk-" .env >nul 2>nul
    if errorlevel 1 echo [提示] .env 中未检测到 DEEPSEEK_API_KEY，AI 功能将降级运行，核心充电与计费不受影响
)

REM ---------- 4. 启动后端 + MQTT Broker ----------
echo [3/5] 启动后端 + 嵌入式 MQTT Broker，端口 :8000 / :1883 ...
start "ChargeFlow-后端" cmd /k "cd backend && python serve.py"
echo       等待后端就绪，最多 60 秒...
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',8000);$c.Close();exit 0}catch{Start-Sleep 1}};exit 1" >nul
if errorlevel 1 (
    echo [错误] 后端 60 秒内未就绪，请查看 ChargeFlow-后端 窗口中的报错
    pause
    exit /b 1
)
echo       后端已就绪

REM ---------- 5. 启动桩模拟器 ----------
echo [4/5] 启动 168 桩模拟器，每桩独立 MQTT 连接，混沌模式注入异常...
start "ChargeFlow-模拟器" cmd /k "cd simulator && python pile_simulator.py --chaos --traffic 6"

REM ---------- 6. 前端 ----------
if not exist "frontend\node_modules" (
    echo [5/5] 首次运行：安装前端依赖（国内镜像，约 1-3 分钟）...
    pushd frontend
    call npm install --no-fund --no-audit --registry https://registry.npmmirror.com
    popd
    if errorlevel 1 (
        echo [错误] 前端依赖安装失败，请查看上方报错
        pause
        exit /b 1
    )
) else (
    echo [5/5] 前端依赖已就绪
)
echo       启动前端开发服务器，端口 :5173 ...
start "ChargeFlow-前端" cmd /k "cd frontend && npm run dev"

echo       等待前端就绪，最多 60 秒...
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',5173);$c.Close();exit 0}catch{Start-Sleep 1}};exit 1" >nul
if errorlevel 1 (
    echo [提示] 前端未在 60 秒内就绪，请查看 ChargeFlow-前端 窗口显示的实际端口
) else (
    start http://127.0.0.1:5173
)

echo.
echo ============================================================
echo   全部服务已启动，浏览器将自动打开：
echo     前端入口  http://127.0.0.1:5173
echo     后端 API  http://127.0.0.1:8000/docs
echo   演示账号：
echo     车主     13800000001 / customer123
echo     运维员   13800000002 / operator123
echo     管理员   13800000000 / admin123
echo   停止服务：关闭对应的三个服务窗口即可
echo ============================================================
echo.
pause
