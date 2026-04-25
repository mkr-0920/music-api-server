import asyncio
import traceback
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 引入重构后的路由
from api.routers import netease, qq, local, playlist, navidrome, instrumental

# 引入依赖和后台任务
from api.dependencies import verify_api_key
from api.routers.instrumental import mvsep_queue_worker
from core.qq_refresh.refresher import QQCookieRefresher

from fastapi.staticfiles import StaticFiles

app = FastAPI(
    title="全能音乐API服务器",
    description="一个集成了Web界面、智能工具和多源音乐API的私有化解决方案。",
    version="2.0.0",
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常拦截器，防止因为未捕获异常导致终端刷屏或前端无响应。"""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": "服务器内部错误", "detail": str(exc)},
    )

# 挂载前端 HTML 目录作为静态文件服务
app.mount("/ui", StaticFiles(directory="html"), name="ui")

# 注册所有路由
app.include_router(netease.router, dependencies=[Depends(verify_api_key)])
app.include_router(qq.router, dependencies=[Depends(verify_api_key)])
app.include_router(playlist.router, dependencies=[Depends(verify_api_key)])
app.include_router(navidrome.router, dependencies=[Depends(verify_api_key)])
app.include_router(instrumental.router, dependencies=[Depends(verify_api_key)])
# local 路由自己处理了部分鉴权 (如 cover 不需要鉴权)
app.include_router(local.router)

@app.get("/")
async def index():
    return {"message": "全能音乐API服务器正在运行"}

# --------------------------------------------------------------------------
# 定时任务
# --------------------------------------------------------------------------
scheduler = AsyncIOScheduler()

def refresh_qq_cookie_job():
    print("正在执行QQ音乐Cookie刷新任务...")
    refresher = QQCookieRefresher()
    refresher.refresh()

@app.on_event("startup")
async def startup_event():
    scheduler.add_job(refresh_qq_cookie_job, "interval", hours=23, id="RefreshQQCookie")
    scheduler.start()
    print("FastAPI 应用启动，QQ音乐Cookie定时刷新任务已添加。")

    asyncio.create_task(mvsep_queue_worker())
    print("FastAPI 应用启动，MVSep 伴奏流水线已激活。")
