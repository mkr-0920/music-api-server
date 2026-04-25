import asyncio
from fastapi.concurrency import run_in_threadpool
import os
import pathlib
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, status
from mutagen.flac import FLAC, Picture
from pydantic import BaseModel

from api.dependencies import db_manager, mvsep_api
from core.config import Config

router = APIRouter(prefix="/api/instrumental", tags=["instrumental"])


class BatchInstrumentalSubmitRequest(BaseModel):
    song_ids: List[int]


instrumental_queue = asyncio.Queue()
instrumental_task_status: Dict[int, Dict[str, Any]] = {}


async def background_download_instrumental(song_id: int, download_url: str):
    print(f"[MVSep后台] 开始处理歌曲 ID: {song_id} 的伴奏下载与封装任务...")

    orig_song = await db_manager.get_song_details_by_id(
song_id=song_id
)
    if not orig_song:
        print(f"[MVSep后台] 错误: 找不到 ID为 {song_id} 的原曲。")
        return

    orig_file_path = orig_song.get("file_path")
    if not orig_file_path:
        print("[MVSep后台] 错误: 原曲路径为空。")
        return

    cover_info = await db_manager.get_cover_art_by_id(song_id=song_id)

    orig_path_obj = pathlib.Path(orig_file_path)
    orig_stem = orig_path_obj.stem
    new_filename = f"{orig_stem} (Instrumental).flac"

    instrumental_dir = getattr(Config, "INSTRUMENTAL_DIRECTORY", None)
    if not instrumental_dir:
        print("[MVSep后台] 错误: 配置文件中未定义 INSTRUMENTAL_DIRECTORY。")
        return

    os.makedirs(instrumental_dir, exist_ok=True)
    save_path = os.path.join(instrumental_dir, new_filename)

    success = await mvsep_api.download_track(download_url, save_path)
    if not success:
        print("[MVSep后台] ❌ 伴奏下载失败。")
        return

    print("[MVSep后台] 伴奏落盘成功，正在物理注入原曲灵魂（包含歌词）...")

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

    db_song_info = {
        "search_key": orig_song.get("search_key"),
        "title": f"{orig_song.get('title', '未知')} (Instrumental)",
        "is_instrumental": 1,
        "duration_ms": orig_song.get("duration_ms", 0),
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

    await db_manager.add_song_to_db(
song_info=db_song_info,
        file_path=save_path,
        quality="flac",
        lyric=orig_song.get("lyric") or orig_song.get("lyrics"),
        tlyric=orig_song.get("tlyric"),
        cover_data=cover_info.get("image_data") if cover_info else None,
        cover_mime=cover_info.get("mime_type") if cover_info else None,
)

    print(
        f"[MVSep后台] 🎉 全流程搞定！伴奏 '{db_song_info['title']}' 已秒速加入本地曲库！"
    )


async def _process_instrumental_pipeline(song_id: int):
    instrumental_task_status[song_id] = {
        "status": "processing",
        "message": "正在获取本地文件...",
    }
    file_path = await db_manager.get_song_path_by_id(song_id=song_id)
    if not file_path or not os.path.exists(file_path):
        instrumental_task_status[song_id] = {
            "status": "failed",
            "message": "本地文件不存在",
        }
        return

    instrumental_task_status[song_id] = {
        "status": "processing",
        "message": "正在上传至 MVSep AI 算力集群...",
    }
    result = await mvsep_api.create_separation(file_path)
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

    while True:
        await asyncio.sleep(6)
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

        instrumental_task_status[song_id] = {
            "status": "processing",
            "message": f"AI 处理中 [{mvsep_status}]...",
            "hash": task_hash,
        }

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


async def mvsep_queue_worker():
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


@router.post("/batch_submit")
async def submit_batch_instrumental_tasks(req: BatchInstrumentalSubmitRequest):
    added_count = 0
    for sid in req.song_ids:
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


@router.get("/queue_status")
async def get_instrumental_queue_status():
    return {"code": 200, "data": instrumental_task_status}
