import re
import traceback
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.dependencies import netease_api
from core.config import Config

router = APIRouter(prefix="/api/netease", tags=["netease"])


@router.get("/daily_recommend")
async def handle_netease_daily_recommend(wyUserId: Optional[str] = None):
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )
    data = await netease_api.get_daily_recommendations()
    if "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])
    return {"code": 200, "message": "成功", "data": data}


@router.get("/style_tags")
async def handle_get_style_tags(wyUserId: Optional[str] = None):
    if not wyUserId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="必须提供 'wyUserId' 参数以获取专属推荐。",
        )
    data = await netease_api.get_style_recommend_tags(user_id=wyUserId)
    if "error" in data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=data["error"])
    return {"code": 200, "message": "成功", "data": data}


@router.get("/radio/modes")
async def handle_netease_radio_modes(wyUserId: Optional[str] = None):
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


@router.get("/radio")
async def handle_netease_radio(
    mode: str = "DEFAULT",
    limit: int = 3,
    sub_mode: Optional[str] = None,
    wyUserId: Optional[str] = None,
):
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


@router.get("/style_recommend")
async def handle_netease_style_recommend(
    tag_id: str, category_id: str, wyUserId: Optional[str] = None
):
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


class SyncSong(BaseModel):
    songmid: str
    name: str
    singer: str
    source: str

class PlaylistSyncRequest(BaseModel):
    listId: str
    songs: List[SyncSong]

def _parse_playlist_url(url_str: str):
    try:
        if "music.163.com" in url_str:
            platform = "wy"
            id_match = re.search(r"[?&]id=(\d+)", url_str)
            creator_match = re.search(r"[?&]creatorId=(\d+)", url_str)
            playlist_id = id_match.group(1) if id_match else None
            creator_id = creator_match.group(1) if creator_match else None
            if playlist_id:
                return platform, playlist_id, creator_id
        return None, None, None
    except Exception:
        return None, None, None


@router.post("/sync")
async def handle_playlist_sync(sync_request: PlaylistSyncRequest):
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
    print(f"[SYNC] 正在获取 {platform} 歌单 {online_playlist_id} 的当前状态...")
    current_playlist_data = await netease_api.get_playlist_info(online_playlist_id)
    if "error" in current_playlist_data:
        raise HTTPException(
            status_code=404,
            detail=f"获取网易云歌单失败: {current_playlist_data['error']}",
        )
    real_creator_id = current_playlist_data.get("creator_id")
    if real_creator_id not in Config.NETEASE_USERS:
        print(f"[SYNC] 权限错误: 歌单创建者 ({real_creator_id}) 未在配置中找到，无法操作。")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足：您没有配置用户ID为 {real_creator_id} 的Cookie，无法修改此歌单。",
        )
    active_user_id = real_creator_id
    print(f"[SYNC] 鉴权通过。将使用用户 {active_user_id} 的身份进行同步。")
    current_song_ids = set(song["id"] for song in current_playlist_data.get("songs", []))
    target_song_ids = []
    for song in sync_request.songs:
        if song.source == "wy" and song.songmid:
            try:
                raw_id = song.songmid.split("_")[1] if "_" in song.songmid else song.songmid
                target_song_ids.append(raw_id)
            except Exception:
                pass
    target_set = set(target_song_ids)
    songs_to_add = list(target_set - current_song_ids)
    songs_to_delete = list(current_song_ids - target_set)

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


@router.get("")
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
