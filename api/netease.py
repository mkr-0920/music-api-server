import os
import requests
import json
import re
from opencc import OpenCC
import threading
import urllib.parse
from hashlib import md5
from random import randrange

# 导入mutagen库
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, TALB
from mutagen.flac import FLAC, Picture

# For NetEase Music Encryption
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# 从我们自己的工具模块中导入Utils类
from utils.helpers import Utils

class APIConstants:
    """API相关常量"""
    AES_KEY = b"e82ckenh8dichen8"
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/8.9.75'
    DEFAULT_CONFIG = {"os": "pc", "appver": "8.9.75", "osver": "", "deviceId": "pyncm!"}
    SONG_URL_V1 = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
    SONG_DETAIL_V3 = "https://interface3.music.163.com/api/v3/song/detail"
    LYRIC_API = "https://interface3.music.163.com/api/song/lyric"
    SEARCH_API = "https://interface.music.163.com/eapi/cloudsearch/pc"

class NeteaseMusicAPI:
    def __init__(self, cookie_str: str, local_api_instance, music_directory: str):
        self.cookies = Utils.parse_cookie_str(cookie_str)
        self.local_api = local_api_instance
        self.music_directory = music_directory
        self.converter = OpenCC('t2s')
        self.headers = {'User-Agent': APIConstants.USER_AGENT}
        self.quality_map = {
            "standard": "128", "exhigh": "320", "lossless": "flac",
            "hires": "hires", "jyeffect": "jyeffect", "sky": "sky",
            "jymaster": "master"
        }

    def _eapi_encrypt(self, url_path: str, payload: dict) -> dict:
        digest = md5(f"nobody{url_path}use{json.dumps(payload)}md5forencrypt".encode('utf-8')).hexdigest()
        params_str = f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
        padder = padding.PKCS7(algorithms.AES(APIConstants.AES_KEY).block_size).padder()
        padded_data = padder.update(params_str.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(APIConstants.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return {'params': encrypted_data.hex().upper()}

    def _post_request(self, url: str, data: dict, is_eapi=False):
        try:
            if is_eapi:
                url_path = urllib.parse.urlparse(url).path.replace("/eapi/", "/api/")
                data = self._eapi_encrypt(url_path, data)
            response = requests.post(url, headers=self.headers, cookies=self.cookies, data=data, timeout=20)
            response.raise_for_status()
            if not response.text: return None
            return response.json()
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"网易云请求或JSON解析出错: {e}")
            return None

    def _get_song_url_data(self, song_id: str, level: str, meta_info: dict):
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
        return self._post_request(APIConstants.SONG_URL_V1, payload, is_eapi=True)
        
    def _get_song_metadata(self, song_id: str):
        data = {'c': json.dumps([{"id": song_id, "v": 0}])}
        return self._post_request(APIConstants.SONG_DETAIL_V3, data)
        
    def _get_lyric_data(self, song_id: str):
        data = {'id': song_id, 'lv': -1, 'kv': -1, 'tv': -1}
        return self._post_request(APIConstants.LYRIC_API, data)
    
    def _embed_metadata(self, file_path: str, song_info: dict, lyric: str, tlyric: str):
        try:
            song_name = song_info.get('name')
            album_name = song_info.get('al', {}).get('name')
            artist_names = ";".join([artist['name'] for artist in song_info.get('ar', [])])
            image_data = None
            if song_info.get('al', {}).get('picUrl'):
                try:
                    image_response = requests.get(song_info['al']['picUrl'], timeout=30)
                    if image_response.status_code == 200: image_data = image_response.content
                except requests.RequestException: pass
            full_lyric = f"{lyric}\n\n--- 翻译 ---\n\n{tlyric}" if tlyric else lyric
            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                if audio.tags is None: audio.add_tags()
                if song_name: audio.tags.add(TIT2(encoding=3, text=song_name))
                if artist_names: audio.tags.add(TPE1(encoding=3, text=artist_names))
                if album_name: audio.tags.add(TALB(encoding=3, text=album_name))
                if image_data: audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=image_data))
                if full_lyric: audio.tags.add(USLT(encoding=3, text=full_lyric))
                audio.save()
            elif file_path.lower().endswith('.flac'):
                audio = FLAC(file_path)
                if song_name: audio['title'] = song_name
                if artist_names: audio['artist'] = artist_names
                if album_name: audio['album'] = album_name
                if image_data:
                    picture = Picture()
                    picture.type = 3; picture.mime = "image/jpeg"; picture.desc = "Cover"; picture.data = image_data
                    audio.add_picture(picture)
                if full_lyric: audio['lyrics'] = full_lyric
                audio.save()
            print(f"后台任务: 元数据嵌入成功 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生错误 - {e}")

    def _download_and_process_single_version(self, search_key, quality, download_url, extension, song_info, lyric, tlyric):
        album_name = song_info.get('al', {}).get('name', '')
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
        base_name = search_key
        if safe_album_name:
            base_name = f"{search_key} {safe_album_name}"
        filename_suffix = " [M]" if quality == 'master' else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)
        file_path = os.path.join(self.music_directory, f"{safe_filename}{filename_suffix}{extension}")
        try:
            print(f"后台任务: 开始下载 '{search_key}' ({quality}) 到 {file_path}")
            with requests.get(download_url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            print(f"后台任务: 下载成功 - {file_path}")
            self._embed_metadata(file_path, song_info, lyric, tlyric)
            self.local_api.add_song_to_db(
                search_key=search_key, file_path=file_path,
                duration=song_info.get('dt', 0), album=song_info.get('al', {}).get('name'),
                quality=quality
            )
            return True
        except requests.RequestException as e:
            print(f"后台任务: 下载失败 - {e}")
            if os.path.exists(file_path): os.remove(file_path)
            return False

    def _background_download_task(self, song_id: str, meta_info: dict, lyric: str, tlyric: str):
        """(后台线程) 智能分层下载，并自动写入数据库。"""
        artist_string = "、".join([artist['name'] for artist in meta_info['ar']])
        song_name = meta_info['name']
        search_key = self.converter.convert(f"{artist_string} - {song_name}")

        # 1. 从数据库获取该歌曲所有音质
        album_name = meta_info.get('al', {}).get('name', '') if meta_info else ''
        existing_qualities = self.local_api.get_existing_qualities(search_key, album_name)
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        # 2. 场景一: 如果有master则跳过下载
        if 'master' in existing_qualities:
            print(f"后台任务: 已存在 master 版本，任务结束。")
            return

        # 3. 场景二: 没有master有flac，尝试下载jymaster
        if 'flac' in existing_qualities:
            print(f"后台任务: 已存在 flac 版本，尝试补充 jymaster 版本...")
            url_info_data = self._get_song_url_data(song_id, 'jymaster', meta_info)
            if url_info_data and url_info_data.get('data'):
                song_info_url = url_info_data['data'][0]
                # 严格检查返回的是否是 jymaster
                if song_info_url.get('url') and song_info_url.get('level') == 'jymaster':
                    self._download_and_process_single_version(
                        search_key, 'master', song_info_url['url'], '.flac', meta_info, lyric, tlyric
                    )
            return # 无论是否成功，任务都结束

        # 4. 场景三: 有320k或128k，尝试下载jymaster和lossless
        if '320' in existing_qualities or '128' in existing_qualities:
            print(f"后台任务: 已存在低品质版本，尝试补充 jymaster 和 lossless...")
            for level in ['jymaster', 'lossless']:
                url_info_data = self._get_song_url_data(song_id, level, meta_info)
                if url_info_data and url_info_data.get('data'):
                    song_info_url = url_info_data['data'][0]
                    if song_info_url.get('url') and song_info_url.get('level') == level:
                        db_quality = self.quality_map.get(level)
                        self._download_and_process_single_version(
                            search_key, db_quality, song_info_url['url'], '.flac', meta_info, lyric, tlyric
                        )
            return

        # 5. 场景四: 如果完全没有这首歌
        if not existing_qualities:
            print(f"后台任务: 本地库无此歌曲，开始智能下载...")
            # 先请求jymaster
            url_info_data = self._get_song_url_data(song_id, 'jymaster', meta_info)
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
                self._download_and_process_single_version(search_key, 'master', download_url, extension, meta_info, lyric, tlyric)
                # 然后再请求lossless下载
                print(f"后台任务: 已下载 master, 继续请求 lossless...")
                lossless_data = self._get_song_url_data(song_id, 'lossless', meta_info)
                if lossless_data and lossless_data.get('data'):
                    lossless_info = lossless_data['data'][0]
                    if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                         self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)
            
            # 如果返回的是exhigh或standard
            elif actual_level in ['exhigh', 'standard']:
                print("只有低品质版本...")
                self._download_and_process_single_version(search_key, db_quality, download_url, extension, meta_info, lyric, tlyric)

            # 如果返回的不是上面几种情况 (例如返回了lossless)
            else:
                # 请求lossless下载
                print(f"后台任务: 请求 jymaster 返回了 {actual_level}，现在请求 lossless...")
                lossless_data = self._get_song_url_data(song_id, 'lossless', meta_info)
                if lossless_data and lossless_data.get('data'):
                    lossless_info = lossless_data['data'][0]
                    if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                         self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)

    def search_song(self, keyword: str, album: str = None, limit: int = 10):
        """
        根据关键词搜索歌曲，并可选地根据专辑名进行过滤。
        """
        payload = {'s': keyword, 'type': 1, 'limit': limit, 'offset': 0}
        search_data = self._post_request(APIConstants.SEARCH_API, payload, is_eapi=True)

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

    def get_song_details(self, song_id, level):
        """
        (已有函数) 获取歌曲完整信息，并触发后台下载。
        """
        meta_data = self._get_song_metadata(song_id)
        if not meta_data or not meta_data.get('songs'): return {"error": "获取歌曲元数据失败。"}
        meta_info = meta_data['songs'][0]

        lyric_data = self._get_lyric_data(song_id)
        lyric = lyric_data.get('lrc', {}).get('lyric', '') if lyric_data else ''
        tlyric = lyric_data.get('tlyric', {}).get('lyric', '') if lyric_data else ''

        if self.local_api:
            threading.Thread(target=self._background_download_task, args=(song_id, meta_info, lyric, tlyric)).start()

        url_data = self._get_song_url_data(song_id, level, meta_info)
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
    

    def search_and_get_details(self, keyword: str, level: str, album: str = None):
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

        search_results = self.search_song(keyword, album=album, limit=5)
        best_match_id = find_exact_match(search_results)
        
        if not best_match_id and album:
            print(f"未能从专辑 '{album}' 中找到精确匹配，尝试在所有专辑中搜索...")
            search_results = self.search_song(keyword, album=None, limit=5)
            best_match_id = find_exact_match(search_results)

        if not best_match_id:
            return {"error": "未能找到精确匹配的歌曲"}
            
        return self.get_song_details(best_match_id, level)
