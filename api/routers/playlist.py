from fastapi import APIRouter, HTTPException, status
from api.dependencies import netease_api, qq_api

router = APIRouter(prefix="/api/playlist", tags=["playlist"])

@router.get("/info")
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
