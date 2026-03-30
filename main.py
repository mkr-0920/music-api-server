import asyncio
import base64
import hashlib
import logging
import os
import pathlib
import re
import sqlite3
import time
import traceback
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from mutagen.flac import FLAC, Picture
from pydantic import BaseModel

from api.kuwo import KuwoMusicAPI
from api.local import LocalMusicAPI
from api.mvsep_api import MVSepAPI
from api.navidrome import NavidromeAPI
from api.netease import NeteaseMusicAPI
from api.qq import QQMusicAPI

# 从我们创建的模块中导入所有API类
from core.config import Config

# qq音乐刷新cookies
from core.qq_refresh.refresher import QQCookieRefresher

# --------------------------------------------------------------------------
# 初始化应用和所有API客户端
# --------------------------------------------------------------------------
app = FastAPI(
    title="全能音乐API服务器",
    description="一个集成了Web界面、智能工具和多源音乐API的私有化解决方案。",
    version="2.0.0",
)


# =====================================================================
# --- 日志过滤配置：防止前端高频轮询刷屏终端 ---
# =====================================================================
class EndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # 如果日志内容里包含我们轮询的接口路径，就返回 False (屏蔽该日志)
        return record.getMessage().find("/api/instrumental/queue_status") == -1


# 将过滤器挂载到 Uvicorn 的访问日志记录器上
logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

# 实例化所有API客户端
local_api = LocalMusicAPI(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(
    Config.NETEASE_USERS,
    local_api,
    Config.MASTER_DIRECTORY,
    Config.FLAC_DIRECTORY,
    Config.LOSSY_DIRECTORY,
)
qq_api = QQMusicAPI(
    local_api, Config.MASTER_DIRECTORY, Config.FLAC_DIRECTORY, Config.LOSSY_DIRECTORY
)
kuwo_api = KuwoMusicAPI()
mvsep_api = MVSepAPI(getattr(Config, "MVSEP_API_KEY", ""))


# --------------------------------------------------------------------------
# API密钥验证 (FastAPI 依赖注入)
# --------------------------------------------------------------------------
def verify_api_key(request: Request):
    provided_key = request.headers.get("X-API-Key") or request.query_params.get(
        "api_key"
    )
    if (
        not hasattr(Config, "API_SECRET_KEY")
        or not provided_key
        or provided_key != Config.API_SECRET_KEY
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败：无效的API密钥。"
        )
    return True


# --------------------------------------------------------------------------
# 定义所有FastAPI路由
# --------------------------------------------------------------------------
@app.get("/")
async def index():
    return {"message": "全能音乐API服务器正在运行"}


# --- 在线音乐API路由 ---
# 网易云日推歌单 (需强制 wyUserId)
@app.get("/api/netease/daily_recommend", dependencies=[Depends(verify_api_key)])
async def handle_netease_daily_recommend(wyUserId: Optional[str] = None):
    """
    获取网易云音乐的每日推荐歌单。
    """
    # --- 路由层校验 ---
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )

    data = await netease_api.get_daily_recommendations()

    if "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    # 返回的是一个歌单对象，包含 playlist_name 和 songs 列表
    return {"code": 200, "message": "成功", "data": data}


# 获取风格标签 (需强制 wyUserId)
@app.get("/api/netease/style_tags", dependencies=[Depends(verify_api_key)])
async def handle_get_style_tags(wyUserId: Optional[str] = None):
    """获取风格日推标签，必须指定 wyUserId"""
    # --- 路由层校验 ---
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )

    data = await netease_api.get_style_recommend_tags(user_id=wyUserId)

    if "error" in data:
        # 如果是 400 错误（如用户ID未配置），这里也可以透传
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    return {"code": 200, "message": "成功", "data": data}


# 获取私人FM模式 (需强制 wyUserId)
@app.get("/api/netease/radio/modes", dependencies=[Depends(verify_api_key)])
async def handle_netease_radio_modes(wyUserId: Optional[str] = None):
    """获取私人FM模式列表，必须指定 wyUserId"""
    # --- 路由层校验 ---
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )

    data = await netease_api.get_private_fm_modes(user_id=wyUserId)

    if "error" in data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=data["error"]
        )

    return {"code": 200, "message": "成功", "data": data}


