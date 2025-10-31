import sqlite3
import os
import re
from datetime import datetime

class LocalMusicAPI:
    """一个用于管理本地SQLite数据库的API类"""
    def __init__(self, db_file):
        self.db_file = db_file
        # 向下兼容的音质顺序 - master也可以向下找
        self.quality_order_down = {
            'master': ['master', 'flac', '320', '128'],
            'flac': ['flac', '320', '128'],
            '320': ['320', '128'],
            '128': ['128']
        }
        if not os.path.exists(self.db_file):
            print(f"数据库文件 {self.db_file} 不存在，正在创建...")
        self._create_tables()

    def _get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_file)

    def _query_db(self, query, args=(), one=False):
        """通用的数据库查询函数"""
        try:
            con = self._get_connection()
            cur = con.cursor()
            cur.execute(query, args)
            rv = cur.fetchall()
            con.close()
            return (rv[0] if rv else None) if one else rv
        except Exception as e:
            print(f"本地音乐数据库查询出错: {e}")
            return None

    def _normalize_album_title(self, title: str) -> str:
        """一个简单的专辑标题标准化函数，用于模糊匹配。"""
        if not title:
            return ""
        
        normalized_title = title.lower()
        num_map = {
            '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
            '十六': '16', '十七': '17', '十八': '18', '十九': '19', '十': '10',
            '九': '9', '八': '8', '七': '7', '六': '6', '五': '5',
            '四': '4', '三': '3', '二': '2', '一': '1'
        }
        for cn_num, an_num in num_map.items():
            normalized_title = normalized_title.replace(cn_num, an_num)

        normalized_title = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', normalized_title)
        return normalized_title

    def _create_tables(self):
        """创建所有必需的数据库表"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                search_key TEXT NOT NULL,
                file_path TEXT NOT NULL UNIQUE,
                duration_ms INTEGER DEFAULT 0,
                album TEXT,
                quality TEXT NOT NULL
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS playlist_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                online_playlist_id TEXT NOT NULL,
                navidrome_playlist_id TEXT NOT NULL,
                playlist_name TEXT,
                last_sync_time DATETIME,
                UNIQUE(platform, online_playlist_id)
            )
        ''')
        conn.commit()
        conn.close()

    # --- 歌单映射相关方法 ---
    def add_playlist_mapping(self, platform, online_id, navidrome_id, name):
        """添加一个新的歌单映射关系"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO playlist_mappings (platform, online_playlist_id, navidrome_playlist_id, playlist_name) VALUES (?, ?, ?, ?)",
                (platform, online_id, navidrome_id, name)
            )
            conn.commit()
            print(f"✓ 成功将 {platform} 歌单 '{name}' (ID: {online_id}) 映射到 Navidrome 歌单 (ID: {navidrome_id})")
        except sqlite3.IntegrityError:
            print(f"警告: {platform} 歌单 {online_id} 的映射关系已存在。")
        except Exception as e:
            print(f"✗ 添加歌单映射时出错: {e}")
        finally:
            conn.close()
    
    def get_mapping_for_online_playlist(self, platform, online_id):
        """根据在线平台ID查找是否存在映射"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT navidrome_playlist_id FROM playlist_mappings WHERE platform = ? AND online_playlist_id = ?", (platform, online_id))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
        
    def update_sync_time(self, navidrome_id):
        """更新指定歌单的最后同步时间"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE playlist_mappings SET last_sync_time = ? WHERE navidrome_playlist_id = ?", (datetime.now(), navidrome_id))
        conn.commit()
        conn.close()

    # --- 歌曲相关方法 ---
    def add_song_to_db(self, search_key, file_path, duration, album, quality):
        """向数据库中添加一条新的歌曲记录"""
        try:
            con = self._get_connection()
            cur = con.cursor()
            cur.execute(
                'INSERT OR IGNORE INTO songs (search_key, file_path, duration_ms, album, quality) VALUES (?, ?, ?, ?, ?)',
                (search_key, file_path, duration, album, quality)
            )
            con.commit()
            con.close()
            if cur.rowcount > 0:
                print(f"后台任务: 已成功将 '{search_key}' ({quality}) 写入数据库。")
            return True
        except Exception as e:
            print(f"后台任务: 写入数据库时发生错误: {e}")
            return False

    def get_existing_qualities(self, search_key: str, album: str = None) -> list:
        """获取本地库中某首歌曲已存在的所有音质版本 (支持模糊专辑匹配)。"""
        if not album:
            results = self._query_db('SELECT quality FROM songs WHERE search_key = ?', (search_key,))
            return [row[0] for row in results] if results else []

        all_versions = self._query_db('SELECT quality, album FROM songs WHERE search_key = ?', (search_key,))
        if not all_versions:
            return []

        normalized_input_album = self._normalize_album_title(album)
        matching_qualities = []
        for quality, db_album in all_versions:
            normalized_db_album = self._normalize_album_title(db_album)
            if normalized_input_album == normalized_db_album:
                matching_qualities.append(quality)
                
        return list(set(matching_qualities))

    def search_song(self, search_key: str, album: str | None = None, quality: str | None = None) -> list | None:
        """根据 '歌手 - 歌名', 可选专辑和可选音质进行搜索，统一返回列表格式"""
        sql = 'SELECT id, search_key, duration_ms, album, quality FROM songs WHERE search_key = ?'
        args = [search_key]

        if album:
            sql += ' AND album LIKE ?'
            args.append(f'%{album}%')

        if quality and quality in self.quality_order_down:
            qualities_to_try = self.quality_order_down[quality]
            for q in qualities_to_try:
                quality_sql = sql + ' AND quality = ?'
                quality_args = args + [q]
                results = self._query_db(quality_sql, quality_args, one=False)
                if results:
                    songs = []
                    for result in results:
                        song_id, found_key, duration_ms, found_album, found_quality = result
                        songs.append({
                            "id": song_id, "title": found_key, "duration_ms": duration_ms,
                            "album": found_album, "quality": found_quality,
                            "download_url": f"/api/local/download/{song_id}"
                        })
                    return songs
            return None
        else:
            results = self._query_db(sql, args, one=False)
            if results:
                songs = []
                for result in results:
                    song_id, found_key, duration_ms, found_album, found_quality = result
                    songs.append({
                        "id": song_id, "title": found_key, "duration_ms": duration_ms,
                        "album": found_album, "quality": found_quality,
                        "download_url": f"/api/local/download/{song_id}"
                    })
                return songs
            else:
                return None

    def get_song_path_by_id(self, song_id: int) -> str | None:
        """根据ID获取歌曲的物理文件路径"""
        result = self._query_db('SELECT file_path FROM songs WHERE id = ?', (song_id,), one=True)
        return result[0] if result else None