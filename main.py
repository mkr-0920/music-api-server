import os
import time
import datetime
import hashlib
import base64
import urllib.parse
import sqlite3
from functools import wraps
from flask import Flask, request, jsonify, Response
from flask_apscheduler import APScheduler
import threading
from urllib.parse import quote

# 从我们创建的模块中导入所有API类
from core.config import Config
from api.netease import NeteaseMusicAPI
from api.qq import QQMusicAPI
from api.kuwo import KuwoMusicAPI
from api.local import LocalMusicAPI # <-- 导入本地API类

# qq音乐刷新cookies
from core.qq_refresh.refresher import QQCookieRefresher
# --------------------------------------------------------------------------
# 初始化应用和所有API客户端
# --------------------------------------------------------------------------
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 实例化所有API客户端
kuwo_api = KuwoMusicAPI()
local_api = LocalMusicAPI(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)
qq_api = QQMusicAPI(local_api, Config.MUSIC_DIRECTORY, Config.FLAC_DIRECTORY)

# --------------------------------------------------------------------------
# API密钥验证装饰器
# --------------------------------------------------------------------------
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        provided_key = request.headers.get('X-API-Key')
        if not hasattr(Config, 'API_SECRET_KEY') or not provided_key or provided_key != Config.API_SECRET_KEY:
            return jsonify({"code": 401, "message": "认证失败：无效的API密钥。"}), 401
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------------------------------
# 定义所有Flask路由
# --------------------------------------------------------------------------
@app.route('/')
def index():
    return "<h1>全能音乐API服务器正在运行</h1>"

# --- 在线音乐API路由 ---
@app.route('/api/netease', methods=['GET'])
@require_api_key
def handle_netease_request():
    song_id = request.args.get('id')
    keyword = request.args.get('q')
    album_search = request.args.get('album')
    level = request.args.get('level', 'hires')
    playlist_id = request.args.get('playlist_id')
    album_id = request.args.get('album_id')

    if playlist_id:
        # 调用“启动器”函数，它会立即返回
        netease_api.start_background_playlist_download(playlist_id, level)
        # 立即返回“任务已接受”响应
        return jsonify({"code": 202, "message": "任务已接受", "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"}}), 202
    
    elif album_id:
        # 调用“启动器”函数，它会立即返回
        netease_api.start_background_album_download(album_id, level)
        # 立即返回“任务已接受”响应
        return jsonify({"code": 202, "message": "任务已接受", "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"}}), 202

    elif song_id:
        data = netease_api.get_song_details(song_id, level)
    elif keyword:
        data = netease_api.search_and_get_details(keyword, level, album_search)
    else:
        return jsonify({"code": 400, "message": "必须提供 'id', 'q', 'playlist_id' 或 'album_id' 参数之一。"}), 400

    if "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
    
    return jsonify({"code": 200, "message": "成功", "data": data})


@app.route('/api/qq', methods=['GET'])
@require_api_key
def handle_qq_request():
    """
    处理来自QQ音乐的请求。
    支持通过 'playlist_id' 或 'album_id' 触发后台批量下载。
    也支持通过 'mid' 直接获取详情，或通过 'q' (关键词) 和可选的 'album' (专辑)进行搜索。
    """
    song_mid = request.args.get('mid')
    song_id = request.args.get('id')
    keyword = request.args.get('q')
    album_search = request.args.get('album')
    
    playlist_id = request.args.get('playlist_id')
    album_id = request.args.get('album_id')
    
    # level 参数在此处仅为保持API格式统一，后端逻辑会自动降级查找
    level = request.args.get('level', 'master') 
    
    # 优先处理批量下载任务
    if playlist_id:
        # 调用异步启动器，此函数会立即返回
        qq_api.start_background_playlist_download(playlist_id, level)
        # 立刻返回“任务已接受”响应，不阻塞
        return jsonify({"code": 202, "message": "任务已接受", "data": {"message": f"歌单 {playlist_id} 已加入后台下载队列。"}}), 202
    
    elif album_id:
        # 调用异步启动器，此函数会立即返回
        qq_api.start_background_album_download(album_id, level)
        # 立刻返回“任务已接受”响应，不阻塞
        return jsonify({"code": 202, "message": "任务已接受", "data": {"message": f"专辑 {album_id} 已加入后台下载队列。"}}), 202
    
    data = None
    if song_id or song_mid:
        data = qq_api.get_song_details(song_mid=song_mid, song_id=song_id)
    elif keyword:
        data = qq_api.search_and_get_details(keyword, album=album_search)
    else:
        # 更新错误信息，告知用户所有可用参数
        return jsonify({"code": 400, "message": "必须提供 'mid', 'q', 'playlist_id' 或 'album_id' 参数之一。"}), 400
    
    # 统一处理返回结果
    if data and "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
    
    return jsonify({"code": 200, "message": "成功", "data": data})



