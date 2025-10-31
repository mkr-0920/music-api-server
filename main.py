import os
import time
import datetime
import hashlib
import base64
import urllib.parse
import sqlite3
from functools import wraps
from typing import Optional, List
import json
import traceback
import asyncio

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response, BackgroundTasks
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from urllib.parse import quote

# 从我们创建的模块中导入所有API类
from core.config import Config
from api.netease import NeteaseMusicAPI
from api.qq import QQMusicAPI
from api.kuwo import KuwoMusicAPI
from api.local import LocalMusicAPI
from api.navidrome import NavidromeAPI 

# qq音乐刷新cookies
from core.qq_refresh.refresher import QQCookieRefresher

# --------------------------------------------------------------------------
# 1. 初始化应用和所有API客户端
# --------------------------------------------------------------------------
app = FastAPI(
    title="全能音乐API服务器",
    description="一个集成了Web界面、智能工具和多源音乐API的私有化解决方案。",
    version="2.0.0"
)

# 实例化所有API客户端
local_api = LocalMusicAPI(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)
qq_api = QQMusicAPI(local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)
kuwo_api = KuwoMusicAPI()


# --------------------------------------------------------------------------
# 2. API密钥验证 (FastAPI 依赖注入)
# --------------------------------------------------------------------------
def verify_api_key(request: Request):
    provided_key = request.headers.get('X-API-Key') or request.query_params.get('api_key')
    if not hasattr(Config, 'API_SECRET_KEY') or not provided_key or provided_key != Config.API_SECRET_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败：无效的API密钥。")
    return True

# --------------------------------------------------------------------------
# 3. 定义所有FastAPI路由
# --------------------------------------------------------------------------
@app.get('/')
async def index():
    return {"message": "全能音乐API服务器正在运行"}

# --- 在线音乐API路由 ---
@app.get('/api/netease', dependencies=[Depends(verify_api_key)])
async def handle_netease_request(
    background_tasks: BackgroundTasks,
    id: Optional[str] = None,
    q: Optional[str] = None,
    album: Optional[str] = None,
    playlist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    level: str = 'hires'
):
    if playlist_id:
        background_tasks.add_task(netease_api.start_background_playlist_download, playlist_id, level)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"code": 202, "message": "任务已接受", "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"}})
    elif album_id:
        background_tasks.add_task(netease_api.start_background_album_download, album_id, level)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"code": 202, "message": "任务已接受", "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"}})

    if id:
        data = await netease_api.get_song_details(id, level)
    elif q:
        data = await netease_api.search_and_get_details(q, level, album)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供 'id', 'q', 'playlist_id' 或 'album_id' 参数之一。")

    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])
    
    return {"code": 200, "message": "成功", "data": data}

@app.get('/api/qq', dependencies=[Depends(verify_api_key)])
async def handle_qq_request(
    background_tasks: BackgroundTasks,
    mid: Optional[str] = None,
    id: Optional[int] = None,
    q: Optional[str] = None,
    album: Optional[str] = None,
    playlist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    level: str = 'master'
):
    if playlist_id:
        background_tasks.add_task(qq_api.start_background_playlist_download, playlist_id, level)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"code": 202, "message": "任务已接受", "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"}})
    elif album_id:
        background_tasks.add_task(qq_api.start_background_album_download, album_id, level)
        return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content={"code": 202, "message": "任务已接受", "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"}})

    if id or mid:
        data = await qq_api.get_song_details(song_mid=mid, song_id=id)
    elif q:
        data = await qq_api.search_and_get_details(q, album=album)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="必须提供 'mid', 'id', 'q', 'playlist_id' 或 'album_id' 参数之一。")
    
    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])
    
    return {"code": 200, "message": "成功", "data": data}

@app.get('/api/playlist/info', dependencies=[Depends(verify_api_key)])
async def handle_playlist_info_request(platform: str, id: str):
    data = None
    if platform.lower() == 'netease':
        data = await netease_api.get_playlist_info(id)
    elif platform.lower() == 'qq':
        data = await qq_api.get_playlist_info(id)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不支持的平台，请选择 'qq' 或 'netease'。")
    
    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])
    
    return {"code": 200, "message": "成功", "data": data}


