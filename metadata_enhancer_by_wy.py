import os
import sys
import time
import argparse
from pathlib import Path

# 确保能从项目根目录正确导入模块
try:
    from api.netease import NeteaseMusicAPI
    from core.config import Config
except ImportError:
    print("错误: 无法导入项目模块。请确保此脚本与您的项目结构在同一根目录下。")
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from api.netease import NeteaseMusicAPI
        from core.config import Config
    except ImportError as e:
        print(f"再次尝试导入失败: {e}")
        sys.exit(1)

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TCON, TCOM, TEXT, TPE4, TBPM, TXXX
from mutagen.flac import FLAC

# 定义我们关心的所有元数据字段及其对应的mutagen标签
METADATA_MAP = {
    'mp3': {
        'artist': 'TPE1', 'title': 'TIT2', 'genre': 'TCON',
        'composer': 'TCOM', 'lyricist': 'TEXT', 'arranger': 'TPE4',
        'producer': 'TPE4', 'bpm': 'TBPM',
        'mix': ('TXXX', 'MIXING'), 'mastering': ('TXXX', 'MASTERING')
    },
    'flac': {
        'artist': 'artist', 'title': 'title', 'genre': 'genre',
        'composer': 'composer', 'lyricist': 'lyricist', 'arranger': 'arranger',
        'producer': 'producer', 'bpm': 'bpm',
        'mix': 'mixing engineer', 'mastering': 'mastering engineer'
    }
}

def get_all_metadata(file_path):
    """从本地文件中读取所有我们关心的元数据。"""
    metadata = {}
    try:
        path_obj = Path(file_path)
        if path_obj.suffix.lower() == '.mp3':
            tags = MP3(path_obj, ID3=ID3)
            for key, tag_name in METADATA_MAP['mp3'].items():
                if isinstance(tag_name, tuple): # 处理 TXXX 自定义标签
                    txxx_tags = tags.getall(tag_name[0])
                    value = next((t.text[0] for t in txxx_tags if t.desc == tag_name[1]), None)
                else:
                    tag = tags.get(tag_name)
                    value = str(tag[0]) if tag else None
                metadata[key] = value
        elif path_obj.suffix.lower() == '.flac':
            tags = FLAC(path_obj)
            for key, tag_name in METADATA_MAP['flac'].items():
                value = tags.get(tag_name, [None])[0]
                metadata[key] = value
    except Exception as e:
        print(f"  - 无法读取元数据: {e}")
    return metadata

def write_metadata(file_path, data_to_write):
    """将一个字典中的所有元数据写入文件。"""
    try:
        path_obj = Path(file_path)
        if path_obj.suffix.lower() == '.mp3':
            audio = MP3(path_obj, ID3=ID3)
            if audio.tags is None: audio.add_tags()
            for key, value in data_to_write.items():
                tag_info = METADATA_MAP['mp3'].get(key)
                if isinstance(tag_info, tuple):
                    audio.tags.add(TXXX(encoding=3, desc=tag_info[1], text=value))
                else:
                    tag_class = globals()[tag_info]
                    audio.tags.add(tag_class(encoding=3, text=value))
            audio.save()
        elif path_obj.suffix.lower() == '.flac':
            audio = FLAC(path_obj)
            for key, value in data_to_write.items():
                tag_name = METADATA_MAP['flac'].get(key)
                audio[tag_name] = value
            audio.save()
        print(f"  - [成功] 已写入 {len(data_to_write)} 项新元数据: {list(data_to_write.keys())}")
        return True
    except Exception as e:
        print(f"  - [失败] 写入元数据时出错: {e}")
        return False

