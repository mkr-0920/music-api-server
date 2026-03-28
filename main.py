import asyncio
import base64
import hashlib
import os
import re
import sqlite3
import time
import traceback
from typing import List, Optional
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
from pydantic import BaseModel

from api.kuwo import KuwoMusicAPI
from api.local import LocalMusicAPI
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

# 实例化所有API客户端
local_api = LocalMusicAPI(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(
    Config.NETEASE_USERS, local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY
)
qq_api = QQMusicAPI(local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)
kuwo_api = KuwoMusicAPI()


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
    album_id: Optional[str] = None,
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
    elif album_id:
        background_tasks.add_task(
            qq_api.start_background_album_download, album_id, level
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "code": 202,
                "message": "任务已接受",
                "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"},
            },
        )

    if id or mid:
        data = await qq_api.get_song_details(song_mid=mid, song_id=id)
    elif q:
        data = await qq_api.search_and_get_details(q, album=album)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'mid', 'id', 'q', 'playlist_id' 或 'album_id' 参数之一。",
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
    # refresh_qq_cookie_job()
    scheduler.add_job(refresh_qq_cookie_job, "interval", hours=23, id="RefreshQQCookie")
    scheduler.start()
    print("FastAPI 应用启动，QQ音乐Cookie定时刷新任务已添加。")


# --------------------------------------------------------------------------
# 启动说明
# --------------------------------------------------------------------------
# 升级后，请在命令行中使用 Uvicorn 来启动服务：
# uvicorn main:app --host 0.0.0.0 --port 5000
#
