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
        # 确保 file_path 是 pathlib.Path 对象
        file_path_obj = pathlib.Path(file_path)
        if file_path_obj.suffix.lower() == '.mp3':
            audio = MP3(file_path_obj)
            album = audio.get('TALB', [None])[0]
            title = audio.get('TIT2', [None])[0]
            artist = audio.get('TPE1', [None])[0]
        elif file_path_obj.suffix.lower() == '.flac':
            audio = FLAC(file_path_obj)
            album = audio.get('album', [None])[0]
            title = audio.get('title', [None])[0]
            artist = audio.get('artist', [None])[0]
        elif file_path_obj.suffix.lower() == '.wav':
            audio = WAVE(file_path_obj)
            title = audio.get('TIT2', [None])[0]
            artist = audio.get('TPE1', [None])[0]
        elif file_path_obj.suffix.lower() in ['.m4a', '.mp4']:
            audio = MP4(file_path_obj)
            album = audio.get('\xa9alb', [None])[0]
            title = audio.get('\xa9nam', [None])[0]
            artist = audio.get('\xa9ART', [None])[0]
        else:
            return 0, None, None

        if title and artist:
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
    """创建数据库，使用 file_path 作为唯一约束，更适合多目录扫描"""
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    # 使用 file_path 作为 UNIQUE 键，这是最可靠的防止重复的方式
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
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_search_key ON songs (search_key)')

    cursor.execute("PRAGMA table_info(songs)")
    columns = [column[1] for column in cursor.fetchall()]
    if 'quality' not in columns:
        cursor.execute('ALTER TABLE songs ADD COLUMN quality TEXT NOT NULL DEFAULT "flac"')
        print("已成功为数据库添加 'quality' 字段。")

    conn.commit()
    conn.close()
    print(f"数据库 '{Config.DATABASE_FILE}' 初始化成功。")

def scan_and_index_music():
    """扫描主音乐目录和FLAC备用目录，并存入数据库"""
    
    # 定义要扫描的目录列表
    dirs_to_scan = []
    if hasattr(Config, 'MUSIC_DIRECTORY') and Config.MUSIC_DIRECTORY:
        dirs_to_scan.append(Config.MUSIC_DIRECTORY)
    # 检查 Config 中是否存在 FLAC_DIRECTORY 变量，如果存在则添加到扫描列表
    if hasattr(Config, 'FLAC_DIRECTORY') and Config.FLAC_DIRECTORY:
        if Config.FLAC_DIRECTORY not in dirs_to_scan: # 避免重复
            dirs_to_scan.append(Config.FLAC_DIRECTORY)

    if not dirs_to_scan:
        print("错误：未在 core/config.py 中配置任何音乐目录 (MUSIC_DIRECTORY)。")
        return

    converter = OpenCC('t2s')
    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    total_new_songs = 0

    # 遍历所有需要扫描的目录
    for scan_dir in dirs_to_scan:
        if not os.path.exists(scan_dir):
            print(f"警告：目录 '{scan_dir}' 不存在，跳过扫描。")
            continue

        print(f"开始扫描目录: {scan_dir}")
        count_in_dir = 0
        music_path = pathlib.Path(scan_dir)

        for file in music_path.rglob('*'):
            if file.suffix.lower() in ['.mp3', '.flac', '.wav', '.m4a']:
                file_path = str(file.resolve())

                # 使用文件路径作为唯一标识，检查是否已存在于数据库
                cursor.execute('SELECT id FROM songs WHERE file_path = ?', (file_path,))
                if cursor.fetchone() is not None:
                    continue # 如果已存在，则跳过

                duration, album, search_key_meta = get_audio_info(file)
                
                if search_key_meta:
                    search_key = converter.convert(search_key_meta)
                else:
                    original_stem = file.stem
                    search_key_raw = original_stem.removesuffix(' [M]') if original_stem.endswith(' [M]') else original_stem
                    search_key = converter.convert(search_key_raw)

                # 确定音质
                if file.stem.endswith(' [M]'):
                    quality = 'master'
                elif file.suffix.lower() == '.flac':
                     quality = 'flac'
                elif file.suffix.lower() == '.mp3':
                    # 简化的MP3音质判断，可以根据需要扩展
                    try:
                        audio = MP3(file)
                        bitrate = audio.info.bitrate / 1000
                        if bitrate > 256:
                            quality = '320'
                        else:
                            quality = '128'
                    except:
                        quality = '128'
                else:
                    quality = 'other'


                # 使用 INSERT OR IGNORE 保证数据安全插入
                cursor.execute(
                    'INSERT OR IGNORE INTO songs (search_key, file_path, duration_ms, album, quality) VALUES (?, ?, ?, ?, ?)',
                    (search_key, file_path, duration, album, quality)
                )
                
                if cursor.rowcount > 0:
                    count_in_dir += 1
                    total_new_songs += 1
                    if total_new_songs % 100 == 0:
                        print(f"已新增索引 {total_new_songs} 首歌曲...")
        
        print(f"目录 '{scan_dir}' 扫描完成，新增 {count_in_dir} 首歌曲。")

    conn.commit()
    conn.close()
    print(f"\n所有目录扫描完成！本次共新增了 {total_new_songs} 首歌曲。")

if __name__ == "__main__":
    create_database()
    scan_and_index_music()

