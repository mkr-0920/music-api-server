import httpx
import json

class NavidromeAPI:
    """一个功能完整的、异步的Navidrome API客户端"""
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        # 使用异步客户端
        self.client = httpx.AsyncClient(timeout=15.0)
        self.is_logged_in = False
        self.subsonic_token = None
        self.subsonic_salt = None

    async def login(self):
        """异步登录Navidrome并设置认证头。"""
        print("正在异步登录 Navidrome...")
        url = f"{self.base_url}/auth/login"
        payload = {"username": self.username, "password": self.password}
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            token, client_id = data.get('token'), data.get('id')
            self.subsonic_token, self.subsonic_salt = data.get('subsonicToken'), data.get('subsonicSalt')

            if not all([token, client_id, self.subsonic_token, self.subsonic_salt]):
                raise ValueError("登录响应中缺少关键认证信息")
            
            self.client.headers.update({
                'x-nd-authorization': f'Bearer {token}',
                'x-nd-client-unique-id': client_id,
                'User-Agent': 'MusicAPIServer-Importer/1.0'
            })
            print("✓ Navidrome 登录成功！")
            self.is_logged_in = True
            return True
        except (httpx.RequestError, ValueError, json.JSONDecodeError) as e:
            print(f"✗ Navidrome 登录失败: {e}")
            self.is_logged_in = False
            return False

    async def search_song(self, artist, title, album=None):
        """使用 Subsonic search3 接口异步搜索歌曲。"""
        if not self.is_logged_in: return None
        url = f"{self.base_url}/rest/search3.view"
        query = f"{artist} {title} {album}" if album else f"{artist} {title}"
        params = {
            'u': self.username, 't': self.subsonic_token, 's': self.subsonic_salt,
            'v': '1.16.1', 'c': 'MusicAPIServer-Importer', 'f': 'json',
            'query': query, 'songCount': 20, 'artistCount': 0, 'albumCount': 0
        }
        try:
            # Subsonic API不使用 x-nd-* 头，所以创建一个临时客户端或直接请求
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'subsonic-response' not in data or data['subsonic-response'].get('status') != 'ok':
                return None
            results = data.get('subsonic-response', {}).get('searchResult3', {}).get('song', [])
            if not results: return None
            if not isinstance(results, list): results = [results]

            artist_lower, title_lower = artist.lower(), title.lower()
            album_lower = album.lower() if album else None
            if album_lower:
                for song in results:
                    if (song.get('artist','').lower() == artist_lower and song.get('title','').lower() == title_lower and song.get('album','').lower() == album_lower):
                        return song.get('id')
            for song in results:
                if (song.get('artist','').lower() == artist_lower and song.get('title','').lower() == title_lower):
                    return song.get('id')
            return results[0].get('id')
        except Exception as e:
            print(f"  -> ✗ 调用 Navidrome search3 API 时发生异常: {e}")
            return None

    async def create_playlist(self, name):
        """异步在Navidrome中创建一个新歌单。"""
        if not self.is_logged_in: return None
        url = f"{self.base_url}/api/playlist"
        payload = {"name": name, "comment": "从在线平台自动导入", "public": False}
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return response.json().get('id')
        except Exception as e:
            print(f"✗ 在Navidrome中创建歌单失败: {e}")
            return None

    async def add_songs_to_playlist(self, playlist_id, song_ids):
        """异步将歌曲批量添加到歌单。"""
        if not self.is_logged_in or not song_ids: return False
        url = f"{self.base_url}/api/playlist/{playlist_id}/tracks"
        payload = {"ids": song_ids}
        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"✗ 添加歌曲到Navidrome歌单失败: {e}")
            return False

    async def get_playlist_tracks(self, playlist_id):
        """异步获取指定歌单中的所有歌曲ID。"""
        if not self.is_logged_in: return None
        url = f"{self.base_url}/api/playlist/{playlist_id}/tracks"
        params = {'_start': 0, '_end': 0}
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            tracks = response.json()
            return [track.get('id') for track in tracks]
        except Exception as e:
            print(f"✗ 获取 Navidrome 歌单歌曲失败: {e}")
            return None
            
    async def remove_songs_from_playlist(self, playlist_id, track_ids_to_remove):
        """异步从歌单中批量移除歌曲。"""
        if not self.is_logged_in or not track_ids_to_remove: return False
        url = f"{self.base_url}/api/playlist/{playlist_id}/tracks"
        params = [('id', track_id) for track_id in track_ids_to_remove]
        try:
            response = await self.client.delete(url, params=params)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"✗ 从 Navidrome 歌单移除歌曲失败: {e}")
            return False

    async def move_track_in_playlist(self, playlist_id, track_to_move_id, insert_before_id):
        """异步移动歌单中的一首歌曲到另一个位置。"""
        if not self.is_logged_in: return False
        url = f"{self.base_url}/api/playlist/{playlist_id}/tracks/{track_to_move_id}"
        payload = {"insert_before": insert_before_id}
        try:
            response = await self.client.put(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"✗ 移动 Navidrome 歌单中的歌曲 '{track_to_move_id}' 失败: {e}")
            return False