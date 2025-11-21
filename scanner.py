import os
import pathlib
import sqlite3

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from opencc import OpenCC

from core.config import Config


def get_comprehensive_metadata(file_path):
    """
    使用mutagen获取最完整的音频文件元数据。
    返回一个包含所有文本信息、封面数据和歌词的字典。
    """
    metadata = {
        "duration_ms": 0,
        "album": None,
        "artist": None,
        "title": None,
        "search_key": None,
        "albumartist": None,
        "composer": None,
        "lyricist": None,
        "arranger": None,
        "producer": None,
        "mix": None,
        "mastering": None,
        "bpm": None,
        "genre": None,
        "tracknumber": None,
        "totaltracks": None,
        "discnumber": None,
        "totaldiscs": None,
        "date": None,
        "year": None,
        "cover_data": None,
        "cover_mime": None,
        "lyrics": None,
    }

    try:
        path_obj = pathlib.Path(file_path)
        audio_info = None

        if path_obj.suffix.lower() == ".mp3":
            audio = MP3(path_obj, ID3=ID3)
            audio_info = audio.info
            tags = audio.tags
            if tags:
                metadata["artist"] = str(tags.get("TPE1", [None])[0])
                metadata["title"] = str(tags.get("TIT2", [None])[0])
                metadata["album"] = str(tags.get("TALB", [None])[0])
                metadata["albumartist"] = str(tags.get("TPE2", [None])[0])
                metadata["composer"] = str(tags.get("TCOM", [None])[0])
                metadata["lyricist"] = str(tags.get("TEXT", [None])[0])
                metadata["arranger"] = str(tags.get("TPE4", [None])[0])
                metadata["producer"] = str(tags.get("TPE4", [None])[0])
                metadata["genre"] = str(tags.get("TCON", [None])[0])
                metadata["date"] = str(tags.get("TDRC", [None])[0])
                metadata["year"] = str(tags.get("TYER", [None])[0])
                metadata["bpm"] = str(tags.get("TBPM", [None])[0])
                metadata["tracknumber"] = str(tags.get("TRCK", [None])[0]).split("/")[0]
                metadata["totaltracks"] = str(tags.get("TRCK", ["/"])[0]).split("/")[-1]
                metadata["discnumber"] = str(tags.get("TPOS", [None])[0]).split("/")[0]
                metadata["totaldiscs"] = str(tags.get("TPOS", ["/"])[0]).split("/")[-1]
                uslt_frames = tags.getall("USLT")
                if uslt_frames:
                    metadata["lyrics"] = uslt_frames[0].text
                apic_frames = tags.getall("APIC")
                if apic_frames:
                    metadata["cover_data"] = apic_frames[0].data
                    metadata["cover_mime"] = apic_frames[0].mime

        elif path_obj.suffix.lower() == ".flac":
            audio = FLAC(path_obj)
            audio_info = audio.info
            metadata["artist"] = audio.get("artist", [None])[0]
            metadata["title"] = audio.get("title", [None])[0]
            metadata["album"] = audio.get("album", [None])[0]
            metadata["albumartist"] = audio.get("albumartist", [None])[0]
            metadata["composer"] = audio.get("composer", [None])[0]
            metadata["lyricist"] = audio.get("lyricist", [None])[0]
            metadata["arranger"] = audio.get("arranger", [None])[0]
            metadata["producer"] = audio.get("producer", [None])[0]
            metadata["genre"] = audio.get("genre", [None])[0]
            metadata["date"] = audio.get("date", [None])[0]
            if metadata["date"]:
                metadata["year"] = metadata["date"].split("-")[0]
            metadata["bpm"] = audio.get("bpm", [None])[0]
            metadata["tracknumber"] = audio.get("tracknumber", [None])[0]
            metadata["totaltracks"] = audio.get("tracktotal", [None])[0]
            metadata["discnumber"] = audio.get("discnumber", [None])[0]
            metadata["totaldiscs"] = audio.get("disctotal", [None])[0]
            metadata["lyrics"] = audio.get("lyrics", [None])[0]
            if audio.pictures:
                metadata["cover_data"] = audio.pictures[0].data
                metadata["cover_mime"] = audio.pictures[0].mime

        elif path_obj.suffix.lower() == ".wav":
            audio = WAVE(path_obj)
            audio_info = audio.info
            if audio.tags:
                metadata["artist"] = str(audio.tags.get("TPE1", [None])[0])
                metadata["title"] = str(audio.tags.get("TIT2", [None])[0])
                metadata["album"] = str(audio.tags.get("TALB", [None])[0])
                metadata["genre"] = str(audio.tags.get("TCON", [None])[0])

        elif path_obj.suffix.lower() in [".m4a", ".mp4"]:
            audio = MP4(path_obj)
            audio_info = audio.info
            tags = audio.tags
            if tags:
                metadata["artist"] = str(tags.get("\xa9ART", [None])[0])
                metadata["title"] = str(tags.get("\xa9nam", [None])[0])
                metadata["album"] = str(tags.get("\xa9alb", [None])[0])
                metadata["albumartist"] = str(tags.get("aART", [None])[0])
                metadata["composer"] = str(tags.get("\xa9wrt", [None])[0])
                metadata["genre"] = str(tags.get("\xa9gen", [None])[0])
                metadata["date"] = str(tags.get("\xa9day", [None])[0])
                if metadata["date"]:
                    metadata["year"] = metadata["date"].split("-")[0]
                metadata["bpm"] = str(tags.get("tmpo", [0])[0])
                metadata["lyrics"] = str(tags.get("\xa9lyr", [None])[0])
                trkn = tags.get("trkn", [(None, None)])[0]
                metadata["tracknumber"] = str(trkn[0]) if trkn[0] is not None else None
                metadata["totaltracks"] = str(trkn[1]) if trkn[1] is not None else None
                disk = tags.get("disk", [(None, None)])[0]
                metadata["discnumber"] = str(disk[0]) if disk[0] is not None else None
                metadata["totaldiscs"] = str(disk[1]) if disk[1] is not None else None
                covr = tags.get("covr", [None])[0]
                if covr:
                    metadata["cover_data"] = bytes(covr)
                    metadata["cover_mime"] = (
                        "image/jpeg" if covr[13:17].lower() == b"jpeg" else "image/png"
                    )
        else:
            return None

        if audio_info:
            metadata["duration_ms"] = int(audio_info.length * 1000)

        if metadata["title"] and metadata["artist"]:
            artist = metadata["artist"]
            if ";" in artist:
                artist = "、".join([a.strip() for a in artist.split(";")])
            elif "/" in artist:
                artist = artist.replace("/", "、")
            metadata["search_key"] = f"{artist} - {metadata['title']}"

        for key, value in metadata.items():
            if value == "None" or value == "0":
                metadata[key] = None

        return metadata

    except Exception as e:
        print(f"读取文件元数据时出错 {file_path}: {e}")
        return None