# 获取私人FM歌曲 (需强制 wyUserId)
@app.get("/api/netease/radio", dependencies=[Depends(verify_api_key)])
async def handle_netease_radio(
    mode: str = "DEFAULT",
    limit: int = 3,
    sub_mode: Optional[str] = None,
    wyUserId: Optional[str] = None,
):
    """获取私人FM歌曲，必须指定 wyUserId"""
    # --- 路由层校验 ---
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )

    if mode == "SCENE_RCMD" and not sub_mode:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="当 mode 为 SCENE_RCMD 时，必须提供 sub_mode 参数。",
        )

    data = await netease_api.get_private_fm(mode, limit, sub_mode, user_id=wyUserId)

    if isinstance(data, dict) and "error" in data:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=data["error"]
        )

    return {"code": 200, "message": "成功", "data": data}


# 获取风格日推歌单 (需强制 wyUserId)
@app.get("/api/netease/style_recommend", dependencies=[Depends(verify_api_key)])
async def handle_netease_style_recommend(
    tag_id: str, category_id: str, wyUserId: Optional[str] = None
):
    """获取指定风格推荐歌单，必须指定 wyUserId"""
    # --- 路由层校验 ---
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )

    if not tag_id or not category_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'tag_id' 和 'category_id' 参数。",
        )

    data = await netease_api.get_style_recommend_playlist(
        tag_id, category_id, user_id=wyUserId
    )

    if "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    return {"code": 200, "message": "成功", "data": data}


# 网易云歌单同步
class SyncSong(BaseModel):
    songmid: str
    name: str
    singer: str
    source: str


class PlaylistSyncRequest(BaseModel):
    listId: str  # 现在将接收一个 URL 字符串
    songs: List[SyncSong]


def _parse_playlist_url(url_str: str) -> (Optional[str], Optional[str], Optional[str]):
    """使用正则表达式解析在线歌单URL，返回 (platform, playlist_id, creator_id)"""
    try:
        # 尝试匹配网易云歌单 URL
        if "music.163.com" in url_str:
            platform = "wy"

            # 使用两次搜索来分别、安全地提取ID
            id_match = re.search(r"[?&]id=(\d+)", url_str)
            creator_match = re.search(r"[?&]creatorId=(\d+)", url_str)

            playlist_id = id_match.group(1) if id_match else None
            creator_id = creator_match.group(1) if creator_match else None

            if playlist_id:
                return platform, playlist_id, creator_id

        # ... (未来可以添加对QQ音乐的 regex 匹配) ...

        return None, None, None
    except Exception:
        return None, None, None