class NavidromeImportRequest(BaseModel):
    navidrome_url: str
    username: str
    password: str
    platform: str
    online_playlist_id: str

@app.post('/api/navidrome/import', dependencies=[Depends(verify_api_key)])
async def handle_navidrome_import(import_request: NavidromeImportRequest):
    """接收一个简单的导入指令，由后端完成所有处理流程。"""
    try:
        platform = import_request.platform
        online_playlist_id = import_request.online_playlist_id

        # --- 1. 后端自己获取在线歌单信息 ---
        online_playlist = None
        if platform.lower() == 'netease':
            online_playlist = await netease_api.get_playlist_info(online_playlist_id)
        elif platform.lower() == 'qq':
            online_playlist = await qq_api.get_playlist_info(online_playlist_id)

        if not online_playlist or "error" in online_playlist:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"从 {platform.upper()} 获取歌单信息失败: {online_playlist.get('error', '未知错误')}")

        playlist_name = online_playlist.get('playlist_name')
        songs = online_playlist.get('songs', [])

        # --- 2. 执行导入逻辑 ---
        # 数据库检查是同步的 -> 使用线程池
        existing_navidrome_id = await run_in_threadpool(local_api.get_mapping_for_online_playlist, platform, online_playlist_id)
        if existing_navidrome_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"此歌单已被导入，对应的 Navidrome 歌单ID为 '{existing_navidrome_id}'。如需更新，请使用同步脚本。")

        navi_api = NavidromeAPI(import_request.navidrome_url, import_request.username, import_request.password)
        if not await navi_api.login():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Navidrome 登录失败。")
        
        new_playlist_id = await navi_api.create_playlist(playlist_name)
        if not new_playlist_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="在 Navidrome 中创建歌单失败。")

        # 并发搜索所有歌曲
        search_tasks = [navi_api.search_song(s.get('artist'), s.get('name'), s.get('album')) for s in songs if s.get('artist') and s.get('name')]
        search_results = await asyncio.gather(*search_tasks)
        
        found_ids = [song_id for song_id in search_results if song_id]
        not_found_titles = [f"{s.get('artist')} - {s.get('name')}" for i, s in enumerate(songs) if not search_results[i]]

        if await navi_api.add_songs_to_playlist(new_playlist_id, found_ids):
            # 数据库写入是同步的 -> 使用线程池
            await run_in_threadpool(local_api.add_playlist_mapping, platform, online_playlist_id, new_playlist_id, playlist_name)
            await run_in_threadpool(local_api.update_sync_time, new_playlist_id)
            
            return {
                "code": 200, "message": "导入任务完成。", "data": {
                    "playlist_name": playlist_name,
                    "total_songs": len(songs),
                    "added_count": len(found_ids),
                    "not_found_count": len(not_found_titles),
                    "not_found_titles": not_found_titles,
                    "navidrome_playlist_id": new_playlist_id
                }
            }
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="添加歌曲到 Navidrome 歌单时发生错误。")

    except Exception as e:
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"处理导入时发生内部服务器错误: {e}")


# --- 本地音乐API路由 ---
@app.get('/api/local/search', dependencies=[Depends(verify_api_key)])
async def handle_local_search(q: str, album: Optional[str] = None, quality: Optional[str] = None):
    data = await run_in_threadpool(local_api.search_song, search_key=q, album=album, quality=quality)
    if not data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"未在本地库中找到歌曲: '{q}'")
    return {"code": 200, "message": "成功", "data": data}

@app.get('/api/local/stream_url/{song_id}', dependencies=[Depends(verify_api_key)])
async def generate_stream_url(song_id: int, request: Request):
    file_path = await run_in_threadpool(local_api.get_song_path_by_id, song_id=song_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="无效的歌曲ID")
    nginx_secret = "YOUR_NGINX_SECRET"
    expires = int(time.time()) + 3 * 3600
    uri_path = f"/secure_media{file_path}"
    string_to_hash = f"{expires}{uri_path} {nginx_secret}"
    md5_hash = hashlib.md5(string_to_hash.encode('utf-8')).digest()
    secure_hash = base64.urlsafe_b64encode(md5_hash).decode('utf-8').replace('=', '')
    base_url = f"https://{request.headers['host']}"
    stream_url = f"{base_url}{uri_path}?md5={secure_hash}&expires={expires}"
    return {"code": 200, "message": "成功", "data": {"url": stream_url, "expires_at": datetime.datetime.fromtimestamp(expires).isoformat()}}

