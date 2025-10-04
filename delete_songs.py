import sqlite3
import os
import argparse

def list_all_songs(cursor):
    """查询并打印数据库中的所有歌曲。"""
    cursor.execute("SELECT id, search_key, album, quality, file_path FROM songs ORDER BY id")
    songs = cursor.fetchall()
    
    if not songs:
        print("数据库中没有任何歌曲记录。")
        return None
        
    print("--- 本地音乐库歌曲列表 ---")
    print(f"{'ID':<5} | {'Search Key':<40} | {'Album':<30} | {'Quality':<8} | {'File Path':<50}")
    print("-" * 140)
    
    for song in songs:
        search_key = (song[1] or '')[:38] + '..' if len(song[1] or '') > 40 else (song[1] or '')
        album = (song[2] or '')[:28] + '..' if len(song[2] or '') > 30 else (song[2] or '')
        file_path = (song[4] or '')[:48] + '..' if len(song[4] or '') > 50 else (song[4] or '')
        
        print(f"{song[0]:<5} | {search_key:<40} | {album:<30} | {song[3]:<8} | {file_path:<50}")
    
    print("-" * 140)
    return songs

def get_ids_from_user():
    """获取并解析用户输入的歌曲ID。"""
    ids_str = input("\n请输入一个或多个要删除的歌曲ID (用空格或逗号分隔), 或直接按回车退出: ")
    if not ids_str.strip():
        return []
    
    ids_raw = ids_str.replace(',', ' ').split()
    
    valid_ids = []
    invalid_inputs = []
    for item in ids_raw:
        try:
            valid_ids.append(int(item))
        except ValueError:
            invalid_inputs.append(item)
            
    if invalid_inputs:
        print(f"\n警告: 以下输入不是有效的数字ID，将被忽略: {', '.join(invalid_inputs)}")
        
    return valid_ids

def main(db_file):
    """主函数，执行删除流程。"""
    if not os.path.exists(db_file):
        print(f"错误: 数据库文件 '{db_file}' 不存在。请确保脚本在正确的目录下运行，或使用 --db 参数指定路径。")
        return

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # 列出所有歌曲
    songs = list_all_songs(cursor)
    if not songs:
        conn.close()
        return

    # 获取用户输入
    ids_to_delete = get_ids_from_user()
    if not ids_to_delete:
        print("没有输入任何ID，程序退出。")
        conn.close()
        return

    # 查找并确认要删除的歌曲
    cursor.execute(f"SELECT id, search_key, file_path FROM songs WHERE id IN ({','.join('?' for _ in ids_to_delete)})", ids_to_delete)
    songs_to_delete = cursor.fetchall()

    if not songs_to_delete:
        print("\n根据您输入的ID，没有找到任何可以删除的歌曲。")
        conn.close()
        return
        
    print("\n--- 将要删除以下歌曲记录和对应的文件 ---")
    for song in songs_to_delete:
        print(f"  - ID: {song[0]}, 歌曲: {song[1]}, 文件: {song[2]}")
    
    confirm = input("\n确认删除以上所有内容吗？此操作不可恢复！(输入 y 确认): ")
    
    if confirm.lower() != 'y':
        print("操作已取消。")
        conn.close()
        return

    # 执行删除操作
    deleted_count = 0
    for song_id, search_key, file_path in songs_to_delete:
        try:
            cursor.execute("DELETE FROM songs WHERE id = ?", (song_id,))
            
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"  - [成功] 已删除文件: {file_path}")
            else:
                print(f"  - [警告] 文件不存在，仅删除数据库记录: {file_path}")
                
            print(f"  - [成功] 已从数据库删除记录: '{search_key}' (ID: {song_id})")
            deleted_count += 1
        except Exception as e:
            print(f"  - [失败] 删除歌曲 (ID: {song_id}) 时发生错误: {e}")
            conn.rollback()
            break

    if deleted_count == len(songs_to_delete):
        conn.commit()
        print(f"\n操作完成！共成功删除了 {deleted_count} 首歌曲。")
    else:
        print("\n操作因错误中断，所有更改已被回滚。")

    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从数据库和文件系统中删除歌曲。")
    parser.add_argument(
        '--db',
        default='music_library.db',
        help="数据库文件的路径 (默认为当前目录下的 'music_library.db')"
    )
    args = parser.parse_args()

    main(db_file=args.db)