@app.post("/api/netease/sync", dependencies=[Depends(verify_api_key)])
async def handle_playlist_sync(sync_request: PlaylistSyncRequest):
    """
    执行“增量同步”。
    自动根据歌单创建者ID切换对应的Cookie进行操作，实现多用户支持。
    """
    # 1. 解析 URL 获取 ID
    platform, online_playlist_id, _ = _parse_playlist_url(sync_request.listId)

    if not platform:
        if sync_request.listId.isdigit():
            platform, online_playlist_id = "wy", sync_request.listId
        else:
            raise HTTPException(status_code=400, detail="listId 格式不正确。")

    if platform != "wy":
        raise HTTPException(
            status_code=400, detail=f"平台 '{platform}' 尚不支持同步功能。"
        )

    print(f"--- [SYNC] 开始同步歌单: {online_playlist_id} ---")

    try:
        # 2. 获取网易云的【当前状态】（包含创建者信息）
        print(f"[SYNC] 正在获取 {platform} 歌单 {online_playlist_id} 的当前状态...")
        # 这里使用默认 Cookie 获取信息即可，读操作通常权限较低
        current_playlist_data = await netease_api.get_playlist_info(online_playlist_id)

        if "error" in current_playlist_data:
            raise HTTPException(
                status_code=404,
                detail=f"获取网易云歌单失败: {current_playlist_data['error']}",
            )

        # --- 多用户鉴权与切换 ---
        real_creator_id = current_playlist_data.get("creator_id")

        # 检查该创建者ID是否在我们的配置中
        if real_creator_id not in Config.NETEASE_USERS:
            print(
                f"[SYNC] 权限错误: 歌单创建者 ({real_creator_id}) 未在配置中找到，无法操作。"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：您没有配置用户ID为 {real_creator_id} 的Cookie，无法修改此歌单。",
            )

        # 既然在配置中，说明我们有权操作。后续操作将使用这个 ID。
        active_user_id = real_creator_id
        print(f"[SYNC] 鉴权通过。将使用用户 {active_user_id} 的身份进行同步。")
        # -----------------------------------

        # 3. 准备数据
        current_song_ids = set(
            song["id"] for song in current_playlist_data.get("songs", [])
        )

        target_song_ids = []
        for song in sync_request.songs:
            if song.source == "wy" and song.songmid:
                try:
                    raw_id = (
                        song.songmid.split("_")[1]
                        if "_" in song.songmid
                        else song.songmid
                    )
                    target_song_ids.append(raw_id)
                except Exception:
                    pass
        target_set = set(target_song_ids)

        # 4. 计算差异
        songs_to_add = list(target_set - current_song_ids)
        songs_to_delete = list(current_song_ids - target_set)

        # 5. 执行操作 (注意：这里都传入了 active_user_id)
        if songs_to_delete:
            print(f"[SYNC] 正在删除 {len(songs_to_delete)} 首歌曲...")
            await netease_api.remove_songs_from_playlist(
                online_playlist_id, songs_to_delete, user_id=active_user_id
            )

        if songs_to_add:
            print(f"[SYNC] 正在添加 {len(songs_to_add)} 首歌曲...")
            await netease_api.add_songs_to_playlist(
                online_playlist_id, songs_to_add, user_id=active_user_id
            )

        if target_song_ids:
            print("[SYNC] 正在更新歌单顺序...")
            await netease_api.reorder_playlist(
                online_playlist_id, target_song_ids, user_id=active_user_id
            )

        print(f"--- [SYNC] 歌单 {online_playlist_id} 同步成功! ---")
        return {
            "code": 200,
            "message": "同步成功",
            "data": {"added": len(songs_to_add), "deleted": len(songs_to_delete)},
        }

    except Exception as e:
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"同步时发生内部服务器错误: {e}")


@app.get("/api/netease", dependencies=[Depends(verify_api_key)])
async def handle_netease_request(
    background_tasks: BackgroundTasks,
    id: Optional[str] = None,
    q: Optional[str] = None,
    album: Optional[str] = None,
    playlist_id: Optional[str] = None,
    album_id: Optional[str] = None,
    level: str = "hires",
):
    if playlist_id:
        background_tasks.add_task(
            netease_api.start_background_playlist_download, playlist_id, level
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "code": 202,
                "message": "任务已接受",
                "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"},
            },
        )
    elif album_id:
        background_tasks.add_task(
            netease_api.start_background_album_download, album_id, level
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "code": 202,
                "message": "任务已接受",
                "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"},
            },
        )

    if id:
        data = await netease_api.get_song_details(id, level)
    elif q:
        data = await netease_api.search_and_get_details(q, level, album)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'id', 'q', 'playlist_id' 或 'album_id' 参数之一。",
        )

    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    return {"code": 200, "message": "成功", "data": data}


@app.get("/api/qq", dependencies=[Depends(verify_api_key)])
async def handle_qq_request(
    background_tasks: BackgroundTasks,
    mid: Optional[str] = None,
    id: Optional[int] = None,
    q: Optional[str] = None,
    album: Optional[str] = None,
    playlist_id: Optional[str] = None,
    album_id: Optional[int] = None,
    album_mid: Optional[str] = None,
    level: str = "master",
):
    if playlist_id:
        background_tasks.add_task(
            qq_api.start_background_playlist_download, playlist_id, level
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "code": 202,
                "message": "任务已接受",
                "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"},
            },
        )
    elif album_id or album_mid:
        # 合并处理：优先取 album_id 并转为字符串，没有则取 album_mid
        target_album = str(album_id) if album_id else album_mid
        background_tasks.add_task(
            qq_api.start_background_album_download, target_album, level
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "code": 202,
                "message": "任务已接受",
                "data": {"message": f"专辑 {target_album} 已加入后台下载队列。"},
            },
        )

    if id or mid:
        data = await qq_api.get_song_details(song_mid=mid, song_id=id)
    elif q:
        data = await qq_api.search_and_get_details(q, album=album)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'mid', 'id', 'q', 'playlist_id', 'album_id' 或 'album_mid' 参数之一。",
        )

    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    return {"code": 200, "message": "成功", "data": data}


