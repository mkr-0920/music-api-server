import os
import re
import sqlite3
from datetime import datetime

from utils.helpers import Utils


class LocalMusicAPI:
    """用于管理本地SQLite元数据中心的API类"""

    def __init__(self, db_file):
        self.db_file = db_file
        self.quality_order_down = {
            "master": ["master", "flac", "320", "128"],
            "flac": ["flac", "320", "128"],
            "320": ["320", "128"],
            "128": ["128"],
        }
        if not os.path.exists(self.db_file):
            print(f"数据库文件 {self.db_file} 不存在，正在创建...")
        self._create_tables()

    def _get_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_file)
        conn.execute("PRAGMA foreign_keys = ON;")  # 确保外键约束被激活
        return conn

    def _query_db(self, query, args=(), one=False):
        """通用的数据库查询函数"""
        try:
            con = self._get_connection()
            con.row_factory = sqlite3.Row  # 【新】允许按列名访问
            cur = con.cursor()
            cur.execute(query, args)
            rv = cur.fetchall()
            con.close()
            results = [dict(row) for row in rv] if rv else []
            return (results[0] if results else None) if one else results
        except Exception as e:
            print(f"本地音乐数据库查询出错: {e}")
            return None

    def _normalize_album_title(self, title: str) -> str:
        """一个简单的专辑标题标准化函数，用于模糊匹配。"""
        if not title:
            return ""

        normalized_title = title.lower()
        num_map = {
            "十一": "11",
            "十二": "12",
            "十三": "13",
            "十四": "14",
            "十五": "15",
            "十六": "16",
            "十七": "17",
            "十八": "18",
            "十九": "19",
            "十": "10",
            "九": "9",
            "八": "8",
            "七": "7",
            "六": "6",
            "五": "5",
            "四": "4",
            "三": "3",
            "二": "2",
            "一": "1",
        }
        for cn_num, an_num in num_map.items():
            normalized_title = normalized_title.replace(cn_num, an_num)

        normalized_title = re.sub(r"[^a-z0-9\u4e00-\u9fa5]", "", normalized_title)
        return normalized_title

    def _create_tables(self):
        """创建所有元数据表和字段"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

        # songs 表 (主表)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS songs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT NOT NULL UNIQUE,
                search_key TEXT,
                quality TEXT,
                duration_ms INTEGER DEFAULT 0,
                album TEXT,
                artist TEXT,
                albumartist TEXT,
                composer TEXT,
                lyricist TEXT,
                arranger TEXT,
                producer TEXT,
                mix TEXT,
                mastering TEXT,
                bpm TEXT,
                genre TEXT,
                tracknumber TEXT,
                totaltracks TEXT,
                discnumber TEXT,
                totaldiscs TEXT,
                date TEXT,
                year TEXT,
                title TEXT,
                is_instrumental INTEGER DEFAULT 0
            )
        """)

        # cover_art 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cover_art (
                song_id INTEGER PRIMARY KEY,
                mime_type TEXT NOT NULL,
                image_data BLOB NOT NULL,
                FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
            )
        """)

        # lyrics 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lyrics (
                song_id INTEGER PRIMARY KEY,
                lyrics TEXT,
                tlyrics TEXT,
                FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
            )
        """)

        # playlist_mappings 表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS playlist_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                platform TEXT NOT NULL,
                online_playlist_id TEXT NOT NULL,
                navidrome_playlist_id TEXT NOT NULL,
                playlist_name TEXT,
                last_sync_time DATETIME,
                UNIQUE(platform, online_playlist_id)
            )
        """)

        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_key ON songs (search_key)"
        )
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
                (platform, online_id, navidrome_id, name),
            )
            conn.commit()
            print(
                f"✓ 成功将 {platform} 歌单 '{name}' (ID: {online_id}) 映射到 Navidrome 歌单 (ID: {navidrome_id})"
            )
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
        cursor.execute(
            "SELECT navidrome_playlist_id FROM playlist_mappings WHERE platform = ? AND online_playlist_id = ?",
            (platform, online_id),
        )
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None

    def update_sync_time(self, navidrome_id):
        """更新指定歌单的最后同步时间"""
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE playlist_mappings SET last_sync_time = ? WHERE navidrome_playlist_id = ?",
            (datetime.now(), navidrome_id),
        )
        conn.commit()
        conn.close()

    # --- 歌曲相关方法 ---
    def add_song_to_db(
        self,
        song_info: dict,
        file_path: str,
        quality: str,
        lyric: str,
        tlyric: str,
        cover_data: bytes,
        cover_mime: str,
    ):
        """向所有3个表中写入完整的歌曲元数据 (包含伴奏字段)"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            # 插入主表 (songs)
            cursor.execute(
                """
                INSERT OR IGNORE INTO songs (
                    file_path, search_key, quality, duration_ms, album, artist,
                    albumartist, composer, lyricist, arranger, producer, mix, mastering,
                    bpm, genre, tracknumber, totaltracks, discnumber, totaldiscs, date, year,
                    title, is_instrumental
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    file_path,
                    song_info.get("search_key"),
                    quality,
                    song_info.get("duration_ms"),
                    song_info.get("album"),
                    song_info.get("artist"),
                    song_info.get("albumartist"),
                    song_info.get("composer"),
                    song_info.get("lyricist"),
                    song_info.get("arranger"),
                    song_info.get("producer"),
                    song_info.get("mix"),
                    song_info.get("mastering"),
                    song_info.get("bpm"),
                    song_info.get("genre"),
                    song_info.get("tracknumber"),
                    song_info.get("totaltracks"),
                    song_info.get("discnumber"),
                    song_info.get("totaldiscs"),
                    song_info.get("date"),
                    song_info.get("year"),
                    song_info.get(
                        "title"
                    ),  # 从字典取 title (如果是在线下载的，可能没有传，由 scanner 后续补充)
                    song_info.get("is_instrumental", 0),  # 默认为 0，代表原曲
                ),
            )
            song_id = cursor.lastrowid
            if song_id == 0:  # 如果 (IGNORE) 触发，说明文件已存在
                print(f"后台任务: '{song_info.get('search_key')}' 的记录已存在，跳过。")
                return True

            # 插入封面表 (cover_art)
            if cover_data:
                cursor.execute(
                    "INSERT OR IGNORE INTO cover_art (song_id, mime_type, image_data) VALUES (?, ?, ?)",
                    (song_id, cover_mime, cover_data),
                )

            # 插入歌词表 (lyrics)
            if lyric or tlyric:
                cursor.execute(
                    "INSERT OR IGNORE INTO lyrics (song_id, lyrics, tlyrics) VALUES (?, ?, ?)",
                    (song_id, lyric, tlyric),
                )

            conn.commit()
            print(
                f"后台任务: 已成功将 '{song_info.get('search_key')}' ({quality}) 完整写入数据库。"
            )
            return True
        except Exception as e:
            print(f"后台任务: 写入数据库时发生严重错误: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def get_existing_qualities(self, search_key: str, album: str = None) -> list:
        """获取本地库中某首歌曲已存在的所有音质版本 (支持模糊专辑匹配)。"""
        if not album:
            results = self._query_db(
                "SELECT quality FROM songs WHERE search_key = ?", (search_key,)
            )
            return (
                [row.get("quality") for row in results if row.get("quality")]
                if results
                else []
            )

        all_versions = self._query_db(
            "SELECT quality, album FROM songs WHERE search_key = ?", (search_key,)
        )
        if not all_versions:
            return []

        normalized_input_album = self._normalize_album_title(album)
        matching_qualities = []

        for row in all_versions:
            db_quality = row.get("quality")
            db_album = row.get("album")

            normalized_db_album = self._normalize_album_title(db_album)
            if normalized_input_album == normalized_db_album:
                matching_qualities.append(db_quality)

        return list(set(matching_qualities))

    def search_local_music(
        self, query: str, mode: str = "any", limit: int = 20, offset: int = 0
    ) -> dict | None:
        """
        在本地音乐库中执行高级搜索，并返回文件大小和伴奏标记。
        """
        search_term = f"%{query}%"

        # --- 查询中加入 is_instrumental ---
        select_clause = "SELECT id, search_key, duration_ms, album, quality, artist, title, albumartist, discnumber, tracknumber, file_path, is_instrumental"
        from_clause = "FROM songs"

        where_clause = ""
        args = []

        if mode == "artist":
            where_clause = "WHERE artist LIKE ? OR albumartist LIKE ?"
            args.extend([search_term, search_term])
        elif mode == "album":
            where_clause = "WHERE album LIKE ?"
            args.append(search_term)
        elif mode == "title":
            where_clause = "WHERE title LIKE ?"
            args.append(search_term)
        elif mode == "any":
            pattern = r'"([^"]*)"|(\S+)'
            matches = re.findall(pattern, query)
            keywords = [m[0] if m[0] else m[1] for m in matches]
            keywords = [k for k in keywords if k.strip()]

            if not keywords:
                where_clause = "WHERE 0"
            else:
                fields_to_search = [
                    "artist",
                    "albumartist",
                    "album",
                    "title",
                    "search_key",
                ]

                and_blocks = []
                for kw in keywords:
                    or_conditions = [f"{field} LIKE ?" for field in fields_to_search]
                    or_block = f"({' OR '.join(or_conditions)})"
                    and_blocks.append(or_block)
                    kw_term = f"%{kw}%"
                    args.extend([kw_term] * len(fields_to_search))

                where_clause = f"WHERE {' AND '.join(and_blocks)}"
        else:
            return {"songs": [], "total_count": 0}

        count_query = f"SELECT COUNT(id) {from_clause} {where_clause}"
        count_result = self._query_db(count_query, args, one=True)
        total_count = count_result["COUNT(id)"] if count_result else 0

        order_clause = "ORDER BY artist, album, discnumber, tracknumber"
        limit_clause = ""

        if mode != "album":
            limit_clause = "LIMIT ? OFFSET ?"
            args.extend([limit, offset])

        full_data_query = f"{select_clause} {from_clause} {where_clause} {order_clause} {limit_clause}"
        results = self._query_db(full_data_query, args, one=False)

        if not results:
            return {"songs": [], "total_count": 0}

        songs = []
        for result_dict in results:
            file_size_bytes = 0
            file_path = result_dict.get("file_path")

            if file_path and os.path.exists(file_path):
                try:
                    file_size_bytes = os.path.getsize(file_path)
                except OSError as e:
                    print(f"无法获取文件大小 {file_path}: {e}")

            # --- 格式化并添加到响应中，包含 is_instrumental ---
            songs.append(
                {
                    "id": result_dict["id"],
                    "title": result_dict["title"],
                    "duration_ms": result_dict["duration_ms"],
                    "album": result_dict["album"],
                    "quality": result_dict["quality"],
                    "artist": result_dict["artist"],
                    "size": Utils.format_size(file_size_bytes),
                    "is_instrumental": bool(result_dict.get("is_instrumental", 0)),
                }
            )

        return {"songs": songs, "total_count": total_count}

    def get_song_path_by_id(self, song_id: int) -> str | None:
        """根据ID获取歌曲的物理文件路径"""
        result = self._query_db(
            "SELECT file_path FROM songs WHERE id = ?", (song_id,), one=True
        )
        return result["file_path"] if result else None

    def get_song_details_by_id(self, song_id: int) -> dict | None:
        """获取歌曲所有元数据 (用于播放)"""
        query = """
            SELECT s.*, l.lyrics, l.tlyrics
            FROM songs s
            LEFT JOIN lyrics l ON s.id = l.song_id
            WHERE s.id = ?
        """
        result = self._query_db(query, (song_id,), one=True)
        return result  # 返回的是一个字典

    def get_cover_art_by_id(self, song_id: int) -> dict | None:
        """获取歌曲的封面数据"""
        query = "SELECT mime_type, image_data FROM cover_art WHERE song_id = ?"
        return self._query_db(query, (song_id,), one=True)
