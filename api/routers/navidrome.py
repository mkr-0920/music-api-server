import asyncio
import traceback

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from api.dependencies import db_manager, netease_api, qq_api
from api.navidrome import NavidromeAPI

router = APIRouter(prefix="/api/navidrome", tags=["navidrome"])


class NavidromeImportRequest(BaseModel):
    navidrome_url: str
    username: str
    password: str
    platform: str
    online_playlist_id: str


@router.post("/import")
async def handle_navidrome_import(import_request: NavidromeImportRequest):
    """接收一个简单的导入指令，由后端完成所有处理流程。"""
    platform = import_request.platform
    online_playlist_id = import_request.online_playlist_id

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

    existing_navidrome_id = await db_manager.get_mapping_for_online_playlist(platform, online_playlist_id)
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
        await db_manager.add_playlist_mapping(
            platform,
            online_playlist_id,
            new_playlist_id,
            playlist_name,
        )
        await db_manager.update_sync_time(new_playlist_id)

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