@app.get("/api/playlist/info", dependencies=[Depends(verify_api_key)])
async def handle_playlist_info_request(platform: str, id: str):
    data = None
    if platform.lower() == "netease":
        data = await netease_api.get_playlist_info(id)
    elif platform.lower() == "qq":
        data = await qq_api.get_playlist_info(id)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不支持的平台，请选择 'qq' 或 'netease'。",
        )

    if data and "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])

    return {"code": 200, "message": "成功", "data": data}


class NavidromeImportRequest(BaseModel):
    navidrome_url: str
    username: str
    password: str
    platform: str
    online_playlist_id: str


@app.post("/api/navidrome/import", dependencies=[Depends(verify_api_key)])
async def handle_navidrome_import(import_request: NavidromeImportRequest):
    """接收一个简单的导入指令，由后端完成所有处理流程。"""
    try:
        platform = import_request.platform
        online_playlist_id = import_request.online_playlist_id

        # --- 后端自己获取在线歌单信息 ---
        online_playlist = None
        if platform.lower() == "netease":
            online_playlist = await netease_api.get_playlist_info(online_playlist_id)
        elif platform.lower() == "qq":
            online_playlist = await qq_api.get_playlist_info(online_playlist_id)

        if not online_playlist or "error" in online_playlist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"从 {platform.upper()} 获取歌单信息失败: {online_playlist.get('error', '未知错误')}",
            )

        playlist_name = online_playlist.get("playlist_name")
        songs = online_playlist.get("songs", [])

        # --- 执行导入逻辑 ---
        # 数据库检查是同步的 -> 使用线程池
        existing_navidrome_id = await run_in_threadpool(
            local_api.get_mapping_for_online_playlist, platform, online_playlist_id
        )
        if existing_navidrome_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"此歌单已被导入，对应的 Navidrome 歌单ID为 '{existing_navidrome_id}'。如需更新，请使用同步脚本。",
            )

        navi_api = NavidromeAPI(
            import_request.navidrome_url,
            import_request.username,
            import_request.password,
        )
        if not await navi_api.login():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Navidrome 登录失败。"
            )

        new_playlist_id = await navi_api.create_playlist(playlist_name)
        if not new_playlist_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="在 Navidrome 中创建歌单失败。",
            )

        # 并发搜索所有歌曲
        search_tasks = [
            navi_api.search_song(s.get("artist"), s.get("name"), s.get("album"))
            for s in songs
            if s.get("artist") and s.get("name")
        ]
        search_results = await asyncio.gather(*search_tasks)

        found_ids = [song_id for song_id in search_results if song_id]
        not_found_titles = [
            f"{s.get('artist')} - {s.get('name')}"
            for i, s in enumerate(songs)
            if not search_results[i]
        ]

        if await navi_api.add_songs_to_playlist(new_playlist_id, found_ids):
            # 数据库写入是同步的 -> 使用线程池
            await run_in_threadpool(
                local_api.add_playlist_mapping,
                platform,
                online_playlist_id,
                new_playlist_id,
                playlist_name,
            )
            await run_in_threadpool(local_api.update_sync_time, new_playlist_id)

            return {
                "code": 200,
                "message": "导入任务完成。",
                "data": {
                    "playlist_name": playlist_name,
                    "total_songs": len(songs),
                    "added_count": len(found_ids),
                    "not_found_count": len(not_found_titles),
                    "not_found_titles": not_found_titles,
                    "navidrome_playlist_id": new_playlist_id,
                },
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="添加歌曲到 Navidrome 歌单时发生错误。",
            )

    except Exception as e:
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(
            status_code=500, detail=f"处理导入时发生内部服务器错误: {e}"
        )


