import os
import sqlite3
import pathlib
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.wave import WAVE
from mutagen.mp4 import MP4
from opencc import OpenCC
from core.config import Config

def get_audio_info(file_path):
    """使用mutagen获取音频文件的时长（毫秒）、专辑名和搜索关键词"""
    duration_ms, album, search_key = 0, None, None
    try:
        if file_path.suffix.lower() == '.mp3':
            audio = MP3(file_path)
            album = audio.get('TALB', [None])[0]
            title = audio.get('TIT2', [None])[0]
            artist = audio.get('TPE1', [None])[0]
        elif file_path.suffix.lower() == '.flac':
            audio = FLAC(file_path)
            album = audio.get('album', [None])[0]
            title = audio.get('title', [None])[0]
            artist = audio.get('artist', [None])[0]
        elif file_path.suffix.lower() == '.wav':
            audio = WAVE(file_path)
            title = audio.get('TIT2', [None])[0]
            artist = audio.get('TPE1', [None])[0]
        elif file_path.suffix.lower() in ['.m4a', '.mp4']:
            audio = MP4(file_path)
            album = audio.get('\xa9alb', [None])[0]
            title = audio.get('\xa9nam', [None])[0]
            artist = audio.get('\xa9ART', [None])[0]
        else:
            return 0, None, None

        # 构造搜索关键词：歌手 - 歌名
        if title and artist:
            # 将分号分隔的多歌手转换为顿号分隔
            if ';' in artist:
                artist = '、'.join([a.strip() for a in artist.split(';')])
            elif '/' in artist:
                artist = artist.replace('/', '、')
            search_key = f"{artist} - {title}"

        if audio and audio.info:
            duration_ms = int(audio.info.length * 1000)
        return duration_ms, album, search_key
    except Exception as e:
        print(f"读取文件元数据时出错 {file_path}: {e}")
        return 0, None, None

def create_database():
    """创建数据库和歌曲表，新增 quality 字段和复合唯一约束"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS songs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_key TEXT NOT NULL,
            file_path TEXT NOT NULL,
            duration_ms INTEGER DEFAULT 0,
            album TEXT,
            quality TEXT NOT NULL,
            UNIQUE(search_key, album, quality)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_key_quality ON songs (search_key, quality)')

    cursor.execute("PRAGMA table_info(songs)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'quality' not in columns:
        cursor.execute('ALTER TABLE songs ADD COLUMN quality TEXT NOT NULL DEFAULT "flac"')
        print("已成功为数据库添加 'quality' 字段。")

    conn.commit()
    conn.close()
    print(f"数据库 '{Config.DATABASE_FILE}' 初始化成功。")

def scan_and_index_music():
    """扫描音乐文件夹，识别音质并存入数据库"""
    if not os.path.exists(Config.MUSIC_DIRECTORY):
        print(f"错误：音乐目录 '{Config.MUSIC_DIRECTORY}' 不存在，请检查路径。")
        return

    converter = OpenCC('t2s')
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    print(f"开始扫描目录: {Config.MUSIC_DIRECTORY}")
    count = 0
    music_path = pathlib.Path(Config.MUSIC_DIRECTORY)

    for file in music_path.rglob('*'):
        if file.suffix.lower() in ['.mp3', '.flac', '.wav', '.m4a']:

            # 获取音频元数据
            duration, album, search_key_meta = get_audio_info(file)
            
            # 如果无法从元数据获取搜索关键词，则回退到原文件名方式
            if search_key_meta:
                search_key = converter.convert(search_key_meta)
            else:
                # 回退方案：使用原文件名逻辑
                original_stem = file.stem
                if original_stem.endswith(' [M]'):
                    search_key_raw = original_stem[:-4]  # 移除 '[M]' 标记
                else:
                    search_key_raw = original_stem
                search_key = converter.convert(search_key_raw)

            # 确定音质
            quality = 'flac'  # 默认为flac
            if file.stem.endswith(' [M]'):
                quality = 'master'

            file_path = str(file.resolve())

            cursor.execute('SELECT id FROM songs WHERE search_key = ? AND quality = ?', (search_key, quality))
            if cursor.fetchone() is None:
                # 如果没有从元数据获取到duration和album，再次尝试获取
                if duration == 0 or album is None:
                    duration, album, _ = get_audio_info(file)
                
                cursor.execute(
                    'INSERT INTO songs (search_key, file_path, duration_ms, album, quality) VALUES (?, ?, ?, ?, ?)',
                    (search_key, file_path, duration, album, quality)
                )
                count += 1
                if count % 100 == 0:
                    print(f"新增索引 {count} 首歌曲...")

    conn.commit()
    conn.close()
    print(f"扫描完成！本次新增了 {count} 首歌曲。")

if __name__ == "__main__":
    create_database()
    scan_and_index_music()