@app.route('/api/kuwo', methods=['GET'])
@require_api_key
def handle_kuwo_request():
    keyword = request.args.get('keyword')
    if not keyword:
        return jsonify({"code": 400, "message": "缺少 'keyword' 参数。"}), 400
    
    data = kuwo_api.get_song_details(keyword)
    if "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
    return jsonify({"code": 200, "message": "成功", "data": data})

# 本地音乐API路由
@app.route('/api/local/search')
@require_api_key
def handle_local_search():
    """根据 '歌手 - 歌名' 搜索本地音乐，专辑参数可选"""
    query = request.args.get('q')
    album = request.args.get('album')
    quality = request.args.get('quality')
    if not query:
        return jsonify({"error": "缺少查询参数 'q'"}), 400
    
    data = local_api.search_song(query, album=album, quality=quality)
    if not data:
        return jsonify({"error": f"未在本地库中找到歌曲: '{query}'"}), 404
    
    return jsonify({"code": 200, "message": "成功", "data": data})

# 流媒体播放
@app.route('/api/local/stream_url/<int:song_id>')
@require_api_key
def generate_stream_url(song_id):
    """
    为一首本地歌曲生成一个临时的、安全的可流媒体播放链接。
    """
    file_path = local_api.get_song_path_by_id(song_id)
    if not file_path:
        return jsonify({"error": "无效的歌曲ID"}), 404

    # 1. 定义与 Nginx 配置中完全一致的密钥
    nginx_secret = "YOUR_NGINX_SECRET" # 必须与 nginx.conf 中的密钥相同

    # 2. 设置链接的有效期（例如，3小时后过期）
    expires = int(time.time()) + 3 * 3600  # 3 hours
    
    # 3. 构造 Nginx 需要的 URI
    uri_path = f"/secure_media{file_path}"

    # 4. 按照 Nginx 的规则生成 MD5 哈希
    # 格式: expires + uri + secret
    string_to_hash = f"{expires}{uri_path} {nginx_secret}"
    
    # 5. 计算 MD5 并进行 Base64 编码
    md5_hash = hashlib.md5(string_to_hash.encode('utf-8')).digest()
    secure_hash = base64.urlsafe_b64encode(md5_hash).decode('utf-8').replace('=', '')

    # 6. 组装最终的流媒体 URL
    stream_url = f"https://musicapi.010920.xyz{uri_path}?md5={secure_hash}&expires={expires}"
    
    return jsonify({
        "code": 200,
        "message": "成功",
        "data": {
            "url": stream_url,
            "expires_at": datetime.datetime.fromtimestamp(expires).isoformat()
        }
    })