# --- 本地音乐API路由 ---
@app.get("/api/local/search", dependencies=[Depends(verify_api_key)])
async def handle_local_search_advanced(
    q: str, mode: str = "any", limit: int = 20, offset: int = 0
):
    """
    在本地音乐库中执行高级搜索。
    - q: 搜索关键词
    - mode: 搜索模式 (any, artist, album, title)，默认为 'any'
    - limit: 返回数量 (album 模式下无效)
    - offset: 结果偏移
    """
    if not q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="缺少查询参数 'q'"
        )

    data = await run_in_threadpool(
        local_api.search_local_music, query=q, mode=mode, limit=limit, offset=offset
    )

    if not data or data["total_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未在本地库中找到与 '{q}' 相关的结果",
        )

    # 返回一个包含 'songs' 列表和 'total_count' 的完整对象
    return {"code": 200, "message": "成功", "data": data}


@app.get("/api/local/play_info/{song_id}", dependencies=[Depends(verify_api_key)])
async def get_local_play_info(song_id: int, request: Request):
    """
    获取本地歌曲的完整播放信息。
    封面URL现在也会根据是否通过CDN访问而自动切换域名。
    """
    try:
        # 从本地数据库获取所有元数据和歌词
        song_details = await run_in_threadpool(
            local_api.get_song_details_by_id, song_id=song_id
        )
        if not song_details:
            raise HTTPException(status_code=404, detail="本地歌曲ID不存在。")

        # 生成流媒体URL (generate_secure_url 内部已经处理了 CDN 逻辑)
        stream_url = await generate_secure_url(song_details["file_path"], request)

        # 智能生成封面 URL 的域名 (使用拼接)
        current_host = request.headers.get("host")

        if request.headers.get("x-is-cdn") == "yes":
            base_host = f"cdn-{current_host}"
        elif request.headers.get("x-forwarded-host"):
            base_host = request.headers.get("x-forwarded-host")
        else:
            base_host = current_host

        base_url = f"https://{base_host}"
        cover_url = f"{base_url}/api/local/cover/{song_id}"

        # 4. 组合成最终的播放信息
        song_details["url"] = stream_url
        song_details["cover_url"] = cover_url

        return {"code": 200, "message": "成功", "data": song_details}

    except Exception as e:
        traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"获取播放信息时发生内部错误: {e}")


@app.get("/api/local/cover/{song_id}")
async def get_local_cover_art(song_id: int):
    """
    从数据库中获取并返回歌曲的封面图片。
    """
    cover_info = await run_in_threadpool(local_api.get_cover_art_by_id, song_id=song_id)
    if not cover_info or not cover_info["image_data"]:
        raise HTTPException(status_code=404, detail="未找到此歌曲的封面。")

    return Response(
        content=cover_info["image_data"], media_type=cover_info["mime_type"]
    )


# --- 辅助函数 ---
async def generate_secure_url(file_path: str, request: Request) -> str:
    """
    为一首本地歌曲生成一个临时的、安全的可流媒体播放链接。
    能够根据请求来源（是否来自CDN）智能切换返回的域名。
    """
    nginx_secret = "YOUR_NGINX_SECRET"  # 必须与 nginx.conf 中的密钥相同
    expires = int(time.time()) + 3 * 3600
    uri_path = f"/secure_media{file_path}"

    # 生成签名
    string_to_hash = f"{expires}{uri_path} {nginx_secret}"
    md5_hash = hashlib.md5(string_to_hash.encode("utf-8")).digest()
    secure_hash = base64.urlsafe_b64encode(md5_hash).decode("utf-8").replace("=", "")

    current_host = request.headers.get("host")
    if request.headers.get("x-is-cdn") == "yes":
        # 动态拼接
        base_host = f"cdn-{current_host}"
    elif request.headers.get("x-forwarded-host"):
        base_host = request.headers.get("x-forwarded-host")
    else:
        base_host = current_host

    # 构造最终 URL
    return f"https://{base_host}{uri_path}?md5={secure_hash}&expires={expires}"


@app.get("/api/local/download/{song_id}", dependencies=[Depends(verify_api_key)])
async def handle_local_download(song_id: int):
    file_path = await run_in_threadpool(local_api.get_song_path_by_id, song_id=song_id)
    if not file_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="无效的歌曲ID"
        )
    filename = os.path.basename(file_path)
    response = Response()
    encoded_file_path = quote(file_path)
    response.headers["X-Accel-Redirect"] = f"/internal_media/{encoded_file_path}"
    response.headers["Content-Type"] = "application/octet-stream"
    response.headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(filename)}"
    )
    return response


