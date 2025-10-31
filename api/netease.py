# --- 1. 标准库导入 ---
import os
import json
import re
import base64
import datetime
import threading
import urllib.parse
from hashlib import md5
from random import randrange
import time
import asyncio
from fastapi.concurrency import run_in_threadpool

# --- 2. 第三方库导入 ---
import httpx
from opencc import OpenCC

# mutagen (处理音乐元数据)
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TYER, TPOS, TCON, TPUB, TDOR, TCOM, TEXT, TPE4, TBPM, TXXX
from mutagen.flac import FLAC, Picture

# cryptography (处理网易云加密)
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- 3. 您自己的模块导入 ---
from utils.helpers import Utils
from core.config import Config

class APIConstants:
    """API相关常量"""
    AES_KEY = b"e82ckenh8dichen8"
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/8.9.75'
    DEFAULT_CONFIG = {"os": "pc", "appver": "8.9.75", "osver": "", "deviceId": "pyncm!"}
    SONG_URL_V1 = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
    SONG_DETAIL_V3 = "https://interface3.music.163.com/api/v3/song/detail"
    LYRIC_API = "https://interface3.music.163.com/api/song/lyric"
    SEARCH_API = "https://interface.music.163.com/eapi/cloudsearch/pc"
    PLAYLIST_DETAIL_API = 'https://music.163.com/api/v6/playlist/detail'
    ALBUM_DETAIL_API = 'https://music.163.com/api/v1/album/'
    ALBUM_V3_DETAIL = "https://music.163.com/eapi/album/v3/detail"
    CACHE_KEY_AES_KEY = b')(13daqP@ssw0rd~'
    SONG_WIKI_API = "https://interface3.music.163.com/api/link/page/parent/relation/construct/info"