def create_database():
    """创建包含所有元数据表和字段的数据库。"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # --- songs 表 (主表) ---
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
            title TEXT
        )
    """)

    # --- cover_art 表 ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cover_art (
            song_id INTEGER PRIMARY KEY,
            mime_type TEXT NOT NULL,
            image_data BLOB NOT NULL,
            FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
        )
    """)

    # --- lyrics 表 ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS lyrics (
            song_id INTEGER PRIMARY KEY,
            lyrics TEXT,
            tlyrics TEXT,
            FOREIGN KEY (song_id) REFERENCES songs (id) ON DELETE CASCADE
        )
    """)

    # --- playlist_mappings 表 ---
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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_key ON songs (search_key)")

    # --- 检查并添加 title 列（用于兼容旧数据库）---
    cursor.execute("PRAGMA table_info(songs)")
    columns = [column[1] for column in cursor.fetchall()]
    if "title" not in columns:
        cursor.execute("ALTER TABLE songs ADD COLUMN title TEXT")
        print("已成功为旧数据库添加 'title' 字段。")

    conn.commit()
    conn.close()
    print(f"数据库 '{Config.DATABASE_FILE}' 初始化/升级成功。")


def scan_and_index_music():
    """扫描音乐文件夹，提取所有元数据并存入数据库。"""

    dirs_to_scan = []
    if hasattr(Config, "MUSIC_DIRECTORY") and Config.MUSIC_DIRECTORY:
        dirs_to_scan.append(Config.MUSIC_DIRECTORY)
    if hasattr(Config, "FLAC_DIRECTORY") and Config.FLAC_DIRECTORY:
        if Config.FLAC_DIRECTORY not in dirs_to_scan:
            dirs_to_scan.append(Config.FLAC_DIRECTORY)

    if not dirs_to_scan:
        print("错误：未在 core/config.py 中配置任何音乐目录。")
        return

    converter = OpenCC("t2s")
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    total_new_songs = 0

    for scan_dir in dirs_to_scan:
        if not os.path.exists(scan_dir):
            print(f"警告：目录 '{scan_dir}' 不存在，跳过扫描。")
            continue

        print(f"开始扫描目录: {scan_dir}")
        count_in_dir = 0
        music_path = pathlib.Path(scan_dir)

        for file in music_path.rglob("*"):
            file_suffix = file.suffix.lower()
            if file.is_file() and file_suffix in [".mp3", ".flac", ".wav", ".m4a"]:
                file_path = str(file.resolve())

                cursor.execute("SELECT id FROM songs WHERE file_path = ?", (file_path,))
                if cursor.fetchone() is not None:
                    continue

                print(f"\n正在索引新文件: {file.name}")
                metadata = get_comprehensive_metadata(file)

                if not metadata:
                    print("  - 无法读取元数据，跳过。")
                    continue

                if not metadata["search_key"]:
                    original_stem = file.stem
                    search_key_raw = (
                        original_stem.removesuffix(" [M]")
                        if original_stem.endswith(" [M]")
                        else original_stem
                    )
                    metadata["search_key"] = converter.convert(search_key_raw)
                    print(
                        f"  - 警告: 缺少元数据，已从文件名回退 search_key: {metadata['search_key']}"
                    )
                    if not metadata["artist"] or not metadata["title"]:
                        try:
                            parts = search_key_raw.split(" - ", 1)
                            if len(parts) == 2:
                                metadata["artist"] = parts[0].strip()
                                metadata["title"] = parts[1].strip()
                        except:
                            pass

                if file.stem.endswith(" [M]"):
                    quality = "master"
                elif file_suffix == ".flac":
                    quality = "flac"
                elif file_suffix == ".mp3":
                    try:
                        bitrate = MP3(file).info.bitrate / 1000
                        quality = "320" if bitrate > 256 else "128"
                    except:
                        quality = "128"
                else:
                    quality = "other"

                try:
                    cursor.execute(
                        """
                        INSERT INTO songs (
                            file_path, search_key, quality, duration_ms, album, artist,
                            albumartist, composer, lyricist, arranger, producer, mix, mastering,
                            bpm, genre, tracknumber, totaltracks, discnumber, totaldiscs, date, year,
                            title
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            file_path,
                            metadata["search_key"],
                            quality,
                            metadata["duration_ms"],
                            metadata["album"],
                            metadata["artist"],
                            metadata["albumartist"],
                            metadata["composer"],
                            metadata["lyricist"],
                            metadata["arranger"],
                            metadata["producer"],
                            metadata["mix"],
                            metadata["mastering"],
                            metadata["bpm"],
                            metadata["genre"],
                            metadata["tracknumber"],
                            metadata["totaltracks"],
                            metadata["discnumber"],
                            metadata["totaldiscs"],
                            metadata["date"],
                            metadata["year"],
                            metadata["title"],
                        ),
                    )

                    song_id = cursor.lastrowid

                    if metadata["cover_data"]:
                        cursor.execute(
                            "INSERT OR IGNORE INTO cover_art (song_id, mime_type, image_data) VALUES (?, ?, ?)",
                            (song_id, metadata["cover_mime"], metadata["cover_data"]),
                        )

                    if metadata["lyrics"]:
                        cursor.execute(
                            "INSERT OR IGNORE INTO lyrics (song_id, lyrics) VALUES (?, ?)",
                            (song_id, metadata["lyrics"]),
                        )

                    conn.commit()

                    count_in_dir += 1
                    total_new_songs += 1
                    print(
                        f"  - [成功] 已将 '{metadata['search_key']}' 完整存入数据库。"
                    )

                except sqlite3.IntegrityError:
                    print("  - [跳过] 文件路径已存在于数据库中。")
                    conn.rollback()
                except Exception as e:
                    print(f"  - [失败] 写入数据库时发生严重错误: {e}")
                    conn.rollback()

        print(f"目录 '{scan_dir}' 扫描完成，新增 {count_in_dir} 首歌曲。")

    conn.close()
    print(f"\n所有目录扫描完成！本次共新增了 {total_new_songs} 首歌曲。")


if __name__ == "__main__":
    create_database()
    scan_and_index_music()