@app.get("/api/local/list", dependencies=[Depends(verify_api_key)])
async def list_local_songs():
    songs_list = await run_in_threadpool(get_all_songs_from_db)
    return {"code": 200, "data": songs_list}


def get_all_songs_from_db():
    """一个同步的辅助函数，用于被线程池调用。"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, search_key, album, quality, file_path FROM songs ORDER BY id DESC"
    )
    songs_tuples = cursor.fetchall()
    conn.close()
    return [
        dict(id=s[0], search_key=s[1], album=s[2], quality=s[3], file_path=s[4])
        for s in songs_tuples
    ]


@app.post("/api/local/delete", dependencies=[Depends(verify_api_key)])
async def delete_local_songs(request: Request):
    req_data = await request.json()
    ids_to_delete = req_data.get("ids")
    if not ids_to_delete or not isinstance(ids_to_delete, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求体中必须包含一个有效的 'ids' 列表。",
        )
    result = await run_in_threadpool(delete_songs_from_db, ids_to_delete=ids_to_delete)
    if result.get("errors"):
        return JSONResponse(
            status_code=status.HTTP_207_MULTI_STATUS,
            content={
                "code": 207,
                "message": result["message"],
                "errors": result["errors"],
            },
        )
    return {"code": 200, "message": result["message"]}


def delete_songs_from_db(ids_to_delete: List[int]):
    """一个同步的辅助函数，用于被线程池调用以执行删除。"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    deleted_count, errors = 0, []
    try:
        placeholders = ",".join("?" for _ in ids_to_delete)
        cursor.execute(
            f"SELECT id, file_path, search_key FROM songs WHERE id IN ({placeholders})",
            ids_to_delete,
        )
        songs_to_delete = cursor.fetchall()
        for song_id, file_path, search_key in songs_to_delete:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
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


# --- MVSep AI 伴奏分离专用路由与任务 ---


# 定义请求模型
class BatchInstrumentalSubmitRequest(BaseModel):
    song_ids: List[int]


# 全局队列与状态存储 (存放在内存中，供前端随时查询)
instrumental_queue = asyncio.Queue()
# 格式: { song_id: {"status": "waiting|processing|downloading|done|failed", "message": "...", "hash": "..."} }
instrumental_task_status: Dict[int, Dict[str, Any]] = {}


