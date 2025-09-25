# 文件路径: api/qq.py

import base64
import time
import requests

# 从我们自己的工具模块中导入Utils类
from utils.helpers import Utils

class QQMusicAPI:
    def __init__(self, cookie_dict: dict):
        self.cookies = cookie_dict
        self.base_url = 'https://u.y.qq.com/cgi-bin/musicu.fcg'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
            'Referer': 'https://y.qq.com/',
        }
        self.file_config = {
            '128': {'s': 'M500', 'e': '.mp3', 'bitrate': '128kbps'},
            '320': {'s': 'M800', 'e': '.mp3', 'bitrate': '320kbps'},
            'flac': {'s': 'F000', 'e': '.flac', 'bitrate': 'FLAC'},
            'master': {'s': 'AI00', 'e': '.flac', 'bitrate': 'Hi-Res'},
        }

    def _post_request(self, url, json_data):
        try:
            response = requests.post(url, json=json_data, headers=self.headers, cookies=self.cookies)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"QQ音乐请求出错: {e}")
            return None

    def get_song_info(self, song_mid):
        """获取歌曲元数据，如歌名、歌手、专辑。"""
        data = {
            "comm": {"cv": 4747474, "ct": 24, "format": "json", "inCharset": "utf-8", "outCharset": "utf-8", "notice": 0, "platform": "yqq.json", "needNewCode": 1},
            "detail": {"module": "music.pf_song_detail_svr", "method": "get_song_detail", "param": {"song_mid": song_mid}}
        }
        song_data = self._post_request(self.base_url, data)
        if not song_data or song_data.get('code') != 0:
            return None
        
        info = song_data['detail']['data']['track_info']
        return {
            "id": info['id'],
            "mid": info['mid'],
            "name": info['name'],
            "artist": "/".join([singer['name'] for singer in info['singer']]),
            "album": info['album']['name'],
            "cover_url": f"https://y.qq.com/music/photo_new/T002R800x800M000{info['album']['mid']}.jpg"
        }

    def get_song_urls(self, song_mid):
        """获取不同音质的URL。"""
        urls = {}
        for quality, config in self.file_config.items():
            filename = f"{config['s']}{song_mid}{song_mid}{config['e']}"
            data = {
                "req_1": {
                    "module": "vkey.GetVkeyServer",
                    "method": "CgiGetVkey",
                    "param": {"guid": "10000", "songmid": [song_mid], "filename": [filename], "uin": self.cookies.get("uin", "0"), "platform": "20"}
                }
            }
            vkey_data = self._post_request(self.base_url, data)
            if vkey_data and vkey_data['req_1']['data']['midurlinfo'][0]['purl']:
                purl = vkey_data['req_1']['data']['midurlinfo'][0]['purl']
                domain = vkey_data['req_1']['data']['sip'][0]
                urls[quality] = domain + purl
            time.sleep(0.1) # 对服务器友好一点
        return urls

    def get_lyrics(self, song_id):
        """获取歌词和翻译歌词。"""
        data = {
            "comm": {"cv": 80600, "ct": 6},
            "music.musichallSong.PlayLyricInfo.GetPlayLyricInfo": {
                "module": "music.musichallSong.PlayLyricInfo",
                "method": "GetPlayLyricInfo",
                "param": {"songID": song_id, "trans": 1, "roma": 1}
            }
        }
        lyric_data = self._post_request(self.base_url, data)
        if not lyric_data or lyric_data.get('code') != 0:
            return "", ""
        
        lyric_info = lyric_data["music.musichallSong.PlayLyricInfo.GetPlayLyricInfo"]["data"]
        lyric = base64.b64decode(lyric_info.get('lyric', b'')).decode('utf-8')
        tlyric = base64.b64decode(lyric_info.get('trans', b'')).decode('utf-8')
        return lyric, tlyric

    def get_song_details(self, song_mid):
        info = self.get_song_info(song_mid)
        if not info:
            return {"error": "获取歌曲信息失败，该歌曲可能不存在或是VIP专享。"}
        
        urls = self.get_song_urls(song_mid)
        lyric, tlyric = self.get_lyrics(info['id'])

        return {
            **info,
            "urls": urls,
            "lyric": lyric,
            "tlyric": tlyric
        }