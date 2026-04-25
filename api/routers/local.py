import base64
import hashlib
import os
import time
import traceback
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from api.dependencies import db_manager, verify_api_key
from core.config import Config

router = APIRouter(prefix="/api/local", tags=["local"])


@router.get("/search", dependencies=[Depends(verify_api_key)])
async def handle_local_search_advanced(
    q: str, mode: str = "any", limit: int = 20, offset: int = 0
):
    if not q:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="缺少查询参数 'q'"
        )

    data = await db_manager.search_local_music(
query=q, mode=mode, limit=limit, offset=offset
)

    if not data or data["total_count"] == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未在本地库中找到与 '{q}' 相关的结果",
        )

    return {"code": 200, "message": "成功", "data": data}


@router.get("/play_info/{song_id}", dependencies=[Depends(verify_api_key)])
async def get_local_play_info(song_id: int, request: Request):
    song_details = await db_manager.get_song_details_by_id(song_id=song_id)
    if not song_details:
        raise HTTPException(status_code=404, detail="本地歌曲ID不存在。")

    stream_url = await generate_secure_url(song_details["file_path"], request)
    current_host = request.headers.get("host")

    if request.headers.get("x-is-cdn") == "yes":
        base_host = f"cdn-{current_host}"
    elif request.headers.get("x-forwarded-host"):
        base_host = request.headers.get("x-forwarded-host")
    else:
        base_host = current_host

    base_url = f"https://{base_host}"
    cover_url = f"{base_url}/api/local/cover/{song_id}"

    song_details["url"] = stream_url
    song_details["cover_url"] = cover_url

    return {"code": 200, "message": "成功", "data": song_details}


@router.get("/cover/{song_id}")
async def get_local_cover_art(song_id: int):
    cover_info = await db_manager.get_cover_art_by_id(song_id=song_id)
    if not cover_info or not cover_info["image_data"]:
        raise HTTPException(status_code=404, detail="未找到此歌曲的封面。")

    return Response(
        content=cover_info["image_data"], media_type=cover_info["mime_type"]
    )


async def generate_secure_url(file_path: str, request: Request) -> str:
    expires = int(time.time()) + 1 * 3600
    uri_path = f"/secure_media{file_path}"
    string_to_hash = f"{expires}{uri_path} {Config.ORIGIN_SECRET_KEY}"
    md5_hash = hashlib.md5(string_to_hash.encode("utf-8")).digest()
    secure_hash = base64.urlsafe_b64encode(md5_hash).decode("utf-8").replace("=", "")
    current_host = request.headers.get("host")
    if request.headers.get("x-is-cdn") == "yes":
        base_host = f"cdn-{current_host}"
    elif request.headers.get("x-forwarded-host"):
        base_host = request.headers.get("x-forwarded-host")
    else:
        base_host = current_host
    return f"https://{base_host}{uri_path}?md5={secure_hash}&expires={expires}"


@router.get("/download/{song_id}", dependencies=[Depends(verify_api_key)])
async def handle_local_download(song_id: int):
    file_path = await db_manager.get_song_path_by_id(song_id=song_id)
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


@router.get("/list", dependencies=[Depends(verify_api_key)])
async def list_local_songs():
    songs_list = await db_manager.get_all_songs()
    return {"code": 200, "data": songs_list}


@router.post("/delete", dependencies=[Depends(verify_api_key)])
async def delete_local_songs(request: Request):
    req_data = await request.json()
    ids_to_delete = req_data.get("ids")
    if not ids_to_delete or not isinstance(ids_to_delete, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请求体中必须包含一个有效的 'ids' 列表。",
        )
    result = await db_manager.delete_songs(ids_to_delete=ids_to_delete)
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