async def background_download_instrumental(song_id: int, download_url: str):
    """
    后台任务：下载伴奏 -> 物理克隆全部元数据（包含歌词、时长） -> 直接入库。
    """
    print(f"[MVSep后台] 开始处理歌曲 ID: {song_id} 的伴奏下载与封装任务...")

    # 1. 查出原曲的元数据、物理路径、封面
    orig_song = await run_in_threadpool(
        local_api.get_song_details_by_id, song_id=song_id
    )
    if not orig_song:
        print(f"[MVSep后台] 错误: 找不到 ID为 {song_id} 的原曲。")
        return

    orig_file_path = orig_song.get("file_path")
    if not orig_file_path:
        print("[MVSep后台] 错误: 原曲路径为空。")
        return

    cover_info = await run_in_threadpool(local_api.get_cover_art_by_id, song_id=song_id)

    # 2. 生成伴奏文件名和路径
    orig_path_obj = pathlib.Path(orig_file_path)
    orig_stem = orig_path_obj.stem
    new_filename = f"{orig_stem} (Instrumental).flac"

    instrumental_dir = getattr(Config, "INSTRUMENTAL_DIRECTORY", None)
    if not instrumental_dir:
        print("[MVSep后台] 错误: 配置文件中未定义 INSTRUMENTAL_DIRECTORY。")
        return

    os.makedirs(instrumental_dir, exist_ok=True)
    save_path = os.path.join(instrumental_dir, new_filename)

    # 3. 执行下载
    success = await mvsep_api.download_track(download_url, save_path)
    if not success:
        print("[MVSep后台] ❌ 伴奏下载失败。")
        return

    print("[MVSep后台] 伴奏落盘成功，正在物理注入原曲灵魂（包含歌词）...")

    # 4. 物理写入 FLAC 元数据 (100% 完美复刻)
    def embed_tags():
        audio = FLAC(save_path)
        audio["title"] = f"{orig_song.get('title', '未知')} (Instrumental)"
        if orig_song.get("artist"):
            audio["artist"] = orig_song["artist"]
        if orig_song.get("album"):
            audio["album"] = orig_song["album"]
        if orig_song.get("albumartist"):
            audio["albumartist"] = orig_song["albumartist"]
        if orig_song.get("composer"):
            audio["composer"] = orig_song["composer"]
        if orig_song.get("lyricist"):
            audio["lyricist"] = orig_song["lyricist"]
        if orig_song.get("arranger"):
            audio["arranger"] = orig_song["arranger"]
        if orig_song.get("genre"):
            audio["genre"] = orig_song["genre"]
        if orig_song.get("date"):
            audio["date"] = str(orig_song["date"])
        if orig_song.get("year"):
            audio["year"] = str(orig_song["year"])
        if orig_song.get("tracknumber"):
            audio["tracknumber"] = str(orig_song["tracknumber"])
        if orig_song.get("discnumber"):
            audio["discnumber"] = str(orig_song["discnumber"])
        if orig_song.get("bpm"):
            audio["bpm"] = str(orig_song["bpm"])

        # 硬核注入原唱歌词，K歌必备！
        orig_lyrics = orig_song.get("lyric") or orig_song.get("lyrics")
        if orig_lyrics:
            audio["lyrics"] = orig_lyrics

        if cover_info and cover_info.get("image_data"):
            picture = Picture()
            picture.type = 3
            picture.mime = cover_info.get("mime_type", "image/jpeg")
            picture.desc = "Cover"
            picture.data = cover_info["image_data"]
            audio.add_picture(picture)

        audio.save()

    try:
        await run_in_threadpool(embed_tags)
        print("[MVSep后台] 物理元数据注入完成！")
    except Exception as e:
        print(f"[MVSep后台] ⚠️ 物理注入元数据失败，但不影响入库: {e}")

    # 5. 纯粹的基因克隆：原封不动照搬原曲所有元数据
    db_song_info = {
        "search_key": orig_song.get("search_key"),
        "title": f"{orig_song.get('title', '未知')} (Instrumental)",
        "is_instrumental": 1,
        "duration_ms": orig_song.get("duration_ms", 0),  # 毫秒不差继承
        "album": orig_song.get("album"),
        "artist": orig_song.get("artist"),
        "albumartist": orig_song.get("albumartist"),
        "composer": orig_song.get("composer"),
        "lyricist": orig_song.get("lyricist"),
        "arranger": orig_song.get("arranger"),
        "bpm": orig_song.get("bpm"),
        "genre": orig_song.get("genre"),
        "tracknumber": orig_song.get("tracknumber"),
        "discnumber": orig_song.get("discnumber"),
        "date": orig_song.get("date"),
        "year": orig_song.get("year"),
    }

    await run_in_threadpool(
        local_api.add_song_to_db,
        song_info=db_song_info,
        file_path=save_path,
        quality="flac",
        lyric=orig_song.get("lyric") or orig_song.get("lyrics"),  # 完整填入原首歌词
        tlyric=orig_song.get("tlyric"),  # 完整填入翻译歌词
        cover_data=cover_info.get("image_data") if cover_info else None,
        cover_mime=cover_info.get("mime_type") if cover_info else None,
    )

    print(
        f"[MVSep后台] 🎉 全流程搞定！伴奏 '{db_song_info['title']}' 已秒速加入本地曲库！"
    )


