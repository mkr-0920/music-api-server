import argparse
import sys
import os
import asyncio
import datetime

# 确保能从项目根目录正确导入模块
try:
    from api.netease import NeteaseMusicAPI
    from api.qq import QQMusicAPI
    from api.navidrome import NavidromeAPI
    from api.local import LocalMusicAPI
    from core.config import Config
except ImportError:
    print("错误: 无法导入项目模块。请确保此脚本与您的项目结构在同一根目录下。")
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    try:
        from api.netease import NeteaseMusicAPI
        from api.qq import QQMusicAPI
        from api.navidrome import NavidromeAPI
        from api.local import LocalMusicAPI
        from core.config import Config
    except ImportError as e:
        print(f"再次尝试导入失败: {e}")
        sys.exit(1)

async def get_online_playlist(platform, playlist_id):
    """通过API异步获取在线歌单的最新歌曲列表"""
    print(f"正在从 {platform.upper()} 获取歌单 {playlist_id} 的最新信息...")
    api = None
    if platform == 'netease':
        # 实例化API客户端
        api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, None, None, None)
        data = await api.get_playlist_info(playlist_id)
    elif platform == 'qq':
        api = QQMusicAPI(None, None, None)
        data = await api.get_playlist_info(playlist_id)
    else:
        return None, None
        
    if not api or "error" in data:
        print(f"✗ 获取在线歌单失败: {data.get('error', '未知错误')}")
        return None, None
        
    return data.get('playlist_name'), data.get('songs')

async def sync_playlist(mapping, navi_api, local_api):
    """执行单个歌单的同步逻辑，包括增删和排序。"""
    mapping_id, platform, online_id, navidrome_id, playlist_name, _ = mapping
    print(f"\n--- 开始同步歌单: '{playlist_name}' ({platform}: {online_id}) ---")
    
    # 1. 获取最新的在线歌单 (有序)
    _, online_songs = await get_online_playlist(platform, online_id)
    if not online_songs:
        print("无法获取在线歌单，跳过同步。")
        return

    # 2. 在Navidrome中匹配在线歌曲，得到一个有序的“目标ID列表”
    print("正在匹配歌曲以生成目标顺序...")
    target_ordered_ids = []
    
    # 并发搜索所有歌曲
    search_tasks = [navi_api.search_song(s.get('artist'), s.get('name'), s.get('album')) for s in online_songs if s.get('artist') and s.get('name')]
    search_results = await asyncio.gather(*search_tasks)
    
    for song_id in search_results:
        if song_id:
            target_ordered_ids.append(song_id)

    # 3. 获取Navidrome中已有的歌曲
    print("正在获取 Navidrome 中当前歌单的歌曲...")
    current_navi_track_ids = await navi_api.get_playlist_tracks(navidrome_id)
    if current_navi_track_ids is None:
        print("无法获取 Navidrome 歌单内容，跳过同步。")
        return
        
    # 4. 计算差异并执行添加/删除
    target_set = set(target_ordered_ids)
    current_set = set(current_navi_track_ids)
    
    songs_to_add = list(target_set - current_set)
    songs_to_remove = list(current_set - target_set)
    
    if songs_to_add:
        print(f"  -> 发现 {len(songs_to_add)} 首新歌，正在添加到 Navidrome...")
        await navi_api.add_songs_to_playlist(navidrome_id, songs_to_add)

    if songs_to_remove:
        print(f"  -> 发现 {len(songs_to_remove)} 首已移除歌曲，正在从 Navidrome 删除...")
        await navi_api.remove_songs_from_playlist(navidrome_id, songs_to_remove)

    # 5. 执行排序逻辑
    # 获取执行完增删操作后的最新列表
    current_order = await navi_api.get_playlist_tracks(navidrome_id)
    if current_order is None:
        print("✗ 无法获取更新后的歌单顺序，排序失败。")
        return

    if current_order == target_ordered_ids:
         print("✓ 歌单已是最新，且顺序正确，无需同步。")
         local_api.update_sync_time(navidrome_id)
         return

    print("正在同步歌曲顺序...")
    # 反向迭代进行排序
    for i in range(len(target_ordered_ids) - 2, -1, -1):
        track_to_move = target_ordered_ids[i]
        insert_before_id = target_ordered_ids[i+1]
        
        # 仅当需要移动时才发起API请求
        try:
            current_index = current_order.index(track_to_move)
            target_index = current_order.index(insert_before_id)
            if current_index >= target_index:
                print(f"  - 正在移动歌曲 '{track_to_move}' 到 '{insert_before_id}' 的前面...")
                if not await navi_api.move_track_in_playlist(navidrome_id, track_to_move, insert_before_id):
                    print(f"  - ✗ 移动歌曲失败，排序中断。")
                    break
                # 更新本地顺序以反映移动
                current_order.insert(target_index, current_order.pop(current_index))
                await asyncio.sleep(0.5)
        except (ValueError, IndexError) as e:
             print(f"  - ✗ 排序时发生错误 (歌曲可能未正确添加/移除): {e}")
             break
        
    local_api.update_sync_time(navidrome_id)
    print(f"✓ 同步完成！新增 {len(songs_to_add)} 首，移除 {len(songs_to_remove)} 首，并已更新顺序。")

async def main():
    parser = argparse.ArgumentParser(description="同步已映射的在线歌单到Navidrome。")
    parser.add_argument("--navidrome-url", required=True, help="您的Navidrome服务地址。")
    parser.add_argument("--username", required=True, help="Navidrome的登录用户名。")
    parser.add_argument("--password", required=True, help="Navidrome的登录密码。")
    parser.add_argument("--all", action='store_true', help="同步所有已映射的歌单。")
    parser.add_argument("--id", type=int, help="只同步指定映射ID的歌单。")
    args = parser.parse_args()
    
    local_api = LocalMusicAPI(Config.DATABASE_FILE)
    conn = local_api._get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, platform, online_playlist_id, navidrome_playlist_id, playlist_name, last_sync_time FROM playlist_mappings")
    all_mappings = cursor.fetchall()
    
    if not all_mappings:
        print("数据库中没有任何歌单映射关系，无法同步。")
        conn.close()
        return

    print("--- 已保存的歌单映射 ---")
    for m in all_mappings:
        sync_time_str = m[5]
        sync_time = "从未"
        if sync_time_str:
            try:
                sync_time = datetime.datetime.fromisoformat(sync_time_str).strftime('%Y-%m-%d %H:%M:%S')
            except:
                 sync_time = "格式错误"
        print(f"  ID: {m[0]} | 平台: {m[1]:<8} | 名称: {m[3]:<30} | 上次同步: {sync_time}")

    mappings_to_sync = []
    if args.all:
        mappings_to_sync = all_mappings
    elif args.id:
        selected = next((m for m in all_mappings if m[0] == args.id), None)
        if selected:
            mappings_to_sync.append(selected)
        else:
            print(f"错误: 未找到映射ID为 {args.id} 的记录。")
    else:
        print("\n请使用 --all 参数同步所有歌单，或使用 --id <映射ID> 同步单个歌单。")
        conn.close()
        return
        
    navi_api = NavidromeAPI(args.navidrome_url, args.username, args.password)
    if not await navi_api.login():
        print("任务终止。")
        conn.close()
        return
        
    for mapping in mappings_to_sync:
        await sync_playlist(mapping, navi_api, local_api)
        
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())

