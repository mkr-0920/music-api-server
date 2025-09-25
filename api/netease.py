# 文件路径: api/netease.py

import json
import urllib.parse
from hashlib import md5
from random import randrange
import requests

# For NetEase Music Encryption
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# 从我们自己的工具模块中导入Utils类
from utils.helpers import Utils

class NeteaseMusicAPI:
    def __init__(self, cookie_str: str):
        self.cookies = Utils.parse_cookie_str(cookie_str)
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/2.10.2.200154',
            'Referer': '',
            'os': 'pc',
            'appver': '',
            'osver': '',
            'deviceId': 'pyncm!'
        }
        self.AES_KEY = b"e82ckenh8dichen8"

    def _hex_digest(self, data):
        return "".join([hex(d)[2:].zfill(2) for d in data])

    def _md5_hex_digest(self, text):
        return md5(text.encode("utf-8")).hexdigest()

    def _eapi_encrypt(self, url, payload):
        url_path = urllib.parse.urlparse(url).path.replace("/eapi/", "/api/")
        payload_str = json.dumps(payload)
        digest = self._md5_hex_digest(f"nobody{url_path}use{payload_str}md5forencrypt")
        params_str = f"{url_path}-36cd479b6b5-{payload_str}-36cd479b6b5-{digest}"
        
        padder = padding.PKCS7(algorithms.AES(self.AES_KEY).block_size).padder()
        padded_data = padder.update(params_str.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(self.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return {'params': self._hex_digest(encrypted_data).upper()}

    def _post_request(self, url, data):
        try:
            response = requests.post(url, headers=self.headers, cookies=self.cookies, data=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"网易云请求出错: {e}")
            return None

    def get_song_details(self, song_id, level):
        # 1. Get song URL
        url_api = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
        payload = {
            'ids': [song_id],
            'level': level,
            'encodeType': 'flac',
            'header': json.dumps({"os": "pc", "appver": "", "osver": "", "deviceId": "pyncm!", "requestId": str(randrange(20000000, 30000000))}),
        }
        if level == 'sky':
            payload['immerseType'] = 'c51'
        encrypted_params = self._eapi_encrypt(url_api, payload)
        url_data = self._post_request(url_api, encrypted_params)

        if not url_data or not url_data.get('data') or not url_data['data'][0].get('url'):
            return {"error": "获取歌曲URL失败，可能是VIP歌曲或ID无效。"}

        song_info = url_data['data'][0]
        
        # 2. Get song name and artist info
        detail_api = "https://interface3.music.163.com/api/v3/song/detail"
        detail_data = self._post_request(detail_api, {'c': json.dumps([{"id": song_id, "v": 0}])})
        
        if not detail_data or not detail_data.get('songs'):
            return {"error": "获取歌曲元数据失败。"}
        
        meta_info = detail_data['songs'][0]

        # 3. Get lyrics
        lyric_api = "https://interface3.music.163.com/api/song/lyric"
        lyric_data = self._post_request(lyric_api, {'id': song_id, 'lv': -1, 'kv': -1, 'tv': -1})

        # 4. Format the final output
        formatted_data = {
            "name": meta_info['name'],
            "artist": "/".join([artist['name'] for artist in meta_info['ar']]),
            "album": meta_info['al']['name'],
            "cover_url": meta_info['al']['picUrl'],
            "quality": song_info.get('level'),
            "size": Utils.format_size(song_info.get('size')),
            "url": song_info['url'].replace("http://", "https://"),
            "lyric": lyric_data.get('lrc', {}).get('lyric'),
            "tlyric": lyric_data.get('tlyric', {}).get('lyric')
        }
        return formatted_data