# 流水线单兵作战函数 (上传 -> 轮询 -> 调用上面的克隆函数)
async def _process_instrumental_pipeline(song_id: int):
    """处理一首歌曲的完整分离生命周期"""
    instrumental_task_status[song_id] = {
        "status": "processing",
        "message": "正在获取本地文件...",
    }
    file_path = await run_in_threadpool(local_api.get_song_path_by_id, song_id=song_id)
    if not file_path or not os.path.exists(file_path):
        instrumental_task_status[song_id] = {
            "status": "failed",
            "message": "本地文件不存在",
        }
        return

    # 上传
    instrumental_task_status[song_id] = {
        "status": "processing",
        "message": "正在上传至 MVSep AI 算力集群...",
    }
    result = await mvsep_api.create_separation(file_path)  # 内部已经写死用 40 模型
    if "error" in result:
        instrumental_task_status[song_id] = {
            "status": "failed",
            "message": result["error"],
        }
        return

    task_hash = result.get("data", {}).get("hash")
    if not task_hash:
        instrumental_task_status[song_id] = {
            "status": "failed",
            "message": "API未返回有效的任务Hash",
        }
        return

    instrumental_task_status[song_id] = {
        "status": "processing",
        "message": "任务已排队，等待 AI 处理...",
        "hash": task_hash,
    }

    # 无限轮询
    while True:
        await asyncio.sleep(6)  # 官方建议轮询间隔
        status_res = await mvsep_api.get_separation_status(task_hash)

        if "error" in status_res:
            instrumental_task_status[song_id] = {
                "status": "failed",
                "message": status_res["error"],
            }
            return

        mvsep_status = status_res.get("status")
        inner_data = status_res.get("data", {})

        if mvsep_status in ["failed", "not_found"]:
            instrumental_task_status[song_id] = {
                "status": "failed",
                "message": inner_data.get("message", "MVSep 处理失败"),
            }
            return

        if mvsep_status == "done":
            # 狙击 Other 轨道
            files = inner_data.get("files", [])
            instrumental_obj = next(
                (f for f in files if f.get("type") == "Other"), None
            )
            if not instrumental_obj or not instrumental_obj.get("url"):
                instrumental_task_status[song_id] = {
                    "status": "failed",
                    "message": "API 返回成功，但未找到 Other 音轨链接",
                }
                return
            download_url = instrumental_obj.get("url")
            break

        # 更新状态指示给前端
        instrumental_task_status[song_id] = {
            "status": "processing",
            "message": f"AI 处理中 [{mvsep_status}]...",
            "hash": task_hash,
        }

    # 执行下载入库
    instrumental_task_status[song_id] = {
        "status": "downloading",
        "message": "AI提取完毕，正在下载克隆入库...",
    }
    try:
        await background_download_instrumental(song_id, download_url)
        instrumental_task_status[song_id] = {
            "status": "done",
            "message": "伴奏提取入库成功！",
        }
    except Exception as e:
        instrumental_task_status[song_id] = {
            "status": "failed",
            "message": f"入库报错: {str(e)}",
        }


# 后台队列消费者 (Worker)
async def mvsep_queue_worker():
    """永不停止的后台打工人，串行消费队列里的任务"""
    print("[MVSep Worker] 伴奏流水线启动，等待任务...")
    while True:
        song_id = await instrumental_queue.get()
        try:
            await _process_instrumental_pipeline(song_id)
        except Exception as e:
            print(f"[MVSep后台] 处理 ID {song_id} 时发生未捕获异常: {e}")
            instrumental_task_status[song_id] = {
                "status": "failed",
                "message": f"系统异常: {e}",
            }
        finally:
            instrumental_queue.task_done()


@app.post("/api/instrumental/batch_submit", dependencies=[Depends(verify_api_key)])
async def submit_batch_instrumental_tasks(req: BatchInstrumentalSubmitRequest):
    """将一批歌曲加入伴奏分离队列"""
    added_count = 0
    for sid in req.song_ids:
        # 如果这首歌不在状态字典里，或者之前处理完成/失败了，允许重新加入队列
        if sid not in instrumental_task_status or instrumental_task_status[sid][
            "status"
        ] in ["done", "failed"]:
            instrumental_task_status[sid] = {
                "status": "waiting",
                "message": "已加入队列排队",
            }
            await instrumental_queue.put(sid)
            added_count += 1

    return {"code": 200, "message": f"成功将 {added_count} 个任务加入流水线。"}


@app.get("/api/instrumental/queue_status", dependencies=[Depends(verify_api_key)])
async def get_instrumental_queue_status():
    """前端定期调此接口，获取所有任务的最新进度并刷新UI"""
    return {"code": 200, "data": instrumental_task_status}


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


# --------------------------------------------------------------------------
# 启动说明
# --------------------------------------------------------------------------
# 升级后，请在命令行中使用 Uvicorn 来启动服务：
# uvicorn main:app --host 0.0.0.0 --port 5000
#