class NeteaseMusicAPI:
    def __init__(self, cookie_str: str, local_api_instance, music_directory: str, flac_directory: str):
        self.cookies = Utils.parse_cookie_str(cookie_str)
        self.local_api = local_api_instance
        self.music_directory = music_directory
        self.flac_directory = flac_directory
        self.converter = OpenCC('t2s')
        self.headers = {'User-Agent': APIConstants.USER_AGENT}
        self.quality_map = {
            "standard": "128", "exhigh": "320", "lossless": "flac",
            "hires": "hires", "jyeffect": "jyeffect", "sky": "sky",
            "jymaster": "master"
        }
        self.album_cache = {}
        # 初始化异步HTTP客户端
        self.client = httpx.AsyncClient(timeout=20.0)

    def _eapi_encrypt(self, url_path: str, payload: dict) -> dict:
        """加密 EAPI 请求体。"""
        digest = md5(f"nobody{url_path}use{json.dumps(payload)}md5forencrypt".encode('utf-8')).hexdigest()
        params_str = f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
        padder = padding.PKCS7(algorithms.AES(APIConstants.AES_KEY).block_size).padder()
        padded_data = padder.update(params_str.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(APIConstants.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return {'params': encrypted_data.hex().upper()}
        
    def _eapi_decrypt(self, encrypted_bytes: bytes) -> str:
        """解密 EAPI 响应。"""
        AES_KEY = b"e82ckenh8dichen8"
        try:
            cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
            decryptor = cipher.decryptor()
            unpadder = padding.PKCS7(algorithms.AES(AES_KEY).block_size).unpadder()
            decrypted_padded_data = decryptor.update(encrypted_bytes) + decryptor.finalize()
            unpadded_data = unpadder.update(decrypted_padded_data) + unpadder.finalize()
            return unpadded_data.decode('utf-8')
        except Exception as e:
            raise ValueError(f"EAPI 响应解密失败: {e}")

    def _generate_cache_key(self, params: dict) -> str:
        """生成 album/v3/detail 接口所需的 cache_key。"""
        sorted_keys = sorted(params.keys(), key=lambda k: ord(k[0]))
        query_string = "&".join([f"{k}={params[k]}" for k in sorted_keys])
        cipher = Cipher(algorithms.AES(APIConstants.CACHE_KEY_AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(algorithms.AES(APIConstants.CACHE_KEY_AES_KEY).block_size).padder()
        padded_data = padder.update(query_string.encode('utf-8')) + padder.finalize()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return base64.b64encode(encrypted_data).decode('utf-8')

    async def _post_request(self, url: str, data: dict, is_eapi=False):
        """通用的异步POST请求函数。"""
        try:
            if is_eapi:
                url_path = urllib.parse.urlparse(url).path.replace("/eapi/", "/api/")
                data = self._eapi_encrypt(url_path, data)
            response = await self.client.post(url, headers=self.headers, cookies=self.cookies, data=data)
            response.raise_for_status()
            if not response.content: return None
            if is_eapi:
                try:
                    return response.json()
                except json.JSONDecodeError:
                    decrypted_text = self._eapi_decrypt(response.content)
                    return json.loads(decrypted_text)
            else:
                return response.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"网易云请求或处理出错: {e}")
            return None

    async def _post_song_wiki_request(self, song_id: str):
        """专门用于请求歌曲百科接口的函数。"""
        ext_json = json.dumps({"states": {"playingResource": {"current": str(song_id)}}})
        url_query_params = {'extJson': ext_json, 'positionCode': "songWikiMainPosition"}
        eapi_payload_to_encrypt = {"extJson": ext_json, "positionCode": "songWikiMainPosition", "header": "{}", "e_r": True}
        url_path = urllib.parse.urlparse(APIConstants.SONG_WIKI_API).path
        encrypted_post_body = self._eapi_encrypt(url_path, eapi_payload_to_encrypt)
        try:
            response = await self.client.post(APIConstants.SONG_WIKI_API, params=url_query_params, data=encrypted_post_body, headers=self.headers, cookies=self.cookies)
            response.raise_for_status()
            return json.loads(response.text)
        except (httpx.RequestError, json.JSONDecodeError) as e:
            print(f"网易云百科接口请求或处理出错: {e}")
            return None

    async def _get_song_url_data(self, song_id: str, level: str, meta_info: dict):
        """获取歌曲指定音质的播放链接。"""
        config = APIConstants.DEFAULT_CONFIG.copy()
        config["requestId"] = str(randrange(20000000, 30000000))
        payload = {'ids': [str(song_id)], 'level': level, 'header': json.dumps(config)}
        if level == 'hires': payload['encodeType'] = 'hires'
        else: payload['encodeType'] = 'flac'
        if level == 'sky': payload['immerseType'] = 'c51'
        if level == 'jyeffect':
            try:
                charge_info_list = meta_info.get('privilege', {}).get('chargeInfoList', [])
                jyeffect_info = next((item for item in charge_info_list if item.get('chargeType') == 10), None)
                if jyeffect_info and jyeffect_info.get('bizId'):
                    payload['soundEffect'] = {"type": "jyeffect", "bizId": jyeffect_info['bizId']}
            except Exception: pass
        return await self._post_request(APIConstants.SONG_URL_V1, payload, is_eapi=True)
        
    async def _get_song_metadata(self, song_id: str):
        """获取歌曲的基础元数据。"""
        data = {'c': json.dumps([{"id": song_id, "v": 0}])}
        return await self._post_request(APIConstants.SONG_DETAIL_V3, data)

    async def _get_song_wiki_details(self, song_id: str) -> dict:
        """调用歌曲百科接口，获取详细的创作者和属性信息。"""
        response_data = await self._post_song_wiki_request(song_id)
        if not response_data or response_data.get('code') != 200: return {}
        details = {}
        try:
            blocks = response_data.get('data', {}).get('blocks', [])
            wiki_block = next((b for b in blocks if b.get('bizCode') == 'songDetailNewSongWiki'), None)
            if not wiki_block: return {}
            nested_blocks = wiki_block.get('rnData', {}).get('blocks', [])
            info_block = next((b for b in nested_blocks if b.get('blockCode') == 'wikiSubBlockSongInfoVo'), None)
            if info_block:
                creators = {}
                elements = info_block.get('blockInfo', {}).get('wikiSubElementVos', [])
                for element in elements:
                    title = element.get('title', '').strip()
                    names = [meta.get('text') for meta in element.get('wikiSubMetaVos', []) if meta.get('text')]
                    if not title or not names: continue
                    if '作词' in title: creators.setdefault('lyricist', []).extend(names)
                    elif '作曲' in title: creators.setdefault('composer', []).extend(names)
                    elif '制作人' in title: creators.setdefault('producer', []).extend(names)
                    elif '编曲' in title: creators.setdefault('arranger', []).extend(names)
                    elif '混音' in title: creators.setdefault('mix', []).extend(names)
                    elif '母带' in title: creators.setdefault('mastering', []).extend(names)
                for key, value in creators.items():
                    details[key] = ";".join(value)
            base_info_block = next((b for b in nested_blocks if b.get('blockCode') == 'wikiSubBlockBaseInfoVo'), None)
            if base_info_block:
                elements = base_info_block.get('blockInfo', {}).get('wikiSubElementVos', [])
                for element in elements:
                    title = element.get('title', '').strip()
                    if title == '曲风' and element.get('wikiSubMetaVos'):
                        details['genre_from_wiki'] = element['wikiSubMetaVos'][0].get('text')
                    if title == 'BPM' and element.get('content'):
                        details['bpm'] = element.get('content')
        except Exception as e:
            print(f"解析网易云歌曲百科信息时出错: {e}")
        return details

    async def _get_lyric_data(self, song_id: str):
        """获取歌曲歌词。"""
        data = {'id': song_id, 'cp': 'false', 'tv': '0', 'lv': '0', 'rv': '0', 'kv': '0'}
        return await self._post_request(APIConstants.LYRIC_API, data)

    async def _get_album_details_by_id(self, album_id: str) -> dict:
        """获取专辑详情，用于补充元数据。"""
        if not album_id: return None
        try:
            params_for_cache_key = {'id': str(album_id), 'e_r': 'true'}
            cache_key = self._generate_cache_key(params_for_cache_key)
            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"
            eapi_payload = {"id": str(album_id), "e_r": "true", "header": json.dumps(APIConstants.DEFAULT_CONFIG)}
            response_json = await self._post_request(final_url, eapi_payload, is_eapi=True)
            if response_json and response_json.get('code') == 200:
                print(f">>> 从 eapi 专辑接口 (ID: {album_id}) 获取到详情。")
                return response_json.get('album')
            else:
                print(f">>> 警告: 查询 eapi 专辑接口 (ID: {album_id}) 失败: {response_json}")
        except Exception as e:
            print(f">>> 警告: 调用 eapi 专辑接口 (ID: {album_id}) 时发生异常: {e}")
        return None

    async def _embed_metadata(self, file_path: str, song_info: dict, lyric: str, tlyric: str):
        """写入包括百科接口在内的所有可用元数据。"""
        try:
            # --- 1. 提取所有元数据 ---
            album_info = song_info.get('al', {})
            artists = song_info.get('ar', [])
            
            song_name = song_info.get('name')
            aliases = song_info.get('alia', [])
            if aliases:
                song_name = f"{song_name} ({' / '.join(aliases)})"
                
            album_name = album_info.get('name')
            album_id = str(album_info.get('id'))
            artist_names = ";".join([artist['name'] for artist in artists])
            track_number = song_info.get('no')
            total_tracks = song_info.get('size')
            disc_number = song_info.get('cd')
            
            lyricist = song_info.get('lyricist')
            composer = song_info.get('composer')
            producer = song_info.get('producer')
            arranger = song_info.get('arranger')
            mix_engineer = song_info.get('mix')
            mastering_engineer = song_info.get('mastering')
            bpm = song_info.get('bpm')
            genre_from_wiki = song_info.get('genre_from_wiki')

            # --- 2. 通过专辑接口获取权威元数据 ---
            album_details = self.album_cache.get(album_id)
            if album_details is None and album_id not in self.album_cache:
                album_details = self._get_album_details_by_id(album_id)
                self.album_cache[album_id] = album_details

            if album_details:
                album_artist = ";".join([artist['name'] for artist in album_details.get('artists', [])]) or artist_names
                publisher = album_details.get('company')
                genre = genre_from_wiki or album_details.get('subType')
                publish_time_ms = album_details.get('publishTime', 0)
            else:
                album_artist = ";".join([ar['name'] for ar in album_info.get('ar', artists)]) or artist_names
                publisher = album_info.get('company')
                genre = genre_from_wiki or album_info.get('subType')
                publish_time_ms = song_info.get('publishTime', 0)
            
            release_date_str, release_year_str = None, None
            if publish_time_ms and publish_time_ms > 0:
                dt_object = datetime.datetime.fromtimestamp(publish_time_ms / 1000)
                release_date_str = dt_object.strftime('%Y-%m-%d')
                release_year_str = dt_object.strftime('%Y')

            # --- 3. 歌词、封面处理 ---
            full_lyric = f"{lyric}\n\n--- 翻译 ---\n\n{tlyric}" if tlyric and lyric else lyric
            image_data = None
            if album_info.get('picUrl'):
                try:
                    # 异步下载封面图片
                    async with httpx.AsyncClient() as client:
                        image_response = await client.get(album_info['picUrl'], timeout=30)
                        if image_response.status_code == 200:
                            image_data = image_response.content
                except httpx.RequestError as e:
                    print(f"下载封面时出错: {e}")
            
            # --- 4. 文件写入 ---
            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                if audio.tags is None: audio.add_tags()
                
                audio.tags.add(TIT2(encoding=3, text=song_name))
                audio.tags.add(TPE1(encoding=3, text=artist_names))
                audio.tags.add(TALB(encoding=3, text=album_name))
                if album_artist: audio.tags.add(TPE2(encoding=3, text=album_artist))
                if track_number: audio.tags.add(TRCK(encoding=3, text=f"{track_number}/{total_tracks}" if total_tracks else str(track_number)))
                if disc_number: audio.tags.add(TPOS(encoding=3, text=str(disc_number)))
                if genre: audio.tags.add(TCON(encoding=3, text=genre))
                if publisher: audio.tags.add(TPUB(encoding=3, text=publisher))
                if lyricist: audio.tags.add(TEXT(encoding=3, text=lyricist))
                if composer: audio.tags.add(TCOM(encoding=3, text=composer))
                if producer: audio.tags.add(TPE4(encoding=3, text=producer))
                if arranger: audio.tags.add(TPE4(encoding=3, text=arranger)) # <-- 新增：写入编曲
                if bpm: audio.tags.add(TBPM(encoding=3, text=str(bpm)))
                if mix_engineer: audio.tags.add(TXXX(encoding=3, desc='MIXING', text=mix_engineer))
                if mastering_engineer: audio.tags.add(TXXX(encoding=3, desc='MASTERING', text=mastering_engineer))
                if release_date_str:
                    audio.tags.add(TDRC(encoding=3, text=release_date_str))
                    audio.tags.add(TDOR(encoding=3, text=release_date_str))
                if release_year_str: audio.tags.add(TYER(encoding=3, text=release_year_str))
                if image_data: audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=image_data))
                if full_lyric: audio.tags.add(USLT(encoding=3, text=full_lyric))
                audio.save()

            elif file_path.lower().endswith('.flac'):
                audio = FLAC(file_path)
                audio['title'] = song_name
                audio['artist'] = artist_names
                audio['album'] = album_name
                if album_artist: audio['albumartist'] = album_artist
                if track_number: audio['tracknumber'] = str(track_number)
                if total_tracks: audio['tracktotal'] = str(total_tracks)
                if disc_number: audio['discnumber'] = str(disc_number)
                if genre: audio['genre'] = genre
                if publisher: audio['organization'] = publisher
                if lyricist: audio['lyricist'] = lyricist
                if composer: audio['composer'] = composer
                if producer: audio['producer'] = producer
                if arranger: audio['arranger'] = arranger # <-- 新增：写入编曲
                if mix_engineer: audio['mixing engineer'] = mix_engineer
                if mastering_engineer: audio['mastering engineer'] = mastering_engineer
                if bpm: audio['bpm'] = str(bpm)
                if release_date_str: audio['date'] = release_date_str
                if release_year_str: audio['year'] = release_year_str
                if full_lyric: audio['lyrics'] = full_lyric
                
                audio.clear_pictures()
                if image_data:
                    picture = Picture()
                    picture.type = 3; picture.mime = "image/jpeg"; picture.desc = "Cover"; picture.data = image_data
                    audio.add_picture(picture)
                audio.save()

            print(f"后台任务: 已将最终元数据嵌入 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生严重错误 - {e}")



    async def _download_and_process_single_version(self, search_key, quality, download_url, extension, song_info, lyric, tlyric):
        album_name = song_info.get('al', {}).get('name', '')
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
        base_name = f"{search_key} {safe_album_name}" if safe_album_name else search_key
        
        save_directory = self.music_directory # 默认保存在主音乐目录

        # 规则：当且仅当要下载的是 'flac' 音质时，进行判断
        if quality == 'flac':
            # 查询数据库，看这首歌是否已存在 master 版本
            existing_qualities = await run_in_threadpool(self.local_api.get_existing_qualities, search_key=search_key, album=album_name)
            if 'master' in existing_qualities:
                # 如果 master 已存在，则将 flac 的保存路径指向子目录
                print(f">>> 检测到已存在 master 版本，将把 flac 版本下载到 'flac' 子目录。")
                save_directory = self.flac_directory

        # 根据最终确定的目录，构造完整的文件路径
        filename_suffix = " [M]" if quality == 'master' else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)
        file_path = os.path.join(save_directory, f"{safe_filename}{filename_suffix}{extension}")
        
        # --- 后续的下载、重试、日志记录逻辑 --- 
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"后台任务: 开始下载 '{base_name}' ({quality}) (第 {attempt + 1} 次尝试)")
                async with self.client.stream("GET", download_url, timeout=300) as r:
                    r.raise_for_status()
                    with open(file_path, 'wb') as f:
                        async for chunk in r.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                
                print(f"后台任务: 下载成功 - {file_path}")
                await self._embed_metadata(file_path, song_info, lyric, tlyric)
                self.local_api.add_song_to_db(
                    search_key=search_key, file_path=file_path,
                    duration=song_info.get('dt', 0), album=album_name, quality=quality
                )
                return True
            except httpx.RequestError as e:
                print(f"后台任务: 下载 '{search_key}' 失败 (第 {attempt + 1} 次尝试)，错误: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5) # 异步等待
                else:
                    song_id = song_info.get('id', '未知ID')
                    self.logger.error(f"歌曲下载失败 - ID: {song_id}, 名称: '{search_key}', 音质: {quality}, 错误: {e}")
                    if os.path.exists(file_path): os.remove(file_path)
                    return False
        return False

    def _run_async_in_thread(self, async_func, *args, **kwargs):
        """一个同步的包装器，用于在独立的线程中创建并运行一个新的事件循环。"""
        asyncio.run(async_func(*args, **kwargs))

    async def _background_download_task(self, song_id: str, meta_info: dict, lyric: str, tlyric: str):
        """(后台线程) 智能分层下载，并自动写入数据库。"""
        artist_string = "、".join([artist['name'] for artist in meta_info['ar']])
        song_name = meta_info['name']
        search_key = self.converter.convert(f"{artist_string} - {song_name}")

        # 1. 从数据库获取该歌曲所有音质
        album_name = meta_info.get('al', {}).get('name', '') if meta_info else ''
        existing_qualities = await run_in_threadpool(self.local_api.get_existing_qualities, search_key=search_key, album=album_name)
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        # 2. 场景一: 如果有master则跳过下载
        if 'master' in existing_qualities:
            print(f"后台任务: 已存在 master 版本，任务结束。")
            return

        # 3. 场景二: 没有master有flac，尝试下载jymaster
        if 'flac' in existing_qualities:
            print(f"后台任务: 已存在 flac 版本，尝试补充 jymaster 版本...")
            url_info_data = await self._get_song_url_data(song_id, 'jymaster', meta_info)
            if url_info_data and url_info_data.get('data'):
                song_info_url = url_info_data['data'][0]
                # 严格检查返回的是否是 jymaster
                if song_info_url.get('url') and song_info_url.get('level') == 'jymaster':
                    await self._download_and_process_single_version(
                        search_key, 'master', song_info_url['url'], '.flac', meta_info, lyric, tlyric
                    )
            return # 无论是否成功，任务都结束

        # 4. 场景三: 有320k或128k，尝试下载jymaster和lossless
        if '320' in existing_qualities or '128' in existing_qualities:
            print(f"后台任务: 已存在低品质版本，尝试补充 jymaster 和 lossless...")
            for level in ['jymaster', 'lossless']:
                url_info_data = await self._get_song_url_data(song_id, level, meta_info)
                if url_info_data and url_info_data.get('data'):
                    song_info_url = url_info_data['data'][0]
                    if song_info_url.get('url') and song_info_url.get('level') == level:
                        db_quality = self.quality_map.get(level)
                        await self._download_and_process_single_version(
                            search_key, db_quality, song_info_url['url'], '.flac', meta_info, lyric, tlyric
                        )
            return

        # 5. 场景四: 如果完全没有这首歌
        if not existing_qualities:
            print(f"后台任务: 本地库无此歌曲，开始智能下载...")
            # 先请求jymaster
            url_info_data = await self._get_song_url_data(song_id, 'jymaster', meta_info)
            if not url_info_data or not url_info_data.get('data'):
                print(f"后台任务: 无法为 '{search_key}' 获取任何音质的URL。")
                return

            song_info_url = url_info_data['data'][0]
            download_url = song_info_url.get('url')
            actual_level = song_info_url.get('level')

            if not download_url or not actual_level: return
            
            db_quality = self.quality_map.get(actual_level)
            extension = f".{song_info_url.get('type', 'mp3')}"

            # 如果返回的正是jymaster
            if actual_level == 'jymaster':
                await self._download_and_process_single_version(search_key, 'master', download_url, extension, meta_info, lyric, tlyric)
                if Config.ENABLE_FLAC_DOWNLOAD_WHEN_HAVE_MASTER:
                    # 然后再请求lossless下载
                    print(f"后台任务: 已下载 master, 继续请求 lossless...")
                    lossless_data = await self._get_song_url_data(song_id, 'lossless', meta_info)
                    if lossless_data and lossless_data.get('data'):
                        lossless_info = lossless_data['data'][0]
                        if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                            await self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)
            
            # 如果返回的是exhigh或standard
            elif actual_level in ['exhigh', 'standard']:
                print("只有低品质版本...")
                await self._download_and_process_single_version(search_key, db_quality, download_url, extension, meta_info, lyric, tlyric)

            # 如果返回的不是上面几种情况 (例如返回了lossless)
            else:
                # 请求lossless下载
                print(f"后台任务: 请求 jymaster 返回了 {actual_level}，现在请求 lossless...")
                lossless_data = await self._get_song_url_data(song_id, 'lossless', meta_info)
                if lossless_data and lossless_data.get('data'):
                    lossless_info = lossless_data['data'][0]
                    if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                         await self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)

    def search_song(self, keyword: str, album: str = None, limit: int = 10):
        """
        根据关键词搜索歌曲，并可选地根据专辑名进行过滤。
        """
        payload = {'s': keyword, 'type': 1, 'limit': limit, 'offset': 0}
        search_data = self._post_request(APIConstants.SEARCH_API, payload, is_eapi=True)
        # print(f"search_data: {search_data}")

        if not search_data or search_data.get('code') != 200 or not search_data.get('result', {}).get('songs'):
            return []
        
        song_list = search_data['result']['songs']
        
        songs_to_process = []
        if album:
            for song in song_list:
                song_album = song.get('al', {}).get('name')
                if song_album and album.lower() in song_album.lower():
                    songs_to_process.append(song)
        else:
            songs_to_process = song_list
        
        formatted_results = []
        for song in songs_to_process:
            formatted_results.append({
                'id': song.get('id'),
                'name': song.get('name'),
                'artist': "、".join([ar.get('name') for ar in song.get('ar', []) if ar.get('name')]),
                'album': song.get('al', {}).get('name'),
            })
        return formatted_results

    async def get_song_details(self, song_id, level):
        """
        获取歌曲完整信息，并触发后台下载。
        """
        meta_data = await self._get_song_metadata(song_id)
        if not meta_data or not meta_data.get('songs'): return {"error": "获取歌曲元数据失败。"}
        meta_info = meta_data['songs'][0]

        wiki_details = await self._get_song_wiki_details(song_id)
        # 将获取到的详细信息合并到主信息字典中
        if wiki_details:
            print(f">>> 成功从百科接口获取到 {list(wiki_details.keys())} 等详细信息。")
            meta_info.update(wiki_details)

        lyric_data = await self._get_lyric_data(song_id)
        lyric = lyric_data.get('lrc', {}).get('lyric', '') if lyric_data else ''
        tlyric = lyric_data.get('tlyric', {}).get('lyric', '') if lyric_data else ''

        if self.local_api and Config.DOWNLOADS_ENABLED:
            # 使用同步包装器，在新的后台线程中运行异步下载任务
            threading.Thread(target=self._run_async_in_thread, args=(self._background_download_task, song_id, meta_info, lyric, tlyric)).start()

        url_data = await self._get_song_url_data(song_id, level, meta_info)
        if not url_data or not url_data.get('data') or not url_data.get('data')[0].get('url'):
            return {"error": f"获取歌曲URL失败(请求音质:{level})"}
        song_info_url = url_data['data'][0]
        
        actual_quality = song_info_url.get('level')
        formatted_data = {
            "name": meta_info['name'],
            "artist": "、".join([artist['name'] for artist in meta_info['ar']]),
            "album": meta_info['al']['name'],
            "cover_url": meta_info['al']['picUrl'],
            "quality_requested": level,
            "quality_actual": actual_quality,
            "size": Utils.format_size(song_info_url.get('size')),
            "url": song_info_url['url'].replace("http://", "https://"),
            "lyric": lyric,
            "tlyric": tlyric
        }
        return formatted_data
    

    async def search_and_get_details(self, keyword: str, level: str, album: str = None):
        """
        根据关键词和可选的专辑名进行搜索，并验证结果的准确性，然后获取最匹配歌曲的详细信息。
        """
        try:
            target_artist, target_song = [x.strip().lower() for x in keyword.split(' - ', 1)]
        except ValueError:
            return {"error": "关键词格式不正确，请使用 '歌手 - 歌曲名' 的格式。"}

        def find_exact_match(results):
            for song in results:
                result_artist = song.get('artist', '').lower()
                result_song = song.get('name', '').lower()
                if target_song == result_song and target_artist in result_artist:
                    return song.get('id')
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
        """
        仅获取歌单的详细信息（名称和歌曲列表），不触发任何下载。
        """
        data = {'id': playlist_id, 'n': 100000, 's': 0}
        response = await self._post_request(APIConstants.PLAYLIST_DETAIL_API, data)
        if not response or response.get('code') != 200:
            return {"error": f"获取网易云歌单 (ID: {playlist_id}) 详情失败。"}

        playlist_data = response.get('playlist', {})
        if not playlist_data:
            return {"error": "未在响应中找到歌单数据。"}
            
        playlist_name = playlist_data.get('name')
        # 网易云的完整曲目信息在 tracks 字段中
        tracks = playlist_data.get('tracks', [])
        
        songs = []
        for track in tracks:
            artist_names = '、'.join([ar.get('name', '未知歌手') for ar in track.get('ar', [])])
            songs.append({
                'name': track.get('name', '未知歌曲'),
                'artist': artist_names
            })

        return {
            "playlist_name": playlist_name,
            "songs": songs
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
        data = {'id': playlist_id, 'n': 100000, 's': 0}
        response = await self._post_request(APIConstants.PLAYLIST_DETAIL_API, data)
        if not response or response.get('code') != 200:
            return {"error": f"获取歌单 (ID: {playlist_id}) 详情失败，请检查ID是否正确。"}
        
        playlist_info = response.get('playlist', {})
        track_ids = [str(t['id']) for t in playlist_info.get('trackIds', [])]
        total_songs = len(track_ids)

        if total_songs == 0:
            return {"error": f"歌单 (ID: {playlist_id}) 中没有找到任何歌曲。"}

        print(f"开始处理歌单 '{playlist_info.get('name')}'，共 {total_songs} 首歌曲。")

        # 遍历歌单中的所有歌曲ID
        for i, song_id in enumerate(track_ids):
            print(f"  -> 正在将第 {i+1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列...")
            # 调用已有的 get_song_details 方法，它会自动触发后台下载线程
            await self.get_song_details(song_id, level)
            await asyncio.sleep(1) # 添加1秒延迟，避免因请求过快被服务器限制
        
        return {"message": f"歌单 '{playlist_info.get('name')}' 已成功加入下载队列，共 {total_songs} 首歌曲。"}

    async def download_album_by_id(self, album_id: str, level: str) -> dict:
        """
        根据专辑ID，将整个专辑的歌曲加入后台下载队列。
        """
        # 直接调用最可靠的 eapi 专辑接口来获取包含所有歌曲信息的完整响应
        album_response = {}
        try:
            params_for_cache_key = {'id': str(album_id), 'e_r': 'true'}
            cache_key = self._generate_cache_key(params_for_cache_key)
            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"
            eapi_payload = {
                "id": str(album_id),
                "e_r": "true",
                "header": json.dumps(APIConstants.DEFAULT_CONFIG)
            }
            album_response = await self._post_request(final_url, eapi_payload, is_eapi=True)
        except Exception as e:
            return {"error": f"请求专辑 (ID: {album_id}) 数据时发生异常: {e}"}

        if not album_response or album_response.get('code') != 200:
            return {"error": f"获取专辑 (ID: {album_id}) 详情失败，请检查ID是否正确。"}
        
        songs = album_response.get('songs', [])
        album_name = album_response.get('album', {}).get('name', '未知专辑')
        total_songs = len(songs)

        if total_songs == 0:
            return {"error": f"专辑 (ID: {album_id}) 中没有找到任何歌曲。"}

        print(f"开始处理专辑 '{album_name}'，共 {total_songs} 首歌曲。")
        
        # 遍历专辑中的所有歌曲
        for i, song in enumerate(songs):
            song_id = str(song['id'])
            print(f"  -> 正在将第 {i+1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列...")
            # 同样调用 get_song_details 来触发下载
            await self.get_song_details(song_id, level)
            await asyncio.sleep(1) # 添加1秒延迟
        
        return {"message": f"专辑 '{album_name}' 已成功加入下载队列，共 {total_songs} 首歌曲。"}

    def start_background_playlist_download(self, playlist_id: str, level: str):
        """启动后台线程来执行异步歌单下载任务。"""
        thread = threading.Thread(target=self._run_async_in_thread, args=(self.download_playlist_by_id, playlist_id, level))
        thread.daemon = True
        thread.start()
        print(f"已为歌单 {playlist_id} 启动后台下载线程。")

    def start_background_album_download(self, album_id: str, level: str):
        """启动后台线程来执行异步专辑下载任务。"""
        thread = threading.Thread(target=self._run_async_in_thread, args=(self.download_album_by_id, album_id, level))
        thread.daemon = True
        thread.start()
        print(f"已为专辑 {album_id} 启动后台下载线程。")


