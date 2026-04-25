import os
import re
import asyncio
import aiosqlite
from datetime import datetime

from utils.helpers import Utils

def auto_async(func):
    """
    装饰器：智能检测当前是否处于异步事件循环中。
    如果是，则返回协程对象（需 await）；
    如果否（例如被独立的同步脚本调用），则直接使用 asyncio.run() 阻塞执行并返回结果。
    """
    def wrapper(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            return func(*args, **kwargs)
        except RuntimeError:
            return asyncio.run(func(*args, **kwargs))
    return wrapper

class DatabaseManager:
    """用于管理本地SQLite元数据中心的API类 (全异步重构版)"""

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
        self._create_tables_sync()

    def _create_tables_sync(self):
        """同步方式初始化表结构，保证启动时立刻就绪"""
        import sqlite3
        conn = sqlite3.connect(self.db_file)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")

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

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cover_art (
                song_id INTEGER PRIMARY KEY,
                mime_type TEXT NOT NULL,
                image_data BLOB NOT NULL,
                FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lyrics (
                song_id INTEGER PRIMARY KEY,
                lyrics TEXT,
                tlyrics TEXT,
                FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
            )
        """)

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

    @auto_async
    async def _query_db(self, query, args=(), one=False):
        """通用的异步数据库查询函数"""
        try:
            async with aiosqlite.connect(self.db_file) as db:
                await db.execute("PRAGMA foreign_keys = ON;")
                db.row_factory = aiosqlite.Row
                async with db.execute(query, args) as cursor:
                    if one:
                        row = await cursor.fetchone()
                        return dict(row) if row else None
                    else:
                        rows = await cursor.fetchall()
                        return [dict(row) for row in rows] if rows else []
        except Exception as e:
            print(f"本地音乐数据库查询出错: {e}")
            return None

    def _normalize_album_title(self, title: str) -> str:
        """一个简单的专辑标题标准化函数，用于模糊匹配。"""
        if not title:
            return ""

        normalized_title = title.lower()
        num_map = {
            "十一": "11", "十二": "12", "十三": "13", "十四": "14", "十五": "15",
            "十六": "16", "十七": "17", "十八": "18", "十九": "19", "十": "10",
            "九": "9", "八": "8", "七": "7", "六": "6", "五": "5",
            "四": "4", "三": "3", "二": "2", "一": "1",
        }
        for cn_num, an_num in num_map.items():
            normalized_title = normalized_title.replace(cn_num, an_num)

        normalized_title = re.sub(r"[^a-z0-9\u4e00-\u9fa5]", "", normalized_title)
        return normalized_title

    @auto_async
    async def add_playlist_mapping(self, platform, online_id, navidrome_id, name):
        """添加一个新的歌单映射关系"""
        try:
            async with aiosqlite.connect(self.db_file) as db:
                await db.execute("PRAGMA foreign_keys = ON;")
                await db.execute(
                    "INSERT INTO playlist_mappings (platform, online_playlist_id, navidrome_playlist_id, playlist_name) VALUES (?, ?, ?, ?)",
                    (platform, online_id, navidrome_id, name),
                )
                await db.commit()
                print(f"✓ 成功将 {platform} 歌单 '{name}' (ID: {online_id}) 映射到 Navidrome 歌单 (ID: {navidrome_id})")
        except sqlite3.IntegrityError:
            print(f"警告: {platform} 歌单 {online_id} 的映射关系已存在。")
        except Exception as e:
            print(f"✗ 添加歌单映射时出错: {e}")

    @auto_async
    async def get_mapping_for_online_playlist(self, platform, online_id):
        """根据在线平台ID查找是否存在映射"""
        result = await self._query_db(
            "SELECT navidrome_playlist_id FROM playlist_mappings WHERE platform = ? AND online_playlist_id = ?",
            (platform, online_id),
            one=True
        )
        return result["navidrome_playlist_id"] if result else None

    @auto_async
    async def update_sync_time(self, navidrome_id):
        """更新指定歌单的最后同步时间"""
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                "UPDATE playlist_mappings SET last_sync_time = ? WHERE navidrome_playlist_id = ?",
                (datetime.now(), navidrome_id),
            )
            await db.commit()

    @auto_async
    async def add_song_to_db(
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
        try:
            async with aiosqlite.connect(self.db_file) as db:
                await db.execute("PRAGMA foreign_keys = ON;")
                cursor = await db.cursor()
                
                await cursor.execute(
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
                        song_info.get("title"),
                        song_info.get("is_instrumental", 0),
                    ),
                )
                
                song_id = cursor.lastrowid
                if song_id == 0:  # 如果 (IGNORE) 触发，说明文件已存在
                    print(f"后台任务: '{song_info.get('search_key')}' 的记录已存在，跳过。")
                    return True

                if cover_data:
                    await cursor.execute(
                        "INSERT OR IGNORE INTO cover_art (song_id, mime_type, image_data) VALUES (?, ?, ?)",
                        (song_id, cover_mime, cover_data),
                    )

                if lyric or tlyric:
                    await cursor.execute(
                        "INSERT OR IGNORE INTO lyrics (song_id, lyrics, tlyrics) VALUES (?, ?, ?)",
                        (song_id, lyric, tlyric),
                    )

                await db.commit()
                print(f"后台任务: 已成功将 '{song_info.get('search_key')}' ({quality}) 完整写入数据库。")
                return True
        except Exception as e:
            print(f"后台任务: 写入数据库时发生严重错误: {e}")
            return False

    @auto_async
    async def get_existing_qualities(self, search_key: str, album: str = None) -> list:
        """获取本地库中某首歌曲已存在的所有音质版本 (支持模糊专辑匹配)。"""
        if not album:
            results = await self._query_db(
                "SELECT quality FROM songs WHERE search_key = ?", (search_key,)
            )
            return [row.get("quality") for row in results if row.get("quality")] if results else []

        all_versions = await self._query_db(
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

    @auto_async
    async def search_local_music(
        self, query: str, mode: str = "any", limit: int = 20, offset: int = 0
    ) -> dict | None:
        """
        在本地音乐库中执行高级搜索，并返回文件大小和伴奏标记。
        """
        search_term = f"%{query}%"

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
                    "artist", "albumartist", "album", "title", "search_key"
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
        count_result = await self._query_db(count_query, args, one=True)
        total_count = count_result["COUNT(id)"] if count_result else 0

        order_clause = "ORDER BY artist, album, discnumber, tracknumber"
        limit_clause = ""

        if mode != "album":
            limit_clause = "LIMIT ? OFFSET ?"
            args.extend([limit, offset])

        full_data_query = f"{select_clause} {from_clause} {where_clause} {order_clause} {limit_clause}"
        results = await self._query_db(full_data_query, args, one=False)

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

    @auto_async
    async def get_song_path_by_id(self, song_id: int) -> str | None:
        """根据ID获取歌曲的物理文件路径"""
        result = await self._query_db(
            "SELECT file_path FROM songs WHERE id = ?", (song_id,), one=True
        )
        return result["file_path"] if result else None

    @auto_async
    async def get_songs_by_quality(self, quality: str) -> list:
        """获取指定音质的所有歌曲 (用于洗版脚本)"""
        query = "SELECT id, file_path, search_key, title, artist, album, duration_ms FROM songs WHERE quality = ?"
        return await self._query_db(query, (quality,), one=False)

    @auto_async
    async def check_song_exists(self, search_key: str, quality: str = None) -> bool:
        """检查指定歌曲是否存在 (可根据音质筛选)"""
        if quality:
            query = "SELECT id FROM songs WHERE search_key = ? AND quality = ?"
            result = await self._query_db(query, (search_key, quality), one=True)
        else:
            query = "SELECT id FROM songs WHERE search_key = ?"
            result = await self._query_db(query, (search_key,), one=True)
        return bool(result)

    @auto_async
    async def find_original_song_for_instrumental(self, artist: str, title_and_album: str, converter) -> str | None:
        """智能查找伴奏对应的原版歌曲文件路径"""
        artist_simplified = converter.convert(artist)
        query = "SELECT file_path, search_key, album FROM songs WHERE search_key LIKE ?"
        candidate_songs = await self._query_db(query, (f"{artist_simplified} - %",), one=False)
        if not candidate_songs:
            return None

        for row in candidate_songs:
            file_path = row["file_path"]
            search_key = row["search_key"]
            db_album = row["album"]
            try:
                original_title = search_key.split(" - ", 1)[1]
            except IndexError:
                continue
            if original_title.lower() in title_and_album.lower() and (
                not db_album or db_album.lower() in title_and_album.lower()
            ):
                return file_path
        return None

    @auto_async
    async def execute_non_query(self, query: str, args: tuple = ()) -> int:
        """执行单条非查询 SQL 语句 (INSERT, UPDATE, DELETE)，返回受影响行数"""
        try:
            async with aiosqlite.connect(self.db_file) as db:
                await db.execute("PRAGMA foreign_keys = ON;")
                cursor = await db.execute(query, args)
                await db.commit()
                return cursor.rowcount
        except Exception as e:
            print(f"执行数据库更新时出错: {e}")
            raise e

    @auto_async
    async def get_song_details_by_id(self, song_id: int) -> dict | None:
        """获取歌曲所有元数据 (用于播放)"""
        query = """
            SELECT s.*, l.lyrics, l.tlyrics
            FROM songs s
            LEFT JOIN lyrics l ON s.id = l.song_id
            WHERE s.id = ?
        """
        result = await self._query_db(query, (song_id,), one=True)
        return result

    @auto_async
    async def get_cover_art_by_id(self, song_id: int) -> dict | None:
        """获取歌曲的封面数据"""
        query = "SELECT mime_type, image_data FROM cover_art WHERE song_id = ?"
        return await self._query_db(query, (song_id,), one=True)

    @auto_async
    async def get_all_songs(self) -> list:
        """获取数据库中所有歌曲列表，用于管理后台"""
        query = "SELECT id, search_key, album, quality, file_path, is_instrumental FROM songs ORDER BY id ASC"
        return await self._query_db(query, one=False)

    @auto_async
    async def delete_songs(self, ids_to_delete: list[int]) -> dict:
        """批量删除歌曲（数据库记录和硬盘文件）"""
        deleted_count, errors = 0, []
        try:
            async with aiosqlite.connect(self.db_file) as db:
                await db.execute("PRAGMA foreign_keys = ON;")
                
                placeholders = ",".join("?" for _ in ids_to_delete)
                cursor = await db.execute(
                    f"SELECT id, file_path, search_key FROM songs WHERE id IN ({placeholders})",
                    ids_to_delete,
                )
                songs_to_delete = await cursor.fetchall()
                
                for row in songs_to_delete:
                    song_id = row[0]
                    file_path = row[1]
                    search_key = row[2]
                    try:
                        if file_path and os.path.exists(file_path):
                            os.remove(file_path)
                        await db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
                        deleted_count += 1
                    except Exception as e:
                        errors.append(f"删除歌曲 '{search_key}' (ID: {song_id}) 时出错: {e}")
                await db.commit()
        except Exception as e:
            return {"message": f"数据库操作时发生严重错误: {e}", "errors": [str(e)]}

        message = f"成功删除了 {deleted_count} 首歌曲。"
        if errors:
            message = f"操作部分成功，删除了 {deleted_count} 首歌曲。"
        return {"message": message, "errors": errors}