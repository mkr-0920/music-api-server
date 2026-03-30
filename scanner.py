import os
import pathlib
import re
import sqlite3

from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TALB, TCON, TDRC, TIT2, TPE1, TYER, USLT
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from opencc import OpenCC

from core.config import Config


def get_comprehensive_metadata(file_path):
    """
    使用 mutagen 获取最完整的音频文件元数据。
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


def embed_cloned_metadata_to_file(file_path, cloned_meta):
    """将从数据库克隆来的元数据，物理写入到伴奏文件中"""
    try:
        path_obj = pathlib.Path(file_path)
        ext = path_obj.suffix.lower()

        title = cloned_meta.get("title")
        artist = cloned_meta.get("artist")
        album = cloned_meta.get("album")
        genre = cloned_meta.get("genre")
        date_str = cloned_meta.get("date")
        year_str = cloned_meta.get("year")
        cover_data = cloned_meta.get("cover_data")
        cover_mime = cloned_meta.get("cover_mime", "image/jpeg")
        lyrics = cloned_meta.get("lyrics")

        if ext == ".flac":
            audio = FLAC(file_path)
            if title:
                audio["title"] = title
            if artist:
                audio["artist"] = artist
            if album:
                audio["album"] = album
            if genre:
                audio["genre"] = genre
            if date_str:
                audio["date"] = date_str
            if year_str:
                audio["year"] = year_str
            if lyrics:
                audio["lyrics"] = lyrics

            if cover_data:
                audio.clear_pictures()
                picture = Picture()
                picture.type = 3
                picture.mime = cover_mime
                picture.desc = "Cover"
                picture.data = cover_data
                audio.add_picture(picture)
            audio.save()

        elif ext == ".mp3" or ext == ".wav":
            # Mutagen 对带有 ID3 的 WAV 也支持通过类似方式写入
            audio = MP3(file_path, ID3=ID3) if ext == ".mp3" else WAVE(file_path)
            if audio.tags is None:
                audio.add_tags()

            if title:
                audio.tags.add(TIT2(encoding=3, text=title))
            if artist:
                audio.tags.add(TPE1(encoding=3, text=artist))
            if album:
                audio.tags.add(TALB(encoding=3, text=album))
            if genre:
                audio.tags.add(TCON(encoding=3, text=genre))
            if date_str:
                audio.tags.add(TDRC(encoding=3, text=date_str))
            if year_str:
                audio.tags.add(TYER(encoding=3, text=year_str))
            if lyrics:
                audio.tags.add(USLT(encoding=3, text=lyrics))

            if cover_data:
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime=cover_mime,
                        type=3,
                        desc="Cover",
                        data=cover_data,
                    )
                )
            audio.save()

    except Exception as e:
        print(f"  - [警告] 向伴奏文件 {file_path} 物理写入元数据时出错: {e}")


def create_database():
    """创建包含所有元数据表和字段的数据库 (全新建表版本)。"""
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
            title TEXT,
            is_instrumental INTEGER DEFAULT 0
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

    conn.commit()
    conn.close()
    print(f"数据库 '{Config.DATABASE_FILE}' 初始化成功。")


def scan_and_index_music():
    """扫描音乐文件夹，提取所有元数据并存入数据库。支持伴奏克隆。"""

    dirs_to_scan = []
    if hasattr(Config, "MASTER_DIRECTORY") and Config.MASTER_DIRECTORY:
        dirs_to_scan.append(Config.MASTER_DIRECTORY)
    if hasattr(Config, "FLAC_DIRECTORY") and Config.FLAC_DIRECTORY:
        if Config.FLAC_DIRECTORY not in dirs_to_scan:
            dirs_to_scan.append(Config.FLAC_DIRECTORY)
    if hasattr(Config, "LOSSY_DIRECTORY") and Config.LOSSY_DIRECTORY:
        if Config.LOSSY_DIRECTORY not in dirs_to_scan:
            dirs_to_scan.append(Config.LOSSY_DIRECTORY)
    if hasattr(Config, "INSTRUMENTAL_DIRECTORY") and Config.INSTRUMENTAL_DIRECTORY:
        if Config.INSTRUMENTAL_DIRECTORY not in dirs_to_scan:
            dirs_to_scan.append(Config.INSTRUMENTAL_DIRECTORY)

    if not dirs_to_scan:
        print("错误：未在 core/config.py 中配置任何音乐扫描目录。")
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

                # --- 伴奏识别与克隆核心逻辑 ---
                is_instrumental = 0
                metadata = None

                # 检查是否符合严格的伴奏命名规范: "歌手 - 歌名 专辑名 (Instrumental).ext"
                if "(Instrumental)" in file.stem:
                    is_instrumental = 1
                    # 正则提取: 捕获前方的 "歌手 - 歌名" 作为 search_key
                    match = re.match(
                        r"^(.+? - .+?)(?: .+?)? \(Instrumental\)", file.stem
                    )

                    if match:
                        raw_search_key = match.group(1)
                        search_key = converter.convert(raw_search_key)

                        print(f"  - 识别为伴奏文件，正在寻找原曲匹配: {search_key}")
                        # 去数据库寻找原曲
                        cursor.execute(
                            "SELECT * FROM songs WHERE search_key = ? AND is_instrumental = 0 LIMIT 1",
                            (search_key,),
                        )
                        orig_row = cursor.fetchone()

                        if orig_row:
                            columns = [desc[0] for desc in cursor.description]
                            orig_data = dict(zip(columns, orig_row))

                            # 获取真实的时长 (伴奏自己的实际音频时长)
                            basic_meta = get_comprehensive_metadata(file)
                            actual_duration = (
                                basic_meta.get("duration_ms", 0) if basic_meta else 0
                            )

                            # 构建克隆字典
                            metadata = orig_data.copy()
                            metadata["duration_ms"] = actual_duration
                            metadata["title"] = (
                                f"{orig_data.get('title', '未知')} (Instrumental)"
                            )
                            metadata["is_instrumental"] = 1
                            metadata["cover_data"] = None
                            metadata["cover_mime"] = None
                            metadata["lyrics"] = None

                            # 获取并装填原曲封面
                            cursor.execute(
                                "SELECT image_data, mime_type FROM cover_art WHERE song_id = ?",
                                (orig_data["id"],),
                            )
                            cover_row = cursor.fetchone()
                            if cover_row:
                                metadata["cover_data"] = cover_row[0]
                                metadata["cover_mime"] = cover_row[1]

                            # 获取并装填原曲歌词
                            cursor.execute(
                                "SELECT lyrics FROM lyrics WHERE song_id = ?",
                                (orig_data["id"],),
                            )
                            lyrics_row = cursor.fetchone()
                            if lyrics_row:
                                metadata["lyrics"] = lyrics_row[0]

                            print("  - [成功] 已匹配原曲，正在向伴奏物理写入元数据...")
                            embed_cloned_metadata_to_file(file_path, metadata)
                        else:
                            print(
                                f"  - [警告] 未在数据库找到原曲 '{search_key}'，作为独立文件解析。"
                            )

                # 如果不是伴奏，或者伴奏没匹配到原曲，回退到常规解析
                if not metadata:
                    metadata = get_comprehensive_metadata(file)
                    if metadata:
                        metadata["is_instrumental"] = is_instrumental
                        if is_instrumental and not metadata.get("title"):
                            metadata["title"] = file.stem

                if not metadata:
                    print("  - 无法读取元数据，跳过。")
                    continue

                if not metadata.get("search_key"):
                    original_stem = file.stem
                    search_key_raw = (
                        original_stem.removesuffix(" [M]")
                        if original_stem.endswith(" [M]")
                        else original_stem
                    )
                    metadata["search_key"] = converter.convert(search_key_raw)
                    if not metadata.get("artist") or not metadata.get("title"):
                        try:
                            parts = search_key_raw.split(" - ", 1)
                            if len(parts) == 2:
                                metadata["artist"] = parts[0].strip()
                                metadata["title"] = parts[1].strip()
                        except:
                            pass

                # 音质判定 (新增对伴奏的音质标记)
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
                            title, is_instrumental
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            file_path,
                            metadata.get("search_key"),
                            quality,
                            metadata.get("duration_ms", 0),
                            metadata.get("album"),
                            metadata.get("artist"),
                            metadata.get("albumartist"),
                            metadata.get("composer"),
                            metadata.get("lyricist"),
                            metadata.get("arranger"),
                            metadata.get("producer"),
                            metadata.get("mix"),
                            metadata.get("mastering"),
                            metadata.get("bpm"),
                            metadata.get("genre"),
                            metadata.get("tracknumber"),
                            metadata.get("totaltracks"),
                            metadata.get("discnumber"),
                            metadata.get("totaldiscs"),
                            metadata.get("date"),
                            metadata.get("year"),
                            metadata.get("title"),
                            metadata.get("is_instrumental", 0),
                        ),
                    )

                    song_id = cursor.lastrowid

                    if metadata.get("cover_data"):
                        cursor.execute(
                            "INSERT OR IGNORE INTO cover_art (song_id, mime_type, image_data) VALUES (?, ?, ?)",
                            (
                                song_id,
                                metadata.get("cover_mime", "image/jpeg"),
                                metadata["cover_data"],
                            ),
                        )

                    if metadata.get("lyrics"):
                        cursor.execute(
                            "INSERT OR IGNORE INTO lyrics (song_id, lyrics) VALUES (?, ?)",
                            (song_id, metadata["lyrics"]),
                        )

                    conn.commit()
                    count_in_dir += 1
                    total_new_songs += 1
                    print(
                        f"  - [成功] 已将 '{metadata.get('search_key')}' (伴奏: {bool(is_instrumental)}) 完整存入数据库。"
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
