# --- 标准库导入 ---
import asyncio
from fastapi.concurrency import run_in_threadpool
import base64
import datetime
import json
import os
import re
import urllib.parse
from hashlib import md5
from random import randrange
from typing import List

# --- 第三方库导入 ---
import httpx

# cryptography (处理网易云加密)
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
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
    TXXX,
    TYER,
    USLT,
)

# mutagen (处理音乐元数据)
from mutagen.mp3 import MP3
from opencc import OpenCC

from core.config import Config

# --- 您自己的模块导入 ---
from utils.helpers import Utils


class APIConstants:
    """API相关常量"""

    AES_KEY = b"e82ckenh8dichen8"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/3.1.21.204647"
    DEFAULT_CONFIG = {
        "os": "pc",
        "appver": "3.1.21.204647",
        "osver": "",
        "deviceId": "pyncm!",
    }
    SONG_URL_V1 = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
    SONG_DETAIL_V3 = "https://interface3.music.163.com/api/v3/song/detail"
    LYRIC_API = "https://interface3.music.163.com/api/song/lyric"
    SEARCH_API = "https://interface.music.163.com/eapi/cloudsearch/pc"
    PLAYLIST_DETAIL_API = "https://music.163.com/api/v6/playlist/detail"
    ALBUM_DETAIL_API = "https://music.163.com/api/v1/album/"
    ALBUM_V3_DETAIL = "https://music.163.com/eapi/album/v3/detail"
    CACHE_KEY_AES_KEY = b")(13daqP@ssw0rd~"
    SONG_WIKI_API = (
        "https://interface3.music.163.com/api/link/page/parent/relation/construct/info"
    )
    DAILY_RECOMMEND_API = "https://interface3.music.163.com/eapi/batch"
    PLAYLIST_MANIPULATE_API = (
        "https://interface.music.163.com/eapi/playlist/manipulate/tracks/"
    )
    STYLE_TAGS_API = (
        "https://interface3.music.163.com/eapi/homepage/daily/song/config/get"
    )
    STYLE_TAGS_SAVE_API = (
        "https://interface3.music.163.com/eapi/homepage/daily/song/tag/save"
    )
    STYLE_PLAYLIST_GET_API = (
        "https://interface3.music.163.com/eapi/homepage/category/daily/song/list"
    )
    RADIO_API = "https://interface3.music.163.com/eapi/v1/radio/get"
    FM_MODES_API = "https://interface3.music.163.com/eapi/link/position/show/resource"


