# --- 1. 标准库导入 ---
import os
import re
import json
import time
import random
import base64
import logging
import datetime
import threading
import urllib.parse
import asyncio
from fastapi.concurrency import run_in_threadpool

# --- 2. 第三方库导入 ---
import httpx
from opencc import OpenCC

# mutagen (处理音乐元数据)
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TYER, TPOS, TCON, TPUB, TDOR, TEXT, TCOM, TPE4, TBPM
from mutagen.flac import FLAC, Picture

# --- 3. 您自己的模块导入 ---
from utils.helpers import Utils
from core.config import Config

class QQMusicAPI:
    def __init__(self, local_api_instance, music_directory: str, flac_directory: str):
        self.local_api = local_api_instance
        self.music_directory = music_directory
        self.flac_directory = flac_directory
        self.converter = OpenCC('t2s')
        self.base_url = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Referer': 'https://y.qq.com/',
            'Origin': 'https://y.qq.com/',
        }
        self.file_config = {
            '128': {'s': 'M500', 'e': '.mp3', 'bitrate': '128kbps'},
            '320': {'s': 'M800', 'e': '.mp3', 'bitrate': '320kbps'},
            'flac': {'s': 'F000', 'e': '.flac', 'bitrate': 'FLAC'},
            'master': {'s': 'AI00', 'e': '.flac', 'bitrate': 'Master'},
        }
        self.album_cache = {}
        self._setup_logger()
        self.client = httpx.AsyncClient(timeout=20.0)

    def _setup_logger(self):
        logger = logging.getLogger('QQMusicDownloader')
        logger.setLevel(logging.ERROR)
        if not logger.handlers:
            handler = logging.FileHandler('download_errors_qq.log', encoding='utf-8')
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        self.logger = logger

    async def _get_request(self, url, params=None):
        try:
            response = await self.client.get(url, params=params, headers=self.headers, cookies=Config.QQ_USER_CONFIG)
            response.raise_for_status()
            text = response.text
            if text.startswith('callback('):
                return json.loads(text[9:-1])
            return response.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"QQ音乐GET请求出错: {e}")
            return None

    async def _post_request(self, json_data):
        try:
            safe_json_data = json.loads(json.dumps(json_data, default=list))
            response = await self.client.post(self.base_url, json=safe_json_data, headers=self.headers, cookies=Config.QQ_USER_CONFIG)
            response.raise_for_status()
            return response.json()
        except (httpx.RequestError, json.JSONDecodeError, ValueError) as e:
            print(f"QQ音乐POST请求出错: {e}")
            return None

    async def _resolve_song_ids(self, song_mid: str = None, song_id: int = None) -> dict:
        if not song_mid and not song_id: return None
        url = 'https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg'
        params = {'platform': 'yqq', 'format': 'json', 'outCharset': 'utf-8'}
        if song_id:
            params['songid'] = song_id
        else:
            params['songmid'] = song_mid
        
        response = await self._get_request(url, params=params)
        if response and response.get('code') == 0 and 'data' in response and len(response['data']) > 0:
            song_data = response['data'][0]
            return {"id": song_data.get('id'), "mid": song_data.get('mid')}
        print(f"ID解析失败: mid={song_mid}, id={song_id}。响应: {response}")
        return None

    async def _fetch_single_url(self, song_mid, quality, guid, uin):
        if quality not in self.file_config:
            return quality, None
        config = self.file_config[quality]
        filename = f"{config['s']}{song_mid}{song_mid}{config['e']}"
        payload = {"req_1": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey", "param": {"filename": [filename], "guid": guid, "songmid": [song_mid], "songtype": [0], "uin": uin, "loginflag": 1, "platform": "20"}}, "comm": {"uin": uin, "format": "json", "ct": 24, "cv": 0}}
        
        vkey_data = await self._post_request(payload)
        if vkey_data and vkey_data.get('req_1', {}).get('data', {}).get('midurlinfo', [{}])[0].get('purl'):
            purl = vkey_data['req_1']['data']['midurlinfo'][0]['purl']
            domain = next((d for d in vkey_data['req_1']['data'].get('sip', []) if 'pv.music' in d), 'https://isure.stream.qqmusic.qq.com/')
            return quality, domain + purl
        return quality, None

    async def _get_album_details_by_mid(self, album_mid: str) -> dict:
        if not album_mid: return None
        url = 'https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg'
        params = {'albummid': album_mid, 'format': 'json', 'outCharset': 'utf-8'}
        response = await self._get_request(url, params=params)
        if response and response.get('code', -1) == 0:
            return response.get('data')
        print(f"错误: 获取专辑 (MID: {album_mid}) 详情失败。服务器响应: {response}")
        return None

    async def _embed_metadata(self, file_path: str, song_info: dict, lyric: str, tlyric: str):
        image_data = None
        if song_info.get('cover_url'):
            try:
                image_response = await self.client.get(song_info['cover_url'], timeout=30)
                if image_response.status_code == 200:
                    image_data = image_response.content
            except httpx.RequestError as e:
                print(f"下载封面时出错: {e}")
        
        await run_in_threadpool(self._write_tags_to_file, file_path, song_info, lyric, tlyric, image_data)

    def _write_tags_to_file(self, file_path, song_info, lyric, tlyric, image_data):
        """同步的元数据写入辅助函数。"""
        try:
            print(f"后台任务: 开始为 {os.path.basename(file_path)} 嵌入元数据...")
            song_name = song_info.get('name')
            artist_names = song_info.get('artist')
            album_name = song_info.get('album_name')
            album_id = song_info.get('album_mid')
            track_number = song_info.get('track_number')
            disc_number = song_info.get('disc_number')
            bpm = song_info.get('bpm')
            lyricist = song_info.get('lyricist')
            composer = song_info.get('composer')
            arranger = song_info.get('arranger')
            
            # 由于此函数在线程池中运行，album_cache的访问是线程安全的
            album_details = self.album_cache.get(album_id)
            if album_details is None and album_id not in self.album_cache:
                # 注意：这里不能用 await，所以我们需要一个同步版本的 _get_album_details_by_mid
                # 为了简单起见，我们暂时忽略缓存填充失败的情况
                pass

            genre = song_info.get('genre')
            if album_details:
                album_artist = album_details.get('singername', artist_names)
                publisher = album_details.get('company')
                if not genre:
                    genre = album_details.get('genre')
                publish_time_str = album_details.get('aDate')
            else:
                album_artist, publisher, publish_time_str = artist_names, None, None
            
            release_date_str, release_year_str = None, None
            if publish_time_str:
                try:
                    dt_object = datetime.datetime.strptime(publish_time_str, '%Y-%m-%d')
                    release_date_str = dt_object.strftime('%Y-%m-%d')
                    release_year_str = dt_object.strftime('%Y')
                except ValueError: pass

            full_lyric = f"{lyric}\n\n--- 翻译\n\n{tlyric}" if tlyric and lyric else lyric
            
            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                if audio.tags is None: audio.add_tags()
                
                audio.tags.add(TIT2(encoding=3, text=song_name))
                audio.tags.add(TPE1(encoding=3, text=artist_names))
                audio.tags.add(TALB(encoding=3, text=album_name))
                if album_artist: audio.tags.add(TPE2(encoding=3, text=album_artist))
                if track_number: audio.tags.add(TRCK(encoding=3, text=str(track_number)))
                if disc_number: audio.tags.add(TPOS(encoding=3, text=str(disc_number)))
                if genre: audio.tags.add(TCON(encoding=3, text=genre))
                if publisher: audio.tags.add(TPUB(encoding=3, text=publisher))
                if bpm and bpm > 0: audio.tags.add(TBPM(encoding=3, text=str(round(bpm))))
                if composer: audio.tags.add(TCOM(encoding=3, text=composer))
                if lyricist: audio.tags.add(TEXT(encoding=3, text=lyricist))
                if arranger: audio.tags.add(TPE4(encoding=3, text=arranger))
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
                if disc_number: audio['discnumber'] = str(disc_number)
                if genre: audio['genre'] = genre
                if publisher: audio['organization'] = publisher
                if bpm and bpm > 0: audio['bpm'] = str(round(bpm))
                if composer: audio['composer'] = composer
                if lyricist: audio['lyricist'] = lyricist
                if arranger: audio['arranger'] = arranger
                if release_date_str: audio['date'] = release_date_str
                if release_year_str: audio['year'] = release_year_str
                if full_lyric: audio['lyrics'] = full_lyric
                
                audio.clear_pictures()
                if image_data:
                    picture = Picture()
                    picture.type = 3; picture.mime = "image/jpeg"; picture.desc = "Cover"; picture.data = image_data
                    audio.add_picture(picture)
                audio.save()

            print(f"后台任务: 元数据嵌入成功 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生严重错误 - {e}")

    async def _download_and_process_single_version(self, search_key, quality, download_url, extension, song_info, lyric, tlyric):
        album_name = song_info.get('album_name', '')
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name)
        base_name = f"{search_key} {safe_album_name}" if safe_album_name else search_key
        filename_suffix = " [M]" if quality == 'master' else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)
        
        save_directory = self.music_directory
        if quality == 'flac':
            existing_qualities = await run_in_threadpool(self.local_api.get_existing_qualities, search_key=search_key, album=album_name)
            if 'master' in existing_qualities:
                save_directory = self.flac_directory

        file_path = os.path.join(save_directory, f"{safe_filename}{filename_suffix}{extension}")
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"后台任务: 开始下载 '{base_name}' ({quality}) (第 {attempt + 1} 次尝试)")
                async with self.client.stream("GET", download_url, timeout=300, headers=self.headers) as r:
                    r.raise_for_status()
                    with open(file_path, 'wb') as f:
                        async for chunk in r.aiter_bytes(chunk_size=8192):
                            f.write(chunk)
                
                print(f"后台任务: 下载成功 - {file_path}")
                await self._embed_metadata(file_path, song_info, lyric, tlyric)
                await run_in_threadpool(
                    self.local_api.add_song_to_db,
                    search_key=search_key, file_path=file_path,
                    duration=song_info.get('duration', 0), album=album_name, quality=quality
                )
                return True
            except httpx.RequestError as e:
                print(f"后台任务: 下载 '{search_key}' 失败 (第 {attempt + 1} 次尝试)，错误: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    self.logger.error(f"歌曲下载失败 - MID: {song_info.get('mid')}, 名称: '{search_key}', 音质: {quality}, 错误: {e}")
                    if os.path.exists(file_path): os.remove(file_path)
                    return False
        return False

    def _run_async_in_thread(self, async_func, *args, **kwargs):
        """同步包装器，在独立线程中运行异步函数。"""
        asyncio.run(async_func(*args, **kwargs))

    async def _background_download_task(self, song_info: dict, song_urls: dict, lyric: str, tlyric: str):
        """异步后台智能分层下载任务。"""
        album_name = song_info.get('album_name', '')
        artist_string = song_info.get('artist', '未知歌手')
        song_name = song_info.get('name', '未知歌曲')
        search_key = self.converter.convert(f"{artist_string} - {song_name}")
        
        existing_qualities = await run_in_threadpool(self.local_api.get_existing_qualities, search_key=search_key, album=album_name)
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        if 'master' in existing_qualities:
            print("后台任务: 已存在 master 版本，任务结束。"); return

        if 'flac' in existing_qualities and 'master' in song_urls:
            print("后台任务: 已存在 flac 版本，尝试补充 master 版本...")
            await self._download_and_process_single_version(search_key, 'master', song_urls['master'], '.flac', song_info, lyric, tlyric)
            return

        if '320' in existing_qualities or '128' in existing_qualities:
            print("后台任务: 已存在低品质版本，尝试补充 master 和 flac...")
            tasks = []
            if 'master' in song_urls:
                tasks.append(self._download_and_process_single_version(search_key, 'master', song_urls['master'], '.flac', song_info, lyric, tlyric))
            if 'flac' in song_urls:
                tasks.append(self._download_and_process_single_version(search_key, 'flac', song_urls['flac'], '.flac', song_info, lyric, tlyric))
            if tasks: await asyncio.gather(*tasks)
            return

        if not existing_qualities:
            print("后台任务: 本地库无此歌曲，开始智能下载...")
            downloaded_master = False
            if 'master' in song_urls:
                if await self._download_and_process_single_version(search_key, 'master', song_urls['master'], '.flac', song_info, lyric, tlyric):
                    downloaded_master = True

            if 'flac' in song_urls and (not downloaded_master or Config.ENABLE_FLAC_DOWNLOAD_WHEN_HAVE_MASTER):
                await self._download_and_process_single_version(search_key, 'flac', song_urls['flac'], '.flac', song_info, lyric, tlyric)

            elif not downloaded_master and 'flac' not in song_urls:
                print("后台任务: 未能下载任何高品质音源，开始降级查找...")
                for quality in ['320', '128']:
                    if song_urls.get(quality):
                        extension = self.file_config[quality]['e']
                        if await self._download_and_process_single_version(search_key, quality, song_urls[quality], extension, song_info, lyric, tlyric):
                            return

    async def get_song_info(self, song_mid):
        payload = {"comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"}, "req_1": {"module": "music.pf_song_detail_svr", "method": "get_song_detail", "param": {"song_mid": song_mid}}}
        song_data = await self._post_request(payload)
        if not song_data or song_data.get('code') != 0: return None
        data = song_data.get('req_1', {}).get('data', {})
        track_info = data.get('track_info', {})
        info_list = data.get('info', [])
        info_dict = {}
        for item in info_list:
            title = item.get('title')
            content = item.get('content')
            if title and content and len(content) > 0:
                info_dict[title] = ";".join([c.get('value', '') for c in content])
        index_cd = track_info.get('index_cd')
        disc_number = index_cd + 1 if isinstance(index_cd, int) else None
        return {
            "id": track_info.get('id'), "mid": track_info.get('mid'), "name": track_info.get('name'),
            "artist": "、".join([singer['name'] for singer in track_info.get('singer', [])]),
            "album_name": track_info.get('album', {}).get('name'), "album_mid": track_info.get('album', {}).get('mid'),
            "duration": track_info.get('interval', 0) * 1000,
            "cover_url": f"https://y.qq.com/music/photo_new/T002R800x800M000{track_info.get('album', {}).get('mid')}.jpg",
            "track_number": track_info.get('index_album'), "disc_number": disc_number, "bpm": track_info.get('bpm'),
            "lyricist": info_dict.get('作词'), "composer": info_dict.get('作曲'), "arranger": info_dict.get('编曲'),
            "genre": info_dict.get('歌曲流派'),
        }

    async def get_song_urls(self, song_mid):
        uin = Config.QQ_USER_CONFIG.get("uin", "0")
        guid = str(random.randint(1000000000, 9999999999))
        tasks = [self._fetch_single_url(song_mid, quality, guid, uin) for quality in self.file_config.keys()]
        results = await asyncio.gather(*tasks)
        return {quality: url for quality, url in results if url}
    
    async def get_lyrics(self, song_id):
        payload = {"comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"}, "req_1": {"module": "music.musichallSong.PlayLyricInfo", "method": "GetPlayLyricInfo", "param": {"songID": song_id, "trans": 1, "roma": 1}}}
        lyric_data = await self._post_request(payload)
        if not lyric_data or lyric_data.get('code') != 0: return "", ""
        lyric_info = lyric_data.get("req_1", {}).get("data", {})
        lyric = base64.b64decode(lyric_info.get('lyric', b'')).decode('utf-8', 'ignore')
        tlyric = base64.b64decode(lyric_info.get('trans', b'')).decode('utf-8', 'ignore')
        return lyric, tlyric

    async def get_song_details(self, song_mid: str = None, song_id: int = None):
        resolved_ids = await self._resolve_song_ids(song_mid=song_mid, song_id=song_id)
        if not resolved_ids or not resolved_ids.get('mid'):
            return {"error": f"无法解析到有效的歌曲信息。"}
        final_mid, final_id = resolved_ids.get('mid'), resolved_ids.get('id')

        info_task = self.get_song_info(final_mid)
        urls_task = self.get_song_urls(final_mid)
        lyric_task = self.get_lyrics(final_id) if final_id else asyncio.sleep(0, result=("", ""))
        
        info, urls, (lyric, tlyric) = await asyncio.gather(info_task, urls_task, lyric_task)
        if not info: return {"error": f"获取详细信息失败。"}
        
        if self.local_api and Config.DOWNLOADS_ENABLED:
            threading.Thread(target=self._run_async_in_thread, args=(self._background_download_task, info, urls, lyric, tlyric)).start()
        
        return {**info, "urls": urls, "lyric": lyric, "tlyric": tlyric}

    async def search_and_get_details(self, keyword: str, album: str = None):
        payload = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"},
            "req_1": {
                "method": "DoSearchForQQMusicDesktop",
                "module": "music.search.SearchCgiService",
                "param": {"num_per_page": 5, "page_num": 1, "query": keyword, "search_type": 0}
            }
        }
        search_results = await self._post_request(payload)
        if not search_results or search_results.get('code') != 0: return {"error": "搜索失败或无结果"}
        song_list = search_results.get('req_1', {}).get('data', {}).get('body', {}).get('song', {}).get('list', [])
        
        try:
            target_artist, target_song = [x.strip().lower() for x in keyword.split(' - ', 1)]
        except ValueError:
            return {"error": "关键词格式不正确..."}
        
        def find_exact_match(songs):
            for song in songs:
                result_song = song.get('name', '').lower()
                result_artist = " / ".join([s.get('name') for s in song.get('singer', [])]).lower()
                if target_song in result_song and target_artist in result_artist:
                    return song
            return None

        best_match_song = find_exact_match(song_list)
        if not best_match_song:
            return {"error": "未能找到精确匹配的歌曲"}
        return await self.get_song_details(song_mid=best_match_song.get('mid'), song_id=best_match_song.get('id'))

    async def get_playlist_info(self, playlist_id: str) -> dict:
        url = 'https://c.y.qq.com/v8/fcg-bin/fcg_v8_playlist_cp.fcg'
        params = {'id': playlist_id, 'tpl': 'wk', 'format': 'json', 'outCharset': 'utf-8'}
        response = await self._get_request(url, params=params)
        if not response or response.get('code', -1) != 0: return {"error": "获取QQ音乐歌单详情失败。"}
        cdlist = response.get('data', {}).get('cdlist', [])
        if not cdlist: return {"error": "未在响应中找到歌单数据。"}
        playlist_data = cdlist[0]
        playlist_name = playlist_data.get('dissname', '未知歌单')
        song_list = playlist_data.get('songlist', [])
        songs = [{'name': s.get('songname'), 'artist': '、'.join([i.get('name') for i in s.get('singer', [])]), 'mid': s.get('songmid')} for s in song_list]
        return {"playlist_name": playlist_name, "songs": songs}

    async def download_playlist_by_id(self, playlist_id: str, level: str):
        url = 'https://c.y.qq.com/v8/fcg-bin/fcg_v8_playlist_cp.fcg'
        params = {'id': playlist_id, 'tpl': 'wk', 'format': 'json', 'outCharset': 'utf-8', 'new_format': 1, 'platform': 'mac'}
        response = await self._get_request(url, params=params)
        if not response or response.get('code', -1) != 0: print(f"错误: 获取歌单详情失败。"); return
        cdlist = response.get('data', {}).get('cdlist', [])
        if not cdlist: print(f"歌单中没有找到任何歌曲。"); return
        playlist_data = cdlist[0]
        playlist_name = playlist_data.get('dissname', '未知歌单')
        song_ids_str = playlist_data.get('songids')
        if not song_ids_str: print(f"歌单中没有找到任何歌曲。"); return
        song_id_list = song_ids_str.split(',')
        total_songs = len(song_id_list)
        print(f"开始处理歌单 '{playlist_name}'，共 {total_songs} 首歌曲。")
        for i, song_id_str in enumerate(song_id_list):
            try:
                song_id = int(song_id_str)
                print(f"  -> 正在将第 {i+1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列...")
                await self.get_song_details(song_id=song_id)
                await asyncio.sleep(1)
            except (ValueError, TypeError): continue
        print(f"歌单 '{playlist_name}' 处理完毕。")

    async def download_album_by_id(self, album_mid: str, level: str):
        url = 'https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_info_cp.fcg'
        params = {'albummid': album_mid, 'format': 'json', 'outCharset': 'utf-8'}
        response = await self._get_request(url, params=params)
        if not response or response.get('code', -1) != 0: print(f"错误: 获取专辑详情失败。"); return
        song_list = response.get('data', {}).get('list', [])
        album_name = response.get('data', {}).get('name', '未知专辑')
        total_songs = len(song_list)
        if total_songs == 0: print(f"专辑中没有找到任何歌曲。"); return
        print(f"开始处理专辑 '{album_name}'，共 {total_songs} 首歌曲。")
        for i, song in enumerate(song_list):
            await self.get_song_details(song_mid=song.get('songmid'), song_id=song.get('songid'))
            await asyncio.sleep(1)
        print(f"专辑 '{album_name}' 处理完毕。")

    def start_background_playlist_download(self, playlist_id: str, level: str):
        threading.Thread(target=self._run_async_in_thread, args=(self.download_playlist_by_id, playlist_id, level)).start()
        print(f"已为歌单 {playlist_id} 启动后台下载线程。")

    def start_background_album_download(self, album_mid: str, level: str):
        threading.Thread(target=self._run_async_in_thread, args=(self.download_album_by_id, album_mid, level)).start()
        print(f"已为专辑 {album_mid} 启动后台下载线程。")

