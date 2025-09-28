import requests
import re
from urllib.parse import quote
from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

class KuwoMusicAPI:
    def __init__(self):
        # --- 这是最终的、正确的修正 ---
        # 使用从 kwDES.js 文件中得到的、完全正确的8字节密钥
        self.DES_KEY = b'ylzsxkwm'
        # --- 修正结束 ---
        self.headers = {
            'User-Agent': 'okhttp/3.10.0'
        }

    def _des_encrypt(self, text: str) -> str:
        """
        复现酷我API的DES加密过程。
        """
        cipher = DES.new(self.DES_KEY, DES.MODE_ECB)
        padded_text = pad(text.encode('utf-8'), DES.block_size)
        encrypted_text = cipher.encrypt(padded_text)
        return encrypted_text.hex()

    def search(self, keyword: str, limit: int = 10) -> list:
        """
        搜索歌曲。
        """
        search_url = f"http://search.kuwo.cn/r.s?&correct=1&vipver=1&stype=comprehensive&encoding=utf8&rformat=json&mobi=1&show_copyright_off=1&searchapi=6&all={quote(keyword)}&rn={limit}"
        
        try:
            response = requests.get(search_url, timeout=10)
            response.raise_for_status()
            json_data = response.json()
            
            # 确保路径存在
            if 'content' not in json_data or len(json_data['content']) < 2 or 'musicpage' not in json_data['content'][1] or 'abslist' not in json_data['content'][1]['musicpage']:
                 return []
            
            song_list_raw = json_data['content'][1]['musicpage']['abslist']
            
            formatted_list = []
            for song in song_list_raw:
                formatted_list.append({
                    'id': song.get('MUSICRID', '').split('_').pop(),
                    'name': song.get('SONGNAME', '未知歌曲'),
                    'artist': song.get('ARTIST', '未知艺术家').replace('&', '/'),
                    'album': song.get('ALBUM', '未知专辑'),
                    'duration': int(song.get('DURATION', 0)) * 1000,
                })
            return formatted_list
        except (requests.RequestException, KeyError, IndexError) as e:
            print(f"酷我搜索失败: {e}")
            return []

    def _get_track_url(self, song_id: str) -> str | None:
        """
        获取歌曲的播放链接，优先使用加密接口。
        """
        params_str = f"corp=kuwo&source=kwplayer_ar_5.1.0.0_B_jiakong_vh.apk&p2p=1&type=convert_url2&sig=0&format=flac|mp3|wma&rid={song_id}"
        encrypted_query = self._des_encrypt(params_str)
        encrypted_url = f"http://mobi.kuwo.cn/mobi.s?f=kuwo&q={encrypted_query}"
        
        try:
            response = requests.get(encrypted_url, headers=self.headers, timeout=10)
            response.raise_for_status()
            match = re.search(r'http[^\s$"]+', response.text)
            if match:
                return match.group(0)
        except requests.RequestException as e:
            print(f"酷我加密接口请求失败: {e}")

        fallback_url = f"http://antiserver.kuwo.cn/anti.s?type=convert_url&format=mp3&response=url&rid=MUSIC_{song_id}"
        try:
            response = requests.get(fallback_url, timeout=10)
            response.raise_for_status()
            if response.text and response.text.startswith('http'):
                return response.text
        except requests.RequestException as e:
            print(f"酷我后备接口请求失败: {e}")
            
        return None

    def get_song_details(self, keyword: str) -> dict | None:
        """
        公开方法：先搜索，然后获取第一首匹配歌曲的详情和URL。
        """
        search_results = self.search(keyword)
        if not search_results:
            return {"error": "未搜索到相关歌曲。"}
            
        first_song = search_results[0]
        # 确保 first_song 中有 'id'
        if not first_song.get('id'):
            return {"error": "搜索结果无效，缺少歌曲ID。"}
            
        song_url = self._get_track_url(first_song['id'])
        
        if not song_url:
            return {"error": "获取歌曲播放链接失败。"}
            
        first_song['url'] = song_url
        return first_song

