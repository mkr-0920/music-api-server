import os
import requests
import base64
import time
import re
import json
from opencc import OpenCC
import threading
from mutagen.mp3 import MP3, EasyMP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, TALB
from mutagen.flac import FLAC, Picture
from utils.helpers import Utils
# 新增: 直接从 core.config 导入 Config 类，以获取动态更新的 Cookie
from core.config import Config

class QQMusicAPI:
    # 修改: __init__ 不再需要接收 cookie_dict 参数
    def __init__(self, local_api_instance, music_directory: str):
        # self.cookies = cookie_dict  <-- 移除这一行
        self.local_api = local_api_instance
        self.music_directory = music_directory
        self.converter = OpenCC('t2s')
        self.base_url = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Referer': 'https://y.qq.com/',
            'Origin': 'https://y.qq.com/',
        }
        self.file_config = {
            '128': {'s': 'M500', 'e': '.mp3'},
            '320': {'s': 'M800', 'e': '.mp3'},
            'flac': {'s': 'F000', 'e': '.flac'},
            'master': {'s': 'AI00', 'e': '.flac'},
        }

    def _get_request(self, url):
        """通用的GET请求函数"""
        try:
            # 修改: 直接使用 Config.QQ_USER_CONFIG 获取最新的 Cookie
            response = requests.get(url, headers=self.headers, cookies=Config.QQ_USER_CONFIG)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"QQ音乐GET请求出错: {e}")
            return None

    def _post_request(self, url, json_data):
        """通用的POST请求函数"""
        try:
            # 修改: 直接使用 Config.QQ_USER_CONFIG 获取最新的 Cookie
            response = requests.post(url, json=json_data, headers=self.headers, cookies=Config.QQ_USER_CONFIG)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"QQ音乐POST请求出错: {e}")
            return None

    def _embed_metadata(self, file_path: str, song_info: dict, lyric: str, tlyric: str):
        try:
            print(f"后台任务: 开始为 {os.path.basename(file_path)} 嵌入元数据...")
            song_name = song_info.get('name')
            album_name = song_info.get('album')
            artist_names = ";".join([singer['name'] for singer in song_info.get('raw_artists', [])])
            image_data = None
            if song_info.get('cover_url'):
                try:
                    image_response = requests.get(song_info['cover_url'], timeout=30)
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
        album_name = song_info.get('album', {})
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
        base_name = search_key
        if safe_album_name:
            base_name = f"{search_key} {safe_album_name}"
        filename_suffix = " [M]" if quality == 'master' else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)
        file_path = os.path.join(self.music_directory, f"{safe_filename}{filename_suffix}{extension}")
        try:
            print(f"后台任务: 开始下载 '{base_name}' ({quality}) 到 {file_path}")
            with requests.get(download_url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(file_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            print(f"后台任务: 下载成功 - {file_path}")
            self._embed_metadata(file_path, song_info, lyric, tlyric)
            self.local_api.add_song_to_db(
                search_key=search_key, file_path=file_path,
                duration=song_info.get('duration', 0), album=song_info.get('album'),
                quality=quality
            )
            return True
        except requests.RequestException as e:
            print(f"后台任务: 下载失败 - {e}")
            if os.path.exists(file_path): os.remove(file_path)
            return False

    def _download_and_save_song(self, song_info: dict, song_urls: dict, lyric: str, tlyric: str):
        album_name = song_info.get('album', {})
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
        artist_string = "、".join([singer['name'] for singer in song_info['raw_artists']])
        song_name = song_info['name']
        search_key = self.converter.convert(f"{artist_string} - {song_name}")
        base_name = search_key
        if safe_album_name:
            base_name = f"{search_key} {safe_album_name}"
        
        existing_qualities = self.local_api.get_existing_qualities(search_key, album_name)
        print(f"后台任务: 本地库中 '{base_name}' 已有音质: {existing_qualities}")

        if 'master' in existing_qualities:
            print(f"后台任务: 已存在 master 版本，任务结束。")
            return

        if 'flac' in existing_qualities:
            print(f"后台任务: 已存在 flac 版本，尝试补充 master 版本...")
            if song_urls.get('master'):
                self._download_and_process_single_version(search_key, 'master', song_urls['master'], '.flac', song_info, lyric, tlyric)
            return

        if '320' in existing_qualities or '128' in existing_qualities:
            print(f"后台任务: 已存在低品质版本，尝试补充 master 和 flac...")
            for quality in ['master', 'flac']:
                if song_urls.get(quality):
                    self._download_and_process_single_version(search_key, quality, song_urls[quality], '.flac', song_info, lyric, tlyric)
            return

        if not existing_qualities:
            print(f"后台任务: 本地库无此歌曲，开始智能下载...")
            downloaded_high_quality = False
            for quality in ['master', 'flac']:
                if song_urls.get(quality):
                    if self._download_and_process_single_version(search_key, quality, song_urls[quality], '.flac', song_info, lyric, tlyric):
                        downloaded_high_quality = True
            
            if not downloaded_high_quality:
                print(f"后台任务: 未能下载任何高品质音源，开始降级查找...")
                fallback_qualities = ['320', '128']
                for quality in fallback_qualities:
                    if song_urls.get(quality):
                        extension = self.file_config[quality]['e']
                        if self._download_and_process_single_version(search_key, quality, song_urls[quality], extension, song_info, lyric, tlyric):
                            return
    
    def search_song(self, keyword: str, album: str = None, limit: int = 5):
        payload = {
            "search": {
                "method": "DoSearchForQQMusicDesktop",
                "module": "music.search.SearchCgiService",
                "param": { "num_per_page": limit, "page_num": 1, "query": keyword, "search_type": 0, },
            },
        }
        search_url = f"{self.base_url}?data={json.dumps(payload)}"
        search_results = self._get_request(search_url)

        if not search_results or search_results.get('code') != 0: return []
        
        song_list = search_results.get('search', {}).get('data', {}).get('body', {}).get('song', {}).get('list', [])
        
        songs_to_process = []
        if album:
            for song in song_list:
                song_album = song.get('album', {}).get('name')
                if song_album and album.lower() in song_album.lower():
                    songs_to_process.append(song)
        else:
            songs_to_process = song_list

        formatted_results = []
        for song in songs_to_process:
            formatted_results.append({
                "mid": song.get('mid'), "name": song.get('name'),
                "duration": song.get('interval', 0) * 1000,
                "album": song.get('album', {}).get('name'),
                "artist": " / ".join([s.get('name') for s in song.get('singer', []) if s.get('name')]),
            })
        return formatted_results

    def get_song_info(self, song_mid):
        data = { "comm": {"cv": 4747474, "ct": 24, "format": "json", "platform": "yqq.json"}, "detail": {"module": "music.pf_song_detail_svr", "method": "get_song_detail", "param": {"song_mid": song_mid}}}
        song_data = self._post_request(self.base_url, data)
        if not song_data or song_data.get('code') != 0: return None
        info = song_data['detail']['data']['track_info']
        return {
            "id": info['id'], "mid": info['mid'], "name": info['name'],
            "artist": "、".join([singer['name'] for singer in info['singer']]),
            "raw_artists": info['singer'], "album": info['album']['name'],
            "duration": info.get('interval', 0) * 1000,
            "cover_url": f"https://y.qq.com/music/photo_new/T002R800x800M000{info['album']['mid']}.jpg"
        }

    def get_song_urls(self, song_mid):
        urls = {}
        for quality, config in self.file_config.items():
            filename = f"{config['s']}{song_mid}{song_mid}{config['e']}"
            # 修改: 直接从 Config.QQ_USER_CONFIG 获取 uin
            uin = Config.QQ_USER_CONFIG.get("uin", "0")
            data = {"req_1": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey", "param": {"guid": "10000", "songmid": [song_mid], "filename": [filename], "uin": uin, "platform": "20"}}}
            vkey_data = self._post_request(self.base_url, data)
            if vkey_data and vkey_data.get('req_1', {}).get('data', {}).get('midurlinfo', [{}])[0].get('purl'):
                purl = vkey_data['req_1']['data']['midurlinfo'][0]['purl']
                domain = vkey_data['req_1']['data']['sip'][0] if vkey_data['req_1']['data']['sip'] else 'https://isure.stream.qqmusic.qq.com/'
                urls[quality] = domain + purl
            time.sleep(0.1)
        return urls
    
    def get_lyrics(self, song_id):
        data = {"comm": {"cv": 80600, "ct": 6}, "music.musichallSong.PlayLyricInfo.GetPlayLyricInfo": {"module": "music.musichallSong.PlayLyricInfo", "method": "GetPlayLyricInfo", "param": {"songID": song_id, "trans": 1, "roma": 1}}}
        lyric_data = self._post_request(self.base_url, data)
        if not lyric_data or lyric_data.get('code') != 0: return "", ""
        lyric_info = lyric_data.get("music.musichallSong.PlayLyricInfo.GetPlayLyricInfo", {}).get("data", {})
        lyric = base64.b64decode(lyric_info.get('lyric', b'')).decode('utf-8')
        tlyric = base64.b64decode(lyric_info.get('trans', b'')).decode('utf-8')
        return lyric, tlyric

    def get_song_details(self, song_mid):
        info = self.get_song_info(song_mid)
        if not info: return {"error": "获取歌曲信息失败"}
        urls = self.get_song_urls(song_mid)
        lyric, tlyric = self.get_lyrics(info['id'])
        if self.local_api:
            threading.Thread(target=self._download_and_save_song, args=(info, urls, lyric, tlyric)).start()
        if 'raw_artists' in info:
            del info['raw_artists']
        return {**info, "urls": urls, "lyric": lyric, "tlyric": tlyric}

    def search_and_get_details(self, keyword: str, album: str = None):
        try:
            target_artist, target_song = [x.strip().lower() for x in keyword.split(' - ', 1)]
        except ValueError:
            return {"error": "关键词格式不正确，请使用 '歌手 - 歌曲名' 的格式。"}
        
        def find_exact_match(results):
            for song in results:
                result_artist = song.get('artist', '').lower()
                result_song = song.get('name', '').lower()
                if target_song == result_song and target_artist in result_artist:
                    return song.get('mid')
            return None

        search_results = self.search_song(keyword, album=album, limit=5)
        best_match_mid = find_exact_match(search_results)
        
        if not best_match_mid and album:
            print(f"未能从专辑 '{album}' 中找到精确匹配，尝试在所有专辑中搜索...")
            search_results = self.search_song(keyword, album=None, limit=5)
            best_match_mid = find_exact_match(search_results)

        if not best_match_mid:
            return {"error": "未能找到精确匹配的歌曲"}
            
        return self.get_song_details(best_match_mid)