@app.get('/api/local/download/{song_id}', dependencies=[Depends(verify_api_key)])
async def handle_local_download(song_id: int):
    file_path = await run_in_threadpool(local_api.get_song_path_by_id, song_id=song_id)
    if not file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="无效的歌曲ID")
    filename = os.path.basename(file_path)
    response = Response()
    encoded_file_path = quote(file_path)
    response.headers['X-Accel-Redirect'] = f'/internal_media/{encoded_file_path}'
    response.headers['Content-Type'] = 'application/octet-stream'
    response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{quote(filename)}'
    return response

@app.get('/api/local/list', dependencies=[Depends(verify_api_key)])
async def list_local_songs():
    songs_list = await run_in_threadpool(get_all_songs_from_db)
    return {"code": 200, "data": songs_list}

def get_all_songs_from_db():
    """一个同步的辅助函数，用于被线程池调用。"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, search_key, album, quality, file_path FROM songs ORDER BY id DESC")
    songs_tuples = cursor.fetchall()
    conn.close()
    return [dict(id=s[0], search_key=s[1], album=s[2], quality=s[3], file_path=s[4]) for s in songs_tuples]

@app.post('/api/local/delete', dependencies=[Depends(verify_api_key)])
async def delete_local_songs(request: Request):
    req_data = await request.json()
    ids_to_delete = req_data.get('ids')
    if not ids_to_delete or not isinstance(ids_to_delete, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请求体中必须包含一个有效的 'ids' 列表。")
    result = await run_in_threadpool(delete_songs_from_db, ids_to_delete=ids_to_delete)
    if result.get("errors"):
        return JSONResponse(status_code=status.HTTP_207_MULTI_STATUS, content={"code": 207, "message": result["message"], "errors": result["errors"]})
    return {"code": 200, "message": result["message"]}

def delete_songs_from_db(ids_to_delete: List[int]):
    """一个同步的辅助函数，用于被线程池调用以执行删除。"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    deleted_count, errors = 0, []
    try:
        placeholders = ','.join('?' for _ in ids_to_delete)
        cursor.execute(f"SELECT id, file_path, search_key FROM songs WHERE id IN ({placeholders})", ids_to_delete)
        songs_to_delete = cursor.fetchall()
        for song_id, file_path, search_key in songs_to_delete:
            try:
                if os.path.exists(file_path): os.remove(file_path)
                cursor.execute("DELETE FROM songs WHERE id = ?", (song_id,))
                deleted_count += 1
            except Exception as e:
                errors.append(f"删除歌曲 '{search_key}' (ID: {song_id}) 时出错: {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        return {"message": f"数据库操作时发生严重错误: {e}", "errors": [str(e)]}
    finally:
        conn.close()
    
    message = f"成功删除了 {deleted_count} 首歌曲。"
    if errors:
        message = f"操作部分成功，删除了 {deleted_count} 首歌曲。"
    return {"message": message, "errors": errors}

# --------------------------------------------------------------------------
# 4. 定时任务
# --------------------------------------------------------------------------
scheduler = AsyncIOScheduler()

def refresh_qq_cookie_job():
    print("正在执行QQ音乐Cookie刷新任务...")
    refresher = QQCookieRefresher()
    refresher.refresh()

@app.on_event("startup")
async def startup_event():
    scheduler.add_job(refresh_qq_cookie_job, 'interval', hours=23, id='RefreshQQCookie')
    scheduler.start()
    print("FastAPI 应用启动，QQ音乐Cookie定时刷新任务已添加。")

# --------------------------------------------------------------------------
# 5. 启动说明
# --------------------------------------------------------------------------
# 升级后，请在命令行中使用 Uvicorn 来启动服务：
# uvicorn main:app --host 0.0.0.0 --port 5000
#