def enhance_metadata(target_directory, dry_run=False):
    """按需从网易云百科接口补全缺失的元数据。"""
    print("正在初始化网易云API...")
    try:
        netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, None, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)
    except TypeError:
        netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, None, Config.MUSIC_DIRECTORY)

    music_path = Path(target_directory)
    if not music_path.is_dir():
        print(f"错误：提供的路径 '{target_directory}' 不是一个有效的目录。")
        return

    print(f"开始扫描目录: {target_directory}")
    if dry_run: print("\n--- 处于试运行 (Dry Run) 模式，不会修改任何文件 ---\n")

    total_files, updated_files = 0, 0
    details_cache = {}

    def find_best_match(local_artist, local_title, results):
        local_artist_norm, local_title_norm = local_artist.lower(), local_title.lower()
        for song in results:
            netease_artist = song.get('artist', '').lower()
            netease_title = song.get('name', '').lower()
            if local_artist_norm in netease_artist and local_title_norm in netease_title:
                return song
        return None

    for file in music_path.rglob('*'):
        if file.suffix.lower() in ['.mp3', '.flac']:
            total_files += 1
            print(f"\n[{total_files}] 正在处理: {file.name}")

            existing_data = get_all_metadata(file)
            artist, title = existing_data.get('artist'), existing_data.get('title')

            if not artist or not title:
                print("  - 缺少歌手或歌曲名元数据，无法搜索，跳过。")
                continue

            # 检查是否所有字段都已存在 (除了特殊的'pop'流派)
            fields_to_check = ['composer', 'lyricist', 'arranger', 'producer', 'mix', 'mastering', 'bpm']
            missing_fields = [f for f in fields_to_check if not existing_data.get(f)]
            if not missing_fields and (existing_data.get('genre') and existing_data.get('genre').lower() != 'pop'):
                 print("  - 所有元数据均已存在，跳过。")
                 continue
            
            search_artist = artist
            if "G.E.M. 邓紫棋" in artist: search_artist = "邓紫棋"
            if "周杰伦" in artist:
                print("  - 检测到歌手为'周杰伦'，根据规则跳过。")
                continue
            
            search_keyword = f"{search_artist} - {title}"
            
            # 步骤 1: 检查或填充缓存
            netease_details = details_cache.get(search_keyword)
            if netease_details is None:
                print(f"  - 正在搜索: '{search_keyword}'")
                search_results = netease_api.search_song(search_keyword, limit=5)
                if not search_results:
                    print("  - 未在网易云找到任何结果，跳过。")
                    details_cache[search_keyword] = {} # 记录空结果
                    time.sleep(1)
                    continue
                
                best_match = find_best_match(search_artist, title, search_results)
                if not best_match or not best_match.get('id'):
                    print("  - 未在搜索结果中找到精确匹配项，跳过。")
                    details_cache[search_keyword] = {}
                    time.sleep(1)
                    continue

                song_id = best_match.get('id')
                print(f"  - 找到精确匹配 (ID: {song_id})，正在调用百科接口...")
                wiki_details = netease_api._get_song_wiki_details(str(song_id))
                netease_details = wiki_details
                details_cache[search_keyword] = netease_details # 存入缓存
                time.sleep(1.5)
            else:
                 print(f"  - 命中缓存: '{search_keyword}'")

            # 步骤 2: 逐项比对，构建需要写入的数据字典
            if not netease_details:
                print("  - 未能获取到任何网易云元数据，跳过。")
                continue

            data_to_write = {}
            # 优先使用百科的流派
            netease_genre = netease_details.get('genre_from_wiki')
            if netease_genre and (not existing_data.get('genre') or existing_data.get('genre').lower() == 'pop'):
                if not (existing_data.get('genre') and existing_data.get('genre').lower() == 'pop' and netease_genre.lower() == 'pop'):
                    data_to_write['genre'] = netease_genre
            
            # 检查其他字段
            for field in ['lyricist', 'composer', 'arranger', 'producer', 'mix', 'mastering', 'bpm']:
                if not existing_data.get(field) and netease_details.get(field):
                    data_to_write[field] = netease_details[field]

            # 步骤 3: 如果有需要写入的数据，则执行写入
            if data_to_write:
                if dry_run:
                    print(f"  - [试运行] 将会写入 {len(data_to_write)} 项新元数据: {data_to_write}")
                    updated_files += 1
                else:
                    if write_metadata(file, data_to_write):
                        updated_files += 1
            else:
                print("  - 无需补充新的元数据。")

    print(f"\n扫描完成！共处理 {total_files} 个音乐文件，更新了 {updated_files} 个文件。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="扫描本地音乐库，并使用网易云音乐数据补充缺失的元数据。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "directory",
        nargs='?',
        default=None,
        help="要扫描的特定音乐目录路径。\n如果未提供此参数，脚本将自动扫描配置文件中定义的\nMUSIC_DIRECTORY 和 FLAC_DIRECTORY (如果存在)。"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="试运行模式，只打印将要执行的操作，不实际修改任何文件。"
    )
    args = parser.parse_args()

    if args.directory:
        print(f"--- 扫描指定目录: {args.directory} ---")
        enhance_metadata(target_directory=args.directory, dry_run=args.dry_run)
    else:
        print("--- 未指定目录，将扫描配置文件中的默认目录 ---")
        
        scanned_any = False
        if hasattr(Config, 'MUSIC_DIRECTORY') and Config.MUSIC_DIRECTORY:
            print(f"\n[1/2] 开始扫描主音乐目录: {Config.MUSIC_DIRECTORY}")
            enhance_metadata(target_directory=Config.MUSIC_DIRECTORY, dry_run=args.dry_run)
            scanned_any = True
        else:
            print("\n[1/2] 未在 core/config.py 中配置 MUSIC_DIRECTORY，跳过主目录扫描。")

        if hasattr(Config, 'FLAC_DIRECTORY') and Config.FLAC_DIRECTORY:
            print(f"\n[2/2] 开始扫描FLAC备用目录: {Config.FLAC_DIRECTORY}")
            enhance_metadata(target_directory=Config.FLAC_DIRECTORY, dry_run=args.dry_run)
            scanned_any = True
        else:
            print("\n[2/2] 未在 core/config.py 中配置 FLAC_DIRECTORY，跳过备用目录扫描。")
            
        if not scanned_any:
            print("\n错误: 配置文件中未定义任何音乐目录，也未通过命令行指定，无法执行扫描。")