class NeteaseMusicAPI:
    def __init__(
        self,
        config_users: dict,
        db_manager_instance,
        master_directory: str,
        flac_directory: str,
        lossy_directory: str,
    ):
        """
        初始化函数变动：第一个参数改为接收用户配置字典
        """
        self.db_manager = db_manager_instance
        self.master_directory = master_directory
        self.flac_directory = flac_directory
        self.lossy_directory = lossy_directory
        self.converter = OpenCC("t2s")
        self.headers = {"User-Agent": APIConstants.USER_AGENT}
        self.quality_map = {
            "standard": "128",
            "exhigh": "320",
            "lossless": "flac",
            "hires": "hires",
            "jyeffect": "jyeffect",
            "sky": "sky",
            "jymaster": "master",
        }
        self.album_cache = {}

        # 初始化多用户 Cookie 池
        self.cookies_pool = {}
        if config_users:
            for uid, cookie_str in config_users.items():
                self.cookies_pool[str(uid)] = Utils.parse_cookie_str(cookie_str)

        # 设置默认 Cookie (取字典中第一个，或者空)
        self.default_cookies = (
            list(self.cookies_pool.values())[0] if self.cookies_pool else {}
        )
        # 为了兼容旧代码，self.cookies 指向默认
        self.cookies = self.default_cookies

    @property
    def client(self):
        import httpx

        # 共享同一个主事件循环下的 Client
        if getattr(self, "_client", None) is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    def _eapi_encrypt(self, url_path: str, payload: dict) -> dict:
        """加密 EAPI 请求体。"""
        digest = md5(
            f"nobody{url_path}use{json.dumps(payload)}md5forencrypt".encode("utf-8")
        ).hexdigest()
        params_str = (
            f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
        )
        padder = padding.PKCS7(algorithms.AES(APIConstants.AES_KEY).block_size).padder()
        padded_data = padder.update(params_str.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(APIConstants.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return {"params": encrypted_data.hex().upper()}

    def _eapi_decrypt(self, encrypted_bytes: bytes) -> str:
        """解密 EAPI 响应。"""
        AES_KEY = b"e82ckenh8dichen8"
        try:
            cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
            decryptor = cipher.decryptor()
            unpadder = padding.PKCS7(algorithms.AES(AES_KEY).block_size).unpadder()
            decrypted_padded_data = (
                decryptor.update(encrypted_bytes) + decryptor.finalize()
            )
            unpadded_data = unpadder.update(decrypted_padded_data) + unpadder.finalize()
            return unpadded_data.decode("utf-8")
        except Exception as e:
            raise ValueError(f"EAPI 响应解密失败: {e}")

    def _generate_cache_key(self, params: dict) -> str:
        """生成 album/v3/detail 接口所需的 cache_key。"""
        sorted_keys = sorted(params.keys(), key=lambda k: ord(k[0]))
        query_string = "&".join([f"{k}={params[k]}" for k in sorted_keys])
        cipher = Cipher(algorithms.AES(APIConstants.CACHE_KEY_AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(
            algorithms.AES(APIConstants.CACHE_KEY_AES_KEY).block_size
        ).padder()
        padded_data = padder.update(query_string.encode("utf-8")) + padder.finalize()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return base64.b64encode(encrypted_data).decode("utf-8")

    async def _post_request(
        self,
        url: str,
        data: dict,
        is_eapi=False,
        eapi_path: str = None,
        os_type: str = None,
        user_id: str = None,
    ):
        """
        支持 eapi 路径覆盖、OS伪装和多用户切换。
        """
        print(f"url: {url}")
        try:
            target_cookies = self.default_cookies.copy()  # 默认

            if user_id and str(user_id) in self.cookies_pool:
                target_cookies = self.cookies_pool[str(user_id)].copy()

            if os_type:
                target_cookies["os"] = os_type

            if is_eapi:
                # 👇👇👇 核心暴力修改：有就覆写，没有就追加 👇👇👇
                data["e_r"] = "false"
                # 👆👆👆 -------------------------------------- 👆👆👆

                url_path = (
                    eapi_path
                    if eapi_path
                    else urllib.parse.urlparse(url).path.replace("/eapi/", "/api/")
                )
                data = self._eapi_encrypt(url_path, data)

            response = await self.client.post(
                url, headers=self.headers, cookies=target_cookies, data=data
            )
            response.raise_for_status()

            if not response.content:
                return None

            content_bytes = response.content

            # 如果以 '{' 或 '[' 开头，100% 是明文 JSON，直接秒解！(O(1) 的判断开销)
            if content_bytes.lstrip().startswith((b"{", b"[")):
                return json.loads(content_bytes)

            # 如果走到这里，且是 EAPI，说明遇到了服务端强制加密的硬茬，老老实实解密
            if is_eapi:
                decrypted_text = self._eapi_decrypt(content_bytes)
                return json.loads(decrypted_text)

            # 常规非 EAPI 兜底
            return response.json()

        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"网易云请求或处理出错: {e}")
            return None

    async def _post_song_wiki_request(self, song_id: str):
        """专门用于请求歌曲百科接口的函数。"""
        ext_json = json.dumps(
            {"states": {"playingResource": {"current": str(song_id)}}}
        )
        url_query_params = {"extJson": ext_json, "positionCode": "songWikiMainPosition"}
        eapi_payload_to_encrypt = {
            "extJson": ext_json,
            "positionCode": "songWikiMainPosition",
            "header": "{}",
            "e_r": True,
        }
        url_path = urllib.parse.urlparse(APIConstants.SONG_WIKI_API).path
        encrypted_post_body = self._eapi_encrypt(url_path, eapi_payload_to_encrypt)
        try:
            response = await self.client.post(
                APIConstants.SONG_WIKI_API,
                params=url_query_params,
                data=encrypted_post_body,
                headers=self.headers,
                cookies=self.cookies,
            )
            response.raise_for_status()
            return json.loads(response.text)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            print(f"网易云百科接口请求或处理出错: {e}")
            return None

    async def _get_song_url_data(self, song_id: str, level: str, meta_info: dict):
        """获取歌曲指定音质的播放链接。"""
        config = APIConstants.DEFAULT_CONFIG.copy()
        config["requestId"] = str(randrange(20000000, 30000000))
        payload = {"ids": [str(song_id)], "level": level, "header": json.dumps(config)}
        if level == "hires":
            payload["encodeType"] = "hires"
        else:
            payload["encodeType"] = "flac"
        if level == "sky":
            payload["immerseType"] = "c51"
        if level == "jyeffect":
            try:
                charge_info_list = meta_info.get("privilege", {}).get(
                    "chargeInfoList", []
                )
                jyeffect_info = next(
                    (item for item in charge_info_list if item.get("chargeType") == 10),
                    None,
                )
                if jyeffect_info and jyeffect_info.get("bizId"):
                    payload["soundEffect"] = {
                        "type": "jyeffect",
                        "bizId": jyeffect_info["bizId"],
                    }
            except Exception:
                pass
        return await self._post_request(APIConstants.SONG_URL_V1, payload, is_eapi=True)

    async def _get_song_metadata(self, song_id: str):
        """获取歌曲的基础元数据。"""
        data = {"c": json.dumps([{"id": song_id, "v": 0}])}
        return await self._post_request(APIConstants.SONG_DETAIL_V3, data)

    async def _get_song_wiki_details(self, song_id: str) -> dict:
        """调用歌曲百科接口，获取详细的创作者和属性信息。"""
        response_data = await self._post_song_wiki_request(song_id)
        if not response_data or response_data.get("code") != 200:
            return {}
        details = {}
        try:
            blocks = response_data.get("data", {}).get("blocks", [])
            wiki_block = next(
                (b for b in blocks if b.get("bizCode") == "songDetailNewSongWiki"), None
            )
            if not wiki_block:
                return {}
            nested_blocks = wiki_block.get("rnData", {}).get("blocks", [])
            info_block = next(
                (
                    b
                    for b in nested_blocks
                    if b.get("blockCode") == "wikiSubBlockSongInfoVo"
                ),
                None,
            )
            if info_block:
                creators = {}
                elements = info_block.get("blockInfo", {}).get("wikiSubElementVos", [])
                for element in elements:
                    title = element.get("title", "").strip()
                    names = [
                        meta.get("text")
                        for meta in element.get("wikiSubMetaVos", [])
                        if meta.get("text")
                    ]
                    if not title or not names:
                        continue
                    if "作词" in title:
                        creators.setdefault("lyricist", []).extend(names)
                    elif "作曲" in title:
                        creators.setdefault("composer", []).extend(names)
                    elif "制作人" in title:
                        creators.setdefault("producer", []).extend(names)
                    elif "编曲" in title:
                        creators.setdefault("arranger", []).extend(names)
                    elif "混音" in title:
                        creators.setdefault("mix", []).extend(names)
                    elif "母带" in title:
                        creators.setdefault("mastering", []).extend(names)
                for key, value in creators.items():
                    details[key] = ";".join(value)
            base_info_block = next(
                (
                    b
                    for b in nested_blocks
                    if b.get("blockCode") == "wikiSubBlockBaseInfoVo"
                ),
                None,
            )
            if base_info_block:
                elements = base_info_block.get("blockInfo", {}).get(
                    "wikiSubElementVos", []
                )
                for element in elements:
                    title = element.get("title", "").strip()
                    if title == "曲风" and element.get("wikiSubMetaVos"):
                        details["genre_from_wiki"] = element["wikiSubMetaVos"][0].get(
                            "text"
                        )
                    if title == "BPM" and element.get("content"):
                        details["bpm"] = element.get("content")
        except Exception as e:
            print(f"解析网易云歌曲百科信息时出错: {e}")
        return details

    async def _get_lyric_data(self, song_id: str):
        """获取歌曲歌词。"""
        data = {
            "id": song_id,
            "cp": "false",
            "tv": "0",
            "lv": "0",
            "rv": "0",
            "kv": "0",
        }
        return await self._post_request(APIConstants.LYRIC_API, data)

    async def _get_album_details_by_id(self, album_id: str) -> dict:
        """获取专辑详情，用于补充元数据。"""
        if not album_id:
            return None
        try:
            params_for_cache_key = {"id": str(album_id), "e_r": "false"}
            cache_key = self._generate_cache_key(params_for_cache_key)
            print(f"cache_key: {cache_key}")

            eapi_payload = {
                "id": str(album_id),
                "e_r": "false",
                "cache_key": cache_key,
                "header": json.dumps(APIConstants.DEFAULT_CONFIG),
            }

            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"

            response_json = await self._post_request(
                final_url, eapi_payload, is_eapi=True
            )

            if response_json and response_json.get("code") == 200:
                print(f">>> 从 eapi 专辑接口 (ID: {album_id}) 获取到详情。")
                return response_json.get("album")
            else:
                print(
                    f">>> 警告: 查询 eapi 专辑接口 (ID: {album_id}) 失败: {response_json}"
                )
        except Exception as e:
            print(f">>> 警告: 调用 eapi 专辑接口 (ID: {album_id}) 时发生异常: {e}")
        return None

    def _write_tags_to_file(self, file_path, song_info, lyric, tlyric, image_data):
        """
        同步的元数据写入辅助函数。
        此函数包含所有阻塞的磁盘I/O操作，应在线程池中运行。
        """
        try:
            print(f"后台任务: 开始为 {os.path.basename(file_path)} 嵌入元数据...")

            # --- 提取所有元数据 (逻辑同您提供的代码) ---
            album_info = song_info.get("al", {})
            artists = song_info.get("ar", [])
            song_name = song_info.get("name")
            aliases = song_info.get("alia", [])
            if aliases:
                song_name = f"{song_name} ({' / '.join(aliases)})"
            album_name = album_info.get("name")
            album_id = str(album_info.get("id"))
            artist_names = ";".join([artist["name"] for artist in artists])
            track_number = song_info.get("no")
            total_tracks = song_info.get("size")
            disc_number = song_info.get("cd")

            lyricist = song_info.get("lyricist")
            composer = song_info.get("composer")
            producer = song_info.get("producer")
            arranger = song_info.get("arranger")
            mix_engineer = song_info.get("mix")
            mastering_engineer = song_info.get("mastering")
            bpm = song_info.get("bpm")
            genre_from_wiki = song_info.get("genre_from_wiki")

            # --- 获取权威元数据 (注意: 这里的 self.album_cache 访问是线程安全的) ---
            album_details = self.album_cache.get(album_id)
            if album_details:
                album_artist = (
                    ";".join(
                        [artist["name"] for artist in album_details.get("artists", [])]
                    )
                    or artist_names
                )
                publisher = album_details.get("company")
                genre = genre_from_wiki or album_details.get("subType")
                publish_time_ms = album_details.get("publishTime", 0)
            else:
                album_artist = (
                    ";".join([ar["name"] for ar in album_info.get("ar", artists)])
                    or artist_names
                )
                publisher = album_info.get("company")
                genre = genre_from_wiki or album_info.get("subType")
                publish_time_ms = song_info.get("publishTime", 0)

            release_date_str, release_year_str = None, None
            if publish_time_ms and publish_time_ms > 0:
                dt_object = datetime.datetime.fromtimestamp(publish_time_ms / 1000)
                release_date_str = dt_object.strftime("%Y-%m-%d")
                release_year_str = dt_object.strftime("%Y")

            full_lyric = (
                f"{lyric}\n\n--- 翻译 ---\n\n{tlyric}" if tlyric and lyric else lyric
            )

            # --- 文件写入 ---
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
                    audio.tags.add(
                        TRCK(
                            encoding=3,
                            text=f"{track_number}/{total_tracks}"
                            if total_tracks
                            else str(track_number),
                        )
                    )
                if disc_number:
                    audio.tags.add(TPOS(encoding=3, text=str(disc_number)))
                if genre:
                    audio.tags.add(TCON(encoding=3, text=genre))
                if publisher:
                    audio.tags.add(TPUB(encoding=3, text=publisher))
                if lyricist:
                    audio.tags.add(TEXT(encoding=3, text=lyricist))
                if composer:
                    audio.tags.add(TCOM(encoding=3, text=composer))
                if producer:
                    audio.tags.add(TPE4(encoding=3, text=producer))
                if arranger:
                    audio.tags.add(TPE4(encoding=3, text=arranger))
                if bpm:
                    audio.tags.add(TBPM(encoding=3, text=str(bpm)))
                if mix_engineer:
                    audio.tags.add(TXXX(encoding=3, desc="MIXING", text=mix_engineer))
                if mastering_engineer:
                    audio.tags.add(
                        TXXX(encoding=3, desc="MASTERING", text=mastering_engineer)
                    )
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
                if total_tracks:
                    audio["tracktotal"] = str(total_tracks)
                if disc_number:
                    audio["discnumber"] = str(disc_number)
                if genre:
                    audio["genre"] = genre
                if publisher:
                    audio["organization"] = publisher
                if lyricist:
                    audio["lyricist"] = lyricist
                if composer:
                    audio["composer"] = composer
                if producer:
                    audio["producer"] = producer
                if arranger:
                    audio["arranger"] = arranger
                if mix_engineer:
                    audio["mixing engineer"] = mix_engineer
                if mastering_engineer:
                    audio["mastering engineer"] = mastering_engineer
                if bpm:
                    audio["bpm"] = str(bpm)
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

            print(f"后台任务: 已将最终元数据嵌入 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生严重错误 - {e}")

    async def _embed_metadata(
        self, file_path: str, song_info: dict, lyric: str, tlyric: str
    ):
        """异步下载封面，并在线程池中执行同步的文件写入。"""
        image_data, cover_mime = None, None
        album_info = song_info.get("al", {})

        # 异步获取专辑详情（用于缓存）
        album_id = str(album_info.get("id"))
        if album_id and album_id not in self.album_cache:
            self.album_cache[album_id] = await self._get_album_details_by_id(album_id)

        # 异步下载封面
        if album_info.get("picUrl"):
            try:
                image_response = await self.client.get(album_info["picUrl"], timeout=30)
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

    async def _download_and_process_single_version(
        self, search_key, quality, download_url, extension, song_info, lyric, tlyric
    ):
        """异步下载，并将同步的DB操作放入线程池。"""
        album_name = song_info.get("al", {}).get("name", "")

        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
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
                    cookies=self.cookies,
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

                # 将所有信息（包括封面）存入数据库
                # 构造一个 search_key，因为 song_info 可能不完整
                artist_names_simple = "、".join(
                    [artist["name"] for artist in song_info.get("ar", [])]
                )
                full_search_key = f"{artist_names_simple} - {song_info.get('name')}"

                # 将所有元数据打包到一个字典中（模拟 scanner.py 的逻辑）
                db_song_info = {
                    "search_key": search_key,
                    "title": song_info.get("name", "未知歌曲"),
                    "is_instrumental": 0,  # 在线下载的强制标记为原曲(0)
                    "duration_ms": song_info.get("dt"),
                    "album": song_info.get("al", {}).get("name"),
                    "artist": artist_names_simple,
                    "albumartist": song_info.get(
                        "album_artist"
                    ),  # 依赖 _embed_metadata 中填充的
                    "composer": song_info.get("composer"),
                    "lyricist": song_info.get("lyricist"),
                    "arranger": song_info.get("arranger"),
                    "producer": song_info.get("producer"),
                    "mix": song_info.get("mix"),
                    "mastering": song_info.get("mastering"),
                    "bpm": song_info.get("bpm"),
                    "genre": song_info.get("genre"),  # 依赖 _embed_metadata 中填充的
                    "tracknumber": song_info.get("no"),
                    "totaltracks": song_info.get("size"),
                    "discnumber": song_info.get("cd"),
                    "date": song_info.get(
                        "release_date_str"
                    ),  # 依赖 _embed_metadata 中填充的
                    "year": song_info.get(
                        "release_year_str"
                    ),  # 依赖 _embed_metadata 中填充的
                }

                await self.db_manager.add_song_to_db(
song_info=db_song_info,
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
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    return False
        return False

    async def _background_download_task(
        self, song_id: str, meta_info: dict, lyric: str, tlyric: str
    ):
        """完全解耦的异步后台智能分层下载任务"""
        artist_string = "、".join([artist["name"] for artist in meta_info["ar"]])
        song_name = meta_info["name"]
        search_key = self.converter.convert(f"{artist_string} - {song_name}")

        album_name = meta_info.get("al", {}).get("name", "") if meta_info else ""
        existing_qualities = await self.db_manager.get_existing_qualities(
search_key=search_key,
            album=album_name,
)
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        tasks = []
        enable_master = getattr(Config, "ENABLE_MASTER_DOWNLOAD", True)
        enable_flac = getattr(Config, "ENABLE_FLAC_DOWNLOAD", False)
        enable_lossy = getattr(Config, "ENABLE_LOSSY_DOWNLOAD", False)

        # 标记是否已经拥有或即将拥有无损音质
        has_lossless = ("master" in existing_qualities) or (
            "flac" in existing_qualities
        )
        will_download_lossless = False

        master_task = None
        flac_task = None

        # 1. 独立判断 Master (网易云标识: jymaster)
        if enable_master and "master" not in existing_qualities:
            url_data_m = await self._get_song_url_data(song_id, "jymaster", meta_info)
            if (
                url_data_m
                and url_data_m.get("data")
                and url_data_m["data"][0].get("level") == "jymaster"
            ):
                song_url_info = url_data_m["data"][0]
                if song_url_info.get("url"):
                    ext = f".{song_url_info.get('type', 'flac')}"
                    master_task = self._download_and_process_single_version(
                        search_key,
                        "master",
                        song_url_info["url"],
                        ext,
                        meta_info,
                        lyric,
                        tlyric,
                    )
                    will_download_lossless = True

        # 2. 独立判断 FLAC (网易云标识: lossless)
        if enable_flac and "flac" not in existing_qualities:
            url_data_f = await self._get_song_url_data(song_id, "lossless", meta_info)
            if (
                url_data_f
                and url_data_f.get("data")
                and url_data_f["data"][0].get("level") == "lossless"
            ):
                song_url_info = url_data_f["data"][0]
                if song_url_info.get("url"):
                    ext = f".{song_url_info.get('type', 'flac')}"
                    flac_task = self._download_and_process_single_version(
                        search_key,
                        "flac",
                        song_url_info["url"],
                        ext,
                        meta_info,
                        lyric,
                        tlyric,
                    )
                    will_download_lossless = True

        if master_task:
            tasks.append(master_task)
        if flac_task:
            tasks.append(flac_task)

        # 3. 真正的有损兜底
        if enable_lossy and not has_lossless and not will_download_lossless:
            if "320" not in existing_qualities and "128" not in existing_qualities:
                url_data_lossy = await self._get_song_url_data(
                    song_id, "exhigh", meta_info
                )
                if url_data_lossy and url_data_lossy.get("data"):
                    song_url_info = url_data_lossy["data"][0]
                    actual_level = song_url_info.get("level")
                    download_url = song_url_info.get("url")

                    if download_url and actual_level in ["exhigh", "standard"]:
                        db_quality = self.quality_map.get(actual_level, "128")
                        ext = f".{song_url_info.get('type', 'mp3')}"
                        tasks.append(
                            self._download_and_process_single_version(
                                search_key,
                                db_quality,
                                download_url,
                                ext,
                                meta_info,
                                lyric,
                                tlyric,
                            )
                        )

        if tasks:
            await asyncio.gather(*tasks)
        else:
            print(f"后台任务: '{search_key}' 命中严格模式，没有需要下载的音质版本。")

    def search_song(self, keyword: str, album: str = None, limit: int = 10):
        """
        根据关键词搜索歌曲，并可选地根据专辑名进行过滤。
        """
        payload = {"s": keyword, "type": 1, "limit": limit, "offset": 0}
        search_data = self._post_request(APIConstants.SEARCH_API, payload, is_eapi=True)
        # print(f"search_data: {search_data}")

        if (
            not search_data
            or search_data.get("code") != 200
            or not search_data.get("result", {}).get("songs")
        ):
            return []

        song_list = search_data["result"]["songs"]

        songs_to_process = []
        if album:
            for song in song_list:
                song_album = song.get("al", {}).get("name")
                if song_album and album.lower() in song_album.lower():
                    songs_to_process.append(song)
        else:
            songs_to_process = song_list

        formatted_results = []
        for song in songs_to_process:
            formatted_results.append(
                {
                    "id": song.get("id"),
                    "name": song.get("name"),
                    "artist": "、".join(
                        [ar.get("name") for ar in song.get("ar", []) if ar.get("name")]
                    ),
                    "album": song.get("al", {}).get("name"),
                }
            )
        return formatted_results

    async def get_song_details(self, song_id, level):
        """
        获取歌曲完整信息，并触发后台下载。
        """
        meta_data = await self._get_song_metadata(song_id)
        if not meta_data or not meta_data.get("songs"):
            return {"error": "获取歌曲元数据失败。"}
        meta_info = meta_data["songs"][0]

        wiki_details = await self._get_song_wiki_details(song_id)
        # 将获取到的详细信息合并到主信息字典中
        if wiki_details:
            print(f">>> 成功从百科接口获取到 {list(wiki_details.keys())} 等详细信息。")
            meta_info.update(wiki_details)

        lyric_data = await self._get_lyric_data(song_id)
        lyric = lyric_data.get("lrc", {}).get("lyric", "") if lyric_data else ""
        tlyric = lyric_data.get("tlyric", {}).get("lyric", "") if lyric_data else ""

        if self.db_manager and Config.DOWNLOADS_ENABLED:
            asyncio.create_task(
                self._background_download_task(song_id, meta_info, lyric, tlyric)
            )

        url_data = await self._get_song_url_data(song_id, level, meta_info)
        if (
            not url_data
            or not url_data.get("data")
            or not url_data.get("data")[0].get("url")
        ):
            return {"error": f"获取歌曲URL失败(请求音质:{level})"}
        song_info_url = url_data["data"][0]

        actual_quality = song_info_url.get("level")
        formatted_data = {
            "name": meta_info["name"],
            "artist": "、".join([artist["name"] for artist in meta_info["ar"]]),
            "album": meta_info["al"]["name"],
            "cover_url": meta_info["al"]["picUrl"],
            "quality_requested": level,
            "quality_actual": actual_quality,
            "size": Utils.format_size(song_info_url.get("size")),
            "url": song_info_url["url"].replace("http://", "https://"),
            "lyric": lyric,
            "tlyric": tlyric,
        }
        return formatted_data

    async def search_and_get_details(self, keyword: str, level: str, album: str = None):
        """
        根据关键词和可选的专辑名进行搜索，并验证结果的准确性，然后获取最匹配歌曲的详细信息。
        """
        try:
            target_artist, target_song = [
                x.strip().lower() for x in keyword.split(" - ", 1)
            ]
        except ValueError:
            return {"error": "关键词格式不正确，请使用 '歌手 - 歌曲名' 的格式。"}

        def find_exact_match(results):
            for song in results:
                result_artist = song.get("artist", "").lower()
                result_song = song.get("name", "").lower()
                if target_song == result_song and target_artist in result_artist:
                    return song.get("id")
            return None

        search_results = await self.search_song(keyword, album=album, limit=5)
        best_match_id = find_exact_match(search_results)

        if not best_match_id and album:
            print(f"未能从专辑 '{album}' 中找到精确匹配，尝试在所有专辑中搜索...")
            search_results = await self.search_song(keyword, album=None, limit=5)
            best_match_id = find_exact_match(search_results)

        if not best_match_id:
            return {"error": "未能找到精确匹配的歌曲"}

        return await self.get_song_details(best_match_id, level)

    async def get_playlist_info(self, playlist_id: str) -> dict:
        """获取歌单详情，包含歌曲ID、元数据和创建者ID。"""
        data = {"id": playlist_id, "n": 100000, "s": 0}
        response = await self._post_request(APIConstants.PLAYLIST_DETAIL_API, data)

        if not response or response.get("code") != 200:
            return {"error": f"获取网易云歌单 (ID: {playlist_id}) 详情失败。"}

        playlist_data = response.get("playlist", {})
        if not playlist_data:
            return {"error": "未在响应中找到歌单数据。"}

        playlist_name = playlist_data.get("name")
        creator_id = str(playlist_data.get("userId", ""))  # 获取创建者ID

        # 优先使用 tracks 获取完整信息
        tracks = playlist_data.get("tracks", [])
        # 如果 tracks 为空（某些情况下），回退到 trackIds 获取纯 ID 列表
        track_ids = playlist_data.get("trackIds", [])

        songs = []

        if tracks:
            for track in tracks:
                artist_names = "、".join(
                    [ar.get("name", "未知歌手") for ar in track.get("ar", [])]
                )
                songs.append(
                    {
                        "id": str(track.get("id")),
                        "name": track.get("name", "未知歌曲"),
                        "artist": artist_names,
                        "album": track.get("al", {}).get("name"),
                    }
                )
        else:
            # 备用逻辑：只有 ID
            for item in track_ids:
                songs.append({"id": str(item.get("id"))})

        return {
            "playlist_name": playlist_name,
            "creator_id": creator_id,  # 返回创建者ID
            "songs": songs,
        }

    def get_playlist_info_sync(self, playlist_id: str) -> dict:
        """
        get_playlist_info 的同步版本，用于在线程池中安全调用。
        """
        return asyncio.run(self.get_playlist_info(playlist_id))

    async def download_playlist_by_id(self, playlist_id: str, level: str) -> dict:
        """
        根据歌单ID，将整个歌单的歌曲加入后台下载队列。
        """
        data = {"id": playlist_id, "n": 100000, "s": 0}
        response = await self._post_request(APIConstants.PLAYLIST_DETAIL_API, data)
        if not response or response.get("code") != 200:
            return {
                "error": f"获取歌单 (ID: {playlist_id}) 详情失败，请检查ID是否正确。"
            }

        playlist_info = response.get("playlist", {})
        track_ids = [str(t["id"]) for t in playlist_info.get("trackIds", [])]
        total_songs = len(track_ids)

        if total_songs == 0:
            return {"error": f"歌单 (ID: {playlist_id}) 中没有找到任何歌曲。"}

        print(f"开始处理歌单 '{playlist_info.get('name')}'，共 {total_songs} 首歌曲。")

        # 遍历歌单中的所有歌曲ID
        for i, song_id in enumerate(track_ids):
            print(
                f"  -> 正在将第 {i + 1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列..."
            )
            # 调用已有的 get_song_details 方法，它会自动触发后台下载线程
            await self.get_song_details(song_id, level)
            await asyncio.sleep(1)  # 添加1秒延迟，避免因请求过快被服务器限制

        return {
            "message": f"歌单 '{playlist_info.get('name')}' 已成功加入下载队列，共 {total_songs} 首歌曲。"
        }

    async def download_album_by_id(self, album_id: str, level: str) -> dict:
        """
        根据专辑ID，将整个专辑的歌曲加入后台下载队列。
        """
        # 直接调用最可靠的 eapi 专辑接口来获取包含所有歌曲信息的完整响应
        album_response = {}
        try:
            params_for_cache_key = {"id": str(album_id), "e_r": "false"}
            cache_key = self._generate_cache_key(params_for_cache_key)
            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"
            eapi_payload = {
                "cache_key": cache_key,
                "id": str(album_id),
                "e_r": "false",
                "header": json.dumps(APIConstants.DEFAULT_CONFIG),
            }
            album_response = await self._post_request(
                final_url, eapi_payload, is_eapi=True
            )
        except Exception as e:
            return {"error": f"请求专辑 (ID: {album_id}) 数据时发生异常: {e}"}

        if not album_response or album_response.get("code") != 200:
            return {"error": f"获取专辑 (ID: {album_id}) 详情失败，请检查ID是否正确。"}

        songs = album_response.get("songs", [])
        album_name = album_response.get("album", {}).get("name", "未知专辑")
        total_songs = len(songs)

        if total_songs == 0:
            return {"error": f"专辑 (ID: {album_id}) 中没有找到任何歌曲。"}

        print(f"开始处理专辑 '{album_name}'，共 {total_songs} 首歌曲。")

        # 遍历专辑中的所有歌曲
        for i, song in enumerate(songs):
            song_id = str(song["id"])
            print(
                f"  -> 正在将第 {i + 1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列..."
            )
            # 同样调用 get_song_details 来触发下载
            await self.get_song_details(song_id, level)
            await asyncio.sleep(1)  # 添加1秒延迟

        return {
            "message": f"专辑 '{album_name}' 已成功加入下载队列，共 {total_songs} 首歌曲。"
        }

    async def start_background_playlist_download(self, playlist_id: str, level: str):
        """启动后台原生协程来执行异步歌单下载任务。"""
        asyncio.create_task(self.download_playlist_by_id(playlist_id, level))
        print(f"已为歌单 {playlist_id} 启动后台下载任务。")

    async def start_background_album_download(self, album_id: str, level: str):
        """启动后台原生协程来执行异步专辑下载任务。"""
        asyncio.create_task(self.download_album_by_id(album_id, level))
        print(f"已为专辑 {album_id} 启动后台下载任务。")

    async def add_songs_to_playlist(
        self, playlist_id: str, song_ids: List[str], user_id: str = None
    ) -> bool:
        """向网易云歌单添加歌曲。"""
        url = APIConstants.PLAYLIST_MANIPULATE_API
        # 遵循抓包示例：trackIds 是一个 "['id1', 'id2']" 格式的字符串
        track_ids_json_str = json.dumps([str(sid) for sid in song_ids])
        payload = {"pid": str(playlist_id), "trackIds": track_ids_json_str, "op": "add"}
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)
        return response and response.get("code") == 200

    async def remove_songs_from_playlist(
        self, playlist_id: str, song_ids: List[str], user_id: str = None
    ) -> bool:
        """从网易云歌单删除歌曲。"""
        url = APIConstants.PLAYLIST_MANIPULATE_API
        # 遵循抓包示例：trackIds 是一个 "['id1', 'id2']" 格式的字符串
        track_ids_json_str = json.dumps([str(sid) for sid in song_ids])
        payload = {"pid": str(playlist_id), "trackIds": track_ids_json_str, "op": "del"}
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)
        return response and response.get("code") == 200

    async def reorder_playlist(
        self, playlist_id: str, all_song_ids: List[str], user_id: str = None
    ) -> bool:
        """更新网易云歌单的完整歌曲顺序。"""
        url = APIConstants.PLAYLIST_MANIPULATE_API
        # 遵循抓包示例：trackIds 是一个 "[id1,id2]" 格式的字符串
        track_ids_str = f"[{','.join([str(sid) for sid in all_song_ids])}]"
        payload = {"pid": str(playlist_id), "trackIds": track_ids_str, "op": "update"}
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)
        return response and response.get("code") == 200

    async def get_daily_recommendations(self, user_id: str = None) -> list | dict:
        """
        异步获取网易云的每日推荐歌曲列表 (支持指定用户)。
        """
        url = APIConstants.DAILY_RECOMMEND_API

        # 要加密的明文 payload
        payload = {
            "/api/v3/discovery/recommend/songs": '{"ispush":"false"}',
            "/api/discovery/recommend/songs/history/recent": "",
            "header": "{}",
            "e_r": True,
        }

        # 传递 user_id 给 _post_request
        # 注意：对于 batch 接口，默认推断的 eapi 路径 /api/batch 是正确的，无需手动指定
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)

        if not response:
            return {"error": "获取每日推荐失败，未收到响应。"}

        # 从 "batch" 响应中提取出我们真正需要的部分
        recommend_data = response.get("/api/v3/discovery/recommend/songs", {})
        if not recommend_data or recommend_data.get("code") != 200:
            return {"error": "获取每日推荐失败。", "data": recommend_data}

        data = recommend_data.get("data") or {}
        raw_list = data.get("dailySongs") or []

        return raw_list

    async def get_style_recommend_tags(self, user_id: str = None) -> dict:
        """获取风格日推标签 (支持指定用户)"""
        url = APIConstants.STYLE_TAGS_API
        payload = {"header": "{}", "e_r": True}
        # 传递 user_id
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)

        if not response or response.get("code") != 200:
            return {"error": "获取风格日推标签失败。"}
        return response.get("data", {})

    async def get_private_fm_modes(self, user_id: str = None) -> dict:
        """获取私人FM模式 (支持指定用户)"""
        url = APIConstants.FM_MODES_API
        ext_json_data = {
            "clientLibraAbTest": {"fm-style-reopen": "t3", "fmNameTest0422": "c"},
            "isHomePageNewFramework": True,
            "userSetFMMode": True,
            "enableAutoPlay": True,
        }
        payload = {
            "positionCode": "FMTopModeDialog",
            "extJson": json.dumps(ext_json_data),
            "header": "{}",
            "e_r": True,
        }
        # 传递 user_id
        response = await self._post_request(
            url, payload, is_eapi=True, user_id=user_id, os_type="android"
        )

        if not response or response.get("code") != 200:
            return {"error": "获取私人FM模式失败。"}
        try:
            return (
                response.get("data", {})
                .get("crossPlatformResource", {})
                .get("dslData", {})
            )
        except AttributeError:
            return {"error": "解析响应数据失败。"}

    async def get_private_fm(
        self,
        mode: str = "DEFAULT",
        limit: int = 3,
        sub_mode: str = None,
        user_id: str = None,
    ) -> dict:
        """获取私人FM歌曲 (支持指定用户)"""
        url = APIConstants.RADIO_API
        payload = {
            "mode": mode,
            "entranceType": "main_bottom_tab",
            "limit": str(limit),
            "openAidj": "false",
            "header": "{}",
            "e_r": True,
        }
        if mode == "SCENE_RCMD" and sub_mode:
            payload["subMode"] = sub_mode

        # 传递 user_id
        response = await self._post_request(url, payload, is_eapi=True, user_id=user_id)

        if not response or response.get("code") != 200:
            return {"error": "获取私人FM数据失败。"}
        return response.get("data", [])

    async def get_style_recommend_playlist(
        self, tag_id: str, category_id: str, user_id: str = None
    ) -> dict:
        """获取风格日推歌单 (支持指定用户)"""
        # 步骤 1: 设置偏好 (带 user_id)
        url_step1 = APIConstants.STYLE_TAGS_SAVE_API
        tags_json_str = json.dumps(
            {"tagIds": [int(tag_id)], "categoryId": int(category_id)}
        )
        payload_step1 = {"tags": tags_json_str, "header": "{}", "e_r": True}

        save_response = await self._post_request(
            url_step1,
            payload_step1,
            is_eapi=True,
            user_id=user_id,
        )
        if not save_response or save_response.get("code") != 200:
            return {"error": "设置风格偏好失败。"}

        # 步骤 2: 获取列表 (带 user_id)
        url_step2 = APIConstants.STYLE_PLAYLIST_GET_API
        payload_step2 = {"header": "{}", "e_r": True}

        playlist_response = await self._post_request(
            url_step2,
            payload_step2,
            is_eapi=True,
            user_id=user_id,
        )

        if not playlist_response or playlist_response.get("code") != 200:
            return {"error": "获取风格日推歌单失败。"}

        return playlist_response.get("data", {})
