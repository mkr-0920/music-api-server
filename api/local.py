import sqlite3

class LocalMusicAPI:
    def __init__(self, db_path):
        self.db_path = db_path
        # 向下兼容的音质顺序 - master也可以向下找
        self.quality_order_down = {
            'master': ['master', 'flac', '320', '128'],  # master可以向下找
            'flac': ['flac', '320', '128'],              # flac向下找320,128
            '320': ['320', '128'],                       # 320向下找128
            '128': ['128']                              # 128只找128
        }

    def _query_db(self, query, args=(), one=False):
        """通用的数据库查询函数"""
        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cur.execute(query, args)
            rv = cur.fetchall()
            con.close()
            return (rv[0] if rv else None) if one else rv
        except Exception as e:
            print(f"本地音乐数据库查询出错: {e}")
            return None

    def add_song_to_db(self, search_key, file_path, duration, album, quality):
        """向数据库中添加一条新的歌曲记录"""
        try:
            con = sqlite3.connect(self.db_path)
            cur = con.cursor()
            cur.execute(
                'INSERT INTO songs (search_key, file_path, duration_ms, album, quality) VALUES (?, ?, ?, ?, ?)',
                (search_key, file_path, duration, album, quality)
            )
            con.commit()
            con.close()
            print(f"后台任务: 已成功将 '{search_key}' ({quality}) 写入数据库。")
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception as e:
            print(f"后台任务: 写入数据库时发生错误: {e}")
            return False

    def get_existing_qualities(self, search_key: str, album: str = None) -> list:
        """获取本地库中某首歌曲已存在的所有音质版本"""
        if album:
            # 指定专辑时，只查找该专辑下的音质版本
            results = self._query_db('SELECT quality FROM songs WHERE search_key = ? AND album = ?', (search_key, album))
        else:
            # 未指定专辑时，查找所有匹配歌曲的音质版本
            results = self._query_db('SELECT quality FROM songs WHERE search_key = ?', (search_key,))
        return [row[0] for row in results] if results else []

    def search_song(self, search_key: str, album: str | None = None, quality: str | None = None) -> list | None:
        """根据 '歌手 - 歌名', 可选专辑和可选音质进行搜索，统一返回列表格式"""

        # 构建基础SQL查询
        sql = 'SELECT id, search_key, duration_ms, album, quality FROM songs WHERE search_key = ?'
        args = [search_key]

        # 如果指定了专辑，添加专辑条件
        if album:
            sql += ' AND album LIKE ?'
            args.append(f'%{album}%')

        # 如果指定了音质，使用向下兼容查找逻辑
        if quality and quality in self.quality_order_down:
            qualities_to_try = self.quality_order_down[quality]
            
            # 按优先级顺序查找
            for q in qualities_to_try:
                # 构建带音质条件的查询
                quality_sql = sql + ' AND quality = ?'
                quality_args = args + [q]
                
                results = self._query_db(quality_sql, quality_args, one=False)
                
                if results:
                    # 找到该音质的歌曲，立即返回该音质的所有歌曲
                    songs = []
                    for result in results:
                        song_id, found_key, duration_ms, found_album, found_quality = result
                        songs.append({
                            "id": song_id, "title": found_key, "duration_ms": duration_ms,
                            "album": found_album, "quality": found_quality,
                            "download_url": f"/api/local/download/{song_id}"
                        })
                    return songs
            # 所有音质都没找到
            return None
        else:
            # 没指定音质或音质不在定义范围内，返回所有匹配结果
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

