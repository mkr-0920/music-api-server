# --- 标准库导入 ---
import asyncio
import base64
import datetime
import json
import logging
import os
import random
import re

# --- 第三方库导入 ---
import httpx
from fastapi.concurrency import run_in_threadpool
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TBPM,
    TCOM,
    TCON,
    TDOR,
    TDRC,
    TEXT,
    TIT2,
    TPE1,
    TPE2,
    TPE4,
    TPOS,
    TPUB,
    TRCK,
    TYER,
    USLT,
)

# mutagen (处理音乐元数据)
from mutagen.mp3 import MP3
from opencc import OpenCC

# --- 您自己的模块导入 ---
from core.config import Config


class QQMusicAPI:
    def __init__(
        self,
        local_api_instance,
        master_directory: str,
        flac_directory: str,
        lossy_directory: str,
    ):
        self.local_api = local_api_instance
        self.master_directory = master_directory
        self.flac_directory = flac_directory
        self.lossy_directory = lossy_directory
        self.converter = OpenCC("t2s")
        self.base_url = "https://u.y.qq.com/cgi-bin/musicu.fcg"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Referer": "https://y.qq.com/",
            "Origin": "https://y.qq.com/",
        }
        self.file_config = {
            "128": {"s": "M500", "e": ".mp3", "bitrate": "128kbps"},
            "320": {"s": "M800", "e": ".mp3", "bitrate": "320kbps"},
            "flac": {"s": "F000", "e": ".flac", "bitrate": "FLAC"},
            "master": {"s": "AI00", "e": ".flac", "bitrate": "Master"},
        }
        self.album_cache = {}
        self._setup_logger()

    @property
    def client(self):
        import httpx

        # 共享同一个主事件循环下的 Client
        if getattr(self, "_client", None) is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _setup_logger(self):
        logger = logging.getLogger("QQMusicDownloader")
        logger.setLevel(logging.ERROR)
        if not logger.handlers:
            handler = logging.FileHandler("download_errors_qq.log", encoding="utf-8")
            formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        self.logger = logger

    async def _get_request(self, url, params=None):
        try:
            response = await self.client.get(
                url, params=params, headers=self.headers, cookies=Config.QQ_USER_CONFIG
            )
            response.raise_for_status()
            text = response.text
            if text.startswith("callback("):
                return json.loads(text[9:-1])
            return response.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"QQ音乐GET请求出错: {e}")
            return None

    async def _post_request(self, json_data):
        try:
            # safe_json_data = json.loads(json.dumps(json_data, default=list))
            response = await self.client.post(
                self.base_url,
                json=json_data,
                headers=self.headers,
                cookies=Config.QQ_USER_CONFIG,
            )
            response.raise_for_status()
            return response.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"QQ音乐POST请求出错: {e}")
            return None

    async def _resolve_song_ids(
        self, song_mid: str = None, song_id: int = None
    ) -> dict:
        if not song_mid and not song_id:
            return None
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
        params = {"platform": "yqq", "format": "json", "outCharset": "utf-8"}
        if song_id:
            params["songid"] = song_id
        else:
            params["songmid"] = song_mid

        response = await self._get_request(url, params=params)
        if (
            response
            and response.get("code") == 0
            and "data" in response
            and len(response["data"]) > 0
        ):
            song_data = response["data"][0]
            return {"id": song_data.get("id"), "mid": song_data.get("mid")}
        print(f"ID解析失败: mid={song_mid}, id={song_id}。响应: {response}")
        return None

    async def _fetch_single_url(self, song_mid, quality, guid, uin):
        if quality not in self.file_config:
            return quality, None
        config = self.file_config[quality]
        filename = f"{config['s']}{song_mid}{song_mid}{config['e']}"
        payload = {
            "req_1": {
                "module": "vkey.GetVkeyServer",
                "method": "CgiGetVkey",
                "param": {
                    "filename": [filename],
                    "guid": guid,
                    "songmid": [song_mid],
                    "songtype": [0],
                    "uin": uin,
                    "loginflag": 1,
                    "platform": "20",
                },
            },
            "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0},
        }

        vkey_data = await self._post_request(payload)
        if vkey_data and vkey_data.get("req_1", {}).get("data", {}).get(
            "midurlinfo", [{}]
        )[0].get("purl"):
            purl = vkey_data["req_1"]["data"]["midurlinfo"][0]["purl"]
            domain = next(
                (
                    d
                    for d in vkey_data["req_1"]["data"].get("sip", [])
                    if "pv.music" in d
                ),
                "https://isure.stream.qqmusic.qq.com/",
            )
            return quality, domain + purl
        return quality, None

    async def _get_album_details(self, album_identifier: str) -> dict:
        if not album_identifier:
            return None

        album_identifier = str(album_identifier).strip()
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg"
        params = {"format": "json", "outCharset": "utf-8"}

        if album_identifier.isdigit():
            params["albumid"] = album_identifier
        else:
            params["albummid"] = album_identifier

        response = await self._get_request(url, params=params)
        if response and response.get("code", -1) == 0:
            return response.get("data")
        print(
            f"错误: 获取专辑 (MID/ID: {album_identifier}) 详情失败。服务器响应: {response}"
        )
        return None

    async def _embed_metadata(
        self, file_path: str, song_info: dict, lyric: str, tlyric: str
    ):
        """异步下载封面，预热专辑缓存，并在线程池中执行同步的文件写入。"""
        image_data, cover_mime = None, None
        album_id = song_info.get("album_mid")

        # 异步预热专辑详情缓存
        if album_id and album_id not in self.album_cache:
            self.album_cache[album_id] = await self._get_album_details(album_id)

        # 异步下载封面
        if song_info.get("cover_url"):
            try:
                image_response = await self.client.get(
                    song_info["cover_url"], timeout=30
                )
                if image_response.status_code == 200:
                    image_data = image_response.content
                    cover_mime = image_response.headers.get(
                        "Content-Type", "image/jpeg"
                    )
            except httpx.RequestError as e:
                print(f"下载封面时出错: {e}")

        # 在线程池中执行所有同步的文件I/O（元数据写入）
        await run_in_threadpool(
            self._write_tags_to_file, file_path, song_info, lyric, tlyric, image_data
        )

        # 返回封面数据，以便存入数据库
        return image_data, cover_mime

    def _write_tags_to_file(self, file_path, song_info, lyric, tlyric, image_data):
        """同步的元数据写入辅助函数。"""
        try:
            print(f"后台任务: 开始为 {os.path.basename(file_path)} 嵌入元数据...")
            song_name = song_info.get("name")
            artist_names = song_info.get("artist")
            album_name = song_info.get("album_name")
            album_id = song_info.get("album_mid")
            track_number = song_info.get("track_number")
            disc_number = song_info.get("disc_number")
            bpm = song_info.get("bpm")
            lyricist = song_info.get("lyricist")
            composer = song_info.get("composer")
            arranger = song_info.get("arranger")

            # 此函数在线程池中运行，album_cache的访问是线程安全的
            album_details = self.album_cache.get(album_id)

            genre = song_info.get("genre")
            if album_details:
                album_artist = album_details.get("singername", artist_names)
                publisher = album_details.get("company")
                if not genre:
                    genre = album_details.get("genre")
                publish_time_str = album_details.get("aDate")
            else:
                album_artist, publisher, publish_time_str = artist_names, None, None

            release_date_str, release_year_str = None, None
            if publish_time_str:
                try:
                    dt_object = datetime.datetime.strptime(publish_time_str, "%Y-%m-%d")
                    release_date_str = dt_object.strftime("%Y-%m-%d")
                    release_year_str = dt_object.strftime("%Y")
                except ValueError:
                    pass

            full_lyric = (
                f"{lyric}\n\n--- 翻译\n\n{tlyric}" if tlyric and lyric else lyric
            )

            if file_path.lower().endswith(".mp3"):
                audio = MP3(file_path, ID3=ID3)
                if audio.tags is None:
                    audio.add_tags()

                audio.tags.add(TIT2(encoding=3, text=song_name))
                audio.tags.add(TPE1(encoding=3, text=artist_names))
                audio.tags.add(TALB(encoding=3, text=album_name))
                if album_artist:
                    audio.tags.add(TPE2(encoding=3, text=album_artist))
                if track_number:
                    audio.tags.add(TRCK(encoding=3, text=str(track_number)))
                if disc_number:
                    audio.tags.add(TPOS(encoding=3, text=str(disc_number)))
                if genre:
                    audio.tags.add(TCON(encoding=3, text=genre))
                if publisher:
                    audio.tags.add(TPUB(encoding=3, text=publisher))
                if bpm and bpm > 0:
                    audio.tags.add(TBPM(encoding=3, text=str(round(bpm))))
                if composer:
                    audio.tags.add(TCOM(encoding=3, text=composer))
                if lyricist:
                    audio.tags.add(TEXT(encoding=3, text=lyricist))
                if arranger:
                    audio.tags.add(TPE4(encoding=3, text=arranger))
                if release_date_str:
                    audio.tags.add(TDRC(encoding=3, text=release_date_str))
                    audio.tags.add(TDOR(encoding=3, text=release_date_str))
                if release_year_str:
                    audio.tags.add(TYER(encoding=3, text=release_year_str))
                if image_data:
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=image_data,
                        )
                    )
                if full_lyric:
                    audio.tags.add(USLT(encoding=3, text=full_lyric))
                audio.save()

            elif file_path.lower().endswith(".flac"):
                audio = FLAC(file_path)
                audio["title"] = song_name
                audio["artist"] = artist_names
                audio["album"] = album_name
                if album_artist:
                    audio["albumartist"] = album_artist
                if track_number:
                    audio["tracknumber"] = str(track_number)
                if disc_number:
                    audio["discnumber"] = str(disc_number)
                if genre:
                    audio["genre"] = genre
                if publisher:
                    audio["organization"] = publisher
                if bpm and bpm > 0:
                    audio["bpm"] = str(round(bpm))
                if composer:
                    audio["composer"] = composer
                if lyricist:
                    audio["lyricist"] = lyricist
                if arranger:
                    audio["arranger"] = arranger
                if release_date_str:
                    audio["date"] = release_date_str
                if release_year_str:
                    audio["year"] = release_year_str
                if full_lyric:
                    audio["lyrics"] = full_lyric

                audio.clear_pictures()
                if image_data:
                    picture = Picture()
                    picture.type = 3
                    picture.mime = "image/jpeg"
                    picture.desc = "Cover"
                    picture.data = image_data
                    audio.add_picture(picture)
                audio.save()

            print(f"后台任务: 元数据嵌入成功 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生严重错误 - {e}")

    async def _download_and_process_single_version(
        self, search_key, quality, download_url, extension, song_info, lyric, tlyric
    ):
        """异步下载，并将所有元数据写入数据库。"""
        album_name = song_info.get("album_name", "")
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name)
        base_name = f"{search_key} {safe_album_name}" if safe_album_name else search_key
        filename_suffix = " [M]" if quality == "master" else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)

        if quality == "master":
            save_directory = self.master_directory
        elif quality == "flac":
            save_directory = self.flac_directory
        else:
            save_directory = self.lossy_directory

        os.makedirs(save_directory, exist_ok=True)

        file_path = os.path.join(
            save_directory, f"{safe_filename}{filename_suffix}{extension}"
        )

        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(
                    f"后台任务: 开始下载 '{base_name}' ({quality}) (第 {attempt + 1} 次尝试)"
                )
                async with self.client.stream(
                    "GET",
                    download_url,
                    timeout=300,
                    headers=self.headers,
                    cookies=Config.QQ_USER_CONFIG,
                ) as r:
                    r.raise_for_status()
                    with open(file_path, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=8192):
                            f.write(chunk)

                print(f"后台任务: 下载成功 - {file_path}")

                # 嵌入元数据到文件，并获取封面数据
                image_data, cover_mime = await self._embed_metadata(
                    file_path, song_info, lyric, tlyric
                )
                # print(f"song_info: {song_info}")

                # 从预热好的缓存中取回专辑附加信息，用于补全数据库
                album_id = song_info.get("album_mid")
                album_details = self.album_cache.get(album_id, {})

                # 解析发行日期
                publish_time_str = album_details.get("aDate")
                release_date_str, release_year_str = None, None
                if publish_time_str:
                    try:
                        dt_object = datetime.datetime.strptime(
                            publish_time_str, "%Y-%m-%d"
                        )
                        release_date_str = dt_object.strftime("%Y-%m-%d")
                        release_year_str = dt_object.strftime("%Y")
                    except ValueError:
                        pass

                # 组装标准数据库字典
                db_song_info = {
                    "search_key": search_key,
                    "title": song_info.get("name", "未知歌曲"),
                    "is_instrumental": 0,
                    "duration_ms": song_info.get("duration", 0),
                    "album": song_info.get("album_name"),
                    "artist": song_info.get("artist"),
                    "albumartist": album_details.get(
                        "singername", song_info.get("artist")
                    ),
                    "composer": song_info.get("composer"),
                    "lyricist": song_info.get("lyricist"),
                    "arranger": song_info.get("arranger"),
                    "bpm": song_info.get("bpm"),
                    "genre": song_info.get("genre") or album_details.get("genre"),
                    "tracknumber": song_info.get("track_number"),
                    "discnumber": song_info.get("disc_number"),
                    "date": release_date_str,
                    "year": release_year_str,
                }

                print(f"db_song_info: {db_song_info}")

                # 入库
                await run_in_threadpool(
                    self.local_api.add_song_to_db,
                    song_info=db_song_info,  # 传入刚组装好的标准字典
                    file_path=file_path,
                    quality=quality,
                    lyric=lyric,
                    tlyric=tlyric,
                    cover_data=image_data,
                    cover_mime=cover_mime,
                )
                return True
            except httpx.RequestError as e:
                print(
                    f"后台任务: 下载 '{search_key}' 失败 (第 {attempt + 1} 次尝试)，错误: {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    self.logger.error(
                        f"歌曲下载失败 - MID: {song_info.get('mid')}, 名称: '{search_key}', 音质: {quality}, 错误: {e}"
                    )
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    return False
        return False

    async def _background_download_task(
        self, song_info: dict, song_urls: dict, lyric: str, tlyric: str
    ):
        """完全解耦的异步后台智能分层下载任务（严格模式 + 真正的兜底）。"""
        album_name = song_info.get("album_name", "")
        artist_string = song_info.get("artist", "未知歌手")
        song_name = song_info.get("name", "未知歌曲")
        search_key = self.converter.convert(f"{artist_string} - {song_name}")

        existing_qualities = await run_in_threadpool(
            self.local_api.get_existing_qualities,
            search_key=search_key,
            album=album_name,
        )
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        tasks = []
        enable_master = getattr(Config, "ENABLE_MASTER_DOWNLOAD", True)
        enable_flac = getattr(Config, "ENABLE_FLAC_DOWNLOAD", False)
        enable_lossy = getattr(Config, "ENABLE_LOSSY_DOWNLOAD", False)

        # 标记是否已经拥有或本次能下到无损
        downloaded_or_has_lossless = ("master" in existing_qualities) or (
            "flac" in existing_qualities
        )

        # 1. 独立判断 Master
        if enable_master:
            if "master" not in existing_qualities and "master" in song_urls:
                tasks.append(
                    self._download_and_process_single_version(
                        search_key,
                        "master",
                        song_urls["master"],
                        ".flac",
                        song_info,
                        lyric,
                        tlyric,
                    )
                )
                downloaded_or_has_lossless = True

        # 2. 独立判断 FLAC
        if enable_flac:
            if "flac" not in existing_qualities and "flac" in song_urls:
                tasks.append(
                    self._download_and_process_single_version(
                        search_key,
                        "flac",
                        song_urls["flac"],
                        ".flac",
                        song_info,
                        lyric,
                        tlyric,
                    )
                )
                downloaded_or_has_lossless = True

        # 3. 真正的有损兜底 (仅在本地没有无损，且本次也下不到无损时触发)
        if enable_lossy and not downloaded_or_has_lossless:
            if "320" not in existing_qualities and "128" not in existing_qualities:
                if "320" in song_urls:
                    tasks.append(
                        self._download_and_process_single_version(
                            search_key,
                            "320",
                            song_urls["320"],
                            self.file_config["320"]["e"],
                            song_info,
                            lyric,
                            tlyric,
                        )
                    )
                elif "128" in song_urls:
                    tasks.append(
                        self._download_and_process_single_version(
                            search_key,
                            "128",
                            song_urls["128"],
                            self.file_config["128"]["e"],
                            song_info,
                            lyric,
                            tlyric,
                        )
                    )

        if tasks:
            await asyncio.gather(*tasks)
        else:
            print(f"后台任务: '{search_key}' 命中严格模式，没有需要下载的音质版本。")

    async def get_song_info(self, song_mid):
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"},
            "req_1": {
                "module": "music.pf_song_detail_svr",
                "method": "get_song_detail",
                "param": {"song_mid": song_mid},
            },
        }
        song_data = await self._post_request(payload)
        if not song_data or song_data.get("code") != 0:
            return None
        data = song_data.get("req_1", {}).get("data", {})
        track_info = data.get("track_info", {})
        info_list = data.get("info", [])
        info_dict = {}
        for item in info_list:
            title = item.get("title")
            content = item.get("content")
            if title and content and len(content) > 0:
                info_dict[title] = ";".join([c.get("value", "") for c in content])
        index_cd = track_info.get("index_cd")
        disc_number = index_cd + 1 if isinstance(index_cd, int) else None
        return {
            "id": track_info.get("id"),
            "mid": track_info.get("mid"),
            "name": track_info.get("name"),
            "artist": "、".join(
                [singer["name"] for singer in track_info.get("singer", [])]
            ),
            "album_name": track_info.get("album", {}).get("name"),
            "album_mid": track_info.get("album", {}).get("mid"),
            "duration": track_info.get("interval", 0) * 1000,
            "cover_url": f"https://y.qq.com/music/photo_new/T002R800x800M000{track_info.get('album', {}).get('mid')}.jpg",
            "track_number": track_info.get("index_album"),
            "disc_number": disc_number,
            "bpm": track_info.get("bpm"),
            "lyricist": info_dict.get("作词"),
            "composer": info_dict.get("作曲"),
            "arranger": info_dict.get("编曲"),
            "genre": info_dict.get("歌曲流派"),
        }

    async def get_song_urls(self, song_mid):
        uin = Config.QQ_USER_CONFIG.get("uin", "0")
        guid = str(random.randint(1000000000, 9999999999))
        tasks = [
            self._fetch_single_url(song_mid, quality, guid, uin)
            for quality in self.file_config.keys()
        ]
        results = await asyncio.gather(*tasks)
        return {quality: url for quality, url in results if url}

    async def get_lyrics(self, song_id):
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"},
            "req_1": {
                "module": "music.musichallSong.PlayLyricInfo",
                "method": "GetPlayLyricInfo",
                "param": {"songID": song_id, "trans": 1, "roma": 1},
            },
        }
        lyric_data = await self._post_request(payload)
        if not lyric_data or lyric_data.get("code") != 0:
            return "", ""
        lyric_info = lyric_data.get("req_1", {}).get("data", {})
        lyric = base64.b64decode(lyric_info.get("lyric", b"")).decode("utf-8", "ignore")
        tlyric = base64.b64decode(lyric_info.get("trans", b"")).decode(
            "utf-8", "ignore"
        )
        return lyric, tlyric

    async def get_song_details(self, song_mid: str = None, song_id: int = None):
        resolved_ids = await self._resolve_song_ids(song_mid=song_mid, song_id=song_id)
        if not resolved_ids or not resolved_ids.get("mid"):
            return {"error": "无法解析到有效的歌曲信息。"}
        final_mid, final_id = resolved_ids.get("mid"), resolved_ids.get("id")

        info_task = self.get_song_info(final_mid)
        urls_task = self.get_song_urls(final_mid)
        lyric_task = (
            self.get_lyrics(final_id) if final_id else asyncio.sleep(0, result=("", ""))
        )

        info, urls, (lyric, tlyric) = await asyncio.gather(
            info_task, urls_task, lyric_task
        )
        if not info:
            return {"error": "获取详细信息失败。"}

        if self.local_api and Config.DOWNLOADS_ENABLED:
            asyncio.create_task(
                self._background_download_task(info, urls, lyric, tlyric)
            )

        return {**info, "urls": urls, "lyric": lyric, "tlyric": tlyric}

    async def search_and_get_details(self, keyword: str, album: str = None):
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"},
            "req_1": {
                "method": "DoSearchForQQMusicDesktop",
                "module": "music.search.SearchCgiService",
                "param": {
                    "num_per_page": 5,
                    "page_num": 1,
                    "query": keyword,
                    "search_type": 0,
                },
            },
        }
        search_results = await self._post_request(payload)
        if not search_results or search_results.get("code") != 0:
            return {"error": "搜索失败或无结果"}
        song_list = (
            search_results.get("req_1", {})
            .get("data", {})
            .get("body", {})
            .get("song", {})
            .get("list", [])
        )

        try:
            target_artist, target_song = [
                x.strip().lower() for x in keyword.split(" - ", 1)
            ]
        except ValueError:
            return {"error": "关键词格式不正确..."}

        def find_exact_match(songs):
            for song in songs:
                result_song = song.get("name", "").lower()
                result_artist = " / ".join(
                    [s.get("name") for s in song.get("singer", [])]
                ).lower()
                if target_song in result_song and target_artist in result_artist:
                    return song
            return None

        best_match_song = find_exact_match(song_list)
        if not best_match_song:
            return {"error": "未能找到精确匹配的歌曲"}
        return await self.get_song_details(
            song_mid=best_match_song.get("mid"), song_id=best_match_song.get("id")
        )

    async def get_playlist_info(self, playlist_id: str) -> dict:
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_playlist_cp.fcg"
        params = {
            "id": playlist_id,
            "tpl": "wk",
            "format": "json",
            "outCharset": "utf-8",
        }
        response = await self._get_request(url, params=params)
        if not response or response.get("code", -1) != 0:
            return {"error": "获取QQ音乐歌单详情失败。"}
        cdlist = response.get("data", {}).get("cdlist", [])
        if not cdlist:
            return {"error": "未在响应中找到歌单数据。"}
        playlist_data = cdlist[0]
        playlist_name = playlist_data.get("dissname", "未知歌单")
        song_list = playlist_data.get("songlist", [])
        songs = [
            {
                "name": s.get("songname"),
                "artist": "、".join([i.get("name") for i in s.get("singer", [])]),
                "mid": s.get("songmid"),
            }
            for s in song_list
        ]
        return {"playlist_name": playlist_name, "songs": songs}

    async def download_playlist_by_id(self, playlist_id: str, level: str):
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_playlist_cp.fcg"
        params = {
            "id": playlist_id,
            "tpl": "wk",
            "format": "json",
            "outCharset": "utf-8",
            "new_format": 1,
            "platform": "mac",
        }
        response = await self._get_request(url, params=params)
        if not response or response.get("code", -1) != 0:
            print("错误: 获取歌单详情失败。")
            return
        cdlist = response.get("data", {}).get("cdlist", [])
        if not cdlist:
            print("歌单中没有找到任何歌曲。")
            return
        playlist_data = cdlist[0]
        playlist_name = playlist_data.get("dissname", "未知歌单")
        song_ids_str = playlist_data.get("songids")
        if not song_ids_str:
            print("歌单中没有找到任何歌曲。")
            return
        song_id_list = song_ids_str.split(",")
        total_songs = len(song_id_list)
        print(f"开始处理歌单 '{playlist_name}'，共 {total_songs} 首歌曲。")
        for i, song_id_str in enumerate(song_id_list):
            try:
                song_id = int(song_id_str)
                print(
                    f"  -> 正在将第 {i + 1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列..."
                )
                await self.get_song_details(song_id=song_id)
                await asyncio.sleep(1)
            except (ValueError, TypeError):
                continue
        print(f"歌单 '{playlist_name}' 处理完毕。")

    async def download_album(self, album_identifier: str, level: str):
        if not album_identifier:
            return

        album_mid = str(album_identifier).strip()
        url = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg"
        params = {"format": "json", "outCharset": "utf-8"}

        # 智能参数嗅探
        if album_mid.isdigit():
            params["albumid"] = album_mid
        else:
            params["albummid"] = album_mid

        response = await self._get_request(url, params=params)
        # print(f"专辑信息：{response}")
        if not response or response.get("code", -1) != 0:
            print("错误: 获取专辑详情失败。")
            return

        song_list = response.get("data", {}).get("list", [])
        album_name = response.get("data", {}).get("name", "未知专辑")
        total_songs = len(song_list)

        if total_songs == 0:
            print("专辑中没有找到任何歌曲。")
            return

        print(f"开始处理专辑 '{album_name}'，共 {total_songs} 首歌曲。")
        for i, song in enumerate(song_list):
            await self.get_song_details(
                song_mid=song.get("songmid"), song_id=song.get("songid")
            )
            await asyncio.sleep(1)
        print(f"专辑 '{album_name}' 处理完毕。")

    async def start_background_playlist_download(self, playlist_id: str, level: str):
        """启动后台原生协程来执行异步歌单下载任务。"""
        asyncio.create_task(self.download_playlist_by_id(playlist_id, level))
        print(f"已为歌单 {playlist_id} 启动后台下载任务。")

    async def start_background_album_download(self, album_identifier: str, level: str):
        """启动后台原生协程来执行异步专辑下载任务。支持纯数字 ID 或字符串 MID。"""
        asyncio.create_task(self.download_album(album_identifier, level))
        print(f"已为专辑 {album_identifier} 启动后台下载任务。")
