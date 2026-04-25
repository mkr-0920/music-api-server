from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from api.dependencies import qq_api

router = APIRouter(prefix="/api/qq", tags=["qq"])


@router.get("")
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
