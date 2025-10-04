import re
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

    def _normalize_album_title(self, title: str) -> str:
        """
        一个简单的专辑标题标准化函数，用于模糊匹配。
        - 转为小写
        - 移除所有非字母、非数字、非中文字符（保留基础匹配项）
        - 简单的中文数字转阿拉伯数字
        """
        if not title:
            return ""
        
        # 转为小写
        normalized_title = title.lower()

        # 定义一个简单的映射来处理用户示例中的数字转换
        # 注意：这个实现比较简单，仅用于处理“十”和“十一”到“十九”这类常见情况
        num_map = {
            '十一': '11', '十二': '12', '十三': '13', '十四': '14', '十五': '15',
            '十六': '16', '十七': '17', '十八': '18', '十九': '19', '十': '10',
            '九': '9', '八': '8', '七': '7', '六': '6', '五': '5',
            '四': '4', '三': '3', '二': '2', '一': '1'
        }
        for cn_num, an_num in num_map.items():
            normalized_title = normalized_title.replace(cn_num, an_num)

        # 移除所有非字母、非数字、非中文字符，以忽略特殊版本标记（如 "special edition"）
        # [^a-z0-9\u4e00-\u9fa5] 匹配任何不是小写字母、数字或中文字符的字符
        normalized_title = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', normalized_title)

        return normalized_title

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
        """
        获取本地库中某首歌曲已存在的所有音质版本 (支持模糊专辑匹配)。
        """
        if not album:
            # 如果没有提供专辑名，则使用原有的精确匹配逻辑
            results = self._query_db('SELECT quality FROM songs WHERE search_key = ?', (search_key,))
            return [row[0] for row in results] if results else []

        # 获取该 search_key 对应的所有歌曲版本
        all_versions = self._query_db('SELECT quality, album FROM songs WHERE search_key = ?', (search_key,))
        if not all_versions:
            return []

        # 标准化输入的专辑名以进行比较
        normalized_input_album = self._normalize_album_title(album)

        # 遍历数据库结果，进行标准化比较
        matching_qualities = []
        for quality, db_album in all_versions:
            normalized_db_album = self._normalize_album_title(db_album)
            # 如果标准化后的专辑名匹配，则记录该音质
            if normalized_input_album == normalized_db_album:
                matching_qualities.append(quality)
                
        # 返回去重后的音质列表
        return list(set(matching_qualities))

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