@app.route('/api/local/download/<int:song_id>')
@require_api_key
def handle_local_download(song_id):
    """为Nginx提供文件路径以下载本地音乐"""
    file_path = local_api.get_song_path_by_id(song_id)
    if not file_path:
        return jsonify({"error": "无效的歌曲ID"}), 404

    filename = os.path.basename(file_path)
    response = Response()
    
    # 对 file_path 和 filename 都进行URL编码，确保所有HTTP头的值都是ASCII安全的
    encoded_file_path = quote(file_path)
    response.headers['X-Accel-Redirect'] = f'/internal_media/{encoded_file_path}'
    
    response.headers['Content-Type'] = 'application/octet-stream'
    
    encoded_filename = quote(filename)
    response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{encoded_filename}'
    
    return response

@app.route('/api/local/list', methods=['GET'])
@require_api_key
def list_local_songs():
    """获取本地数据库中所有歌曲的列表。"""
    try:
        conn = sqlite3.connect(Config.DATABASE_FILE)
        cursor = conn.cursor()
        # 查询所有需要的字段，并按ID排序
        cursor.execute("SELECT id, search_key, album, quality, file_path FROM songs ORDER BY id DESC")
        songs_tuples = cursor.fetchall()
        conn.close()
        # 将元组列表转换为字典列表，方便前端处理
        songs_list = [
            dict(id=s[0], search_key=s[1], album=s[2], quality=s[3], file_path=s[4])
            for s in songs_tuples
        ]
        return jsonify({"code": 200, "data": songs_list})
    except Exception as e:
        return jsonify({"code": 500, "message": f"读取数据库时出错: {e}"}), 500

@app.route('/api/local/delete', methods=['POST'])
@require_api_key
def delete_local_songs():
    """从数据库和文件系统中删除指定的歌曲。"""
    ids_to_delete = request.json.get('ids')
    if not ids_to_delete or not isinstance(ids_to_delete, list):
        return jsonify({"code": 400, "message": "请求体中必须包含一个有效的 'ids' 列表。"}), 400

    conn = sqlite3.connect(Config.DATABASE_FILE)
    cursor = conn.cursor()
    
    deleted_count = 0
    errors = []

    try:
        # 首先，获取要删除的歌曲的文件路径
        placeholders = ','.join('?' for _ in ids_to_delete)
        cursor.execute(f"SELECT id, file_path, search_key FROM songs WHERE id IN ({placeholders})", ids_to_delete)
        songs_to_delete = cursor.fetchall()

        for song_id, file_path, search_key in songs_to_delete:
            try:
                # 从文件系统删除
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                # 从数据库删除
                cursor.execute("DELETE FROM songs WHERE id = ?", (song_id,))
                deleted_count += 1
            except Exception as e:
                errors.append(f"删除歌曲 '{search_key}' (ID: {song_id}) 时出错: {e}")

        conn.commit()
    except Exception as e:
        conn.rollback()
        return jsonify({"code": 500, "message": f"数据库操作时发生严重错误: {e}"}), 500
    finally:
        conn.close()

    if errors:
        return jsonify({"code": 207, "message": f"操作部分成功，删除了 {deleted_count} 首歌曲。", "errors": errors}), 207

    return jsonify({"code": 200, "message": f"成功删除了 {deleted_count} 首歌曲。"})

# --- 定时任务 ---
scheduler = APScheduler()

def refresh_qq_cookie_job():
    # 在应用上下文中执行，确保访问app.config等
    with app.app_context():
        refresher = QQCookieRefresher()
        refresher.refresh()
# --------------------------------------------------------------------------
# 启动服务器
# --------------------------------------------------------------------------
if __name__ == '__main__':
    # # 手动调用一次，确保启动时立即刷新
    # print("Executing initial cookie refresh on startup...")
    # with app.app_context():
    #     refresh_qq_cookie_job()

    # 初始化调度器并添加任务
    scheduler.init_app(app)
    scheduler.add_job(id='RefreshQQCookie', func=refresh_qq_cookie_job, trigger='interval', hours=23)
    scheduler.start()
    print("qq音乐cookies定时刷新启动")

    print(f"服务器启动于 http://{Config.HOST}:{Config.PORT}")
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG_MODE)


