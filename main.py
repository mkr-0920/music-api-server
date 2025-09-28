import os
import urllib.parse
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
# 1. 初始化应用和所有API客户端
# --------------------------------------------------------------------------
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False

# 实例化所有API客户端
kuwo_api = KuwoMusicAPI()
local_api = LocalMusicAPI(Config.DATABASE_FILE)
netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR, local_api, Config.MUSIC_DIRECTORY)
qq_api = QQMusicAPI(local_api, Config.MUSIC_DIRECTORY)

# --------------------------------------------------------------------------
# 2. API密钥验证装饰器
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
# 3. 定义所有Flask路由
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
    album = request.args.get('album') # 获取可选的专辑参数
    level = request.args.get('level', 'hires') # 默认音质为hires

    if song_id:
        data = netease_api.get_song_details(song_id, level)
    elif keyword:
        # 调用新的搜索并获取详情函数
        data = netease_api.search_and_get_details(keyword, level, album)
    else:
        return jsonify({"code": 400, "message": "必须提供 'id' 或 'q' 参数。"}), 400

    if "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
    return jsonify({"code": 200, "message": "成功", "data": data})

@app.route('/api/qq', methods=['GET'])
@require_api_key
def handle_qq_request():
    """
    处理来自QQ音乐的请求。
    支持通过 'mid' 直接获取详情，或通过 'q' (关键词) 和可选的 'album' (专辑)进行搜索。
    """
    song_mid = request.args.get('mid')
    keyword = request.args.get('q')
    album = request.args.get('album') # 新增：获取album参数

    # 优先处理 mid 参数
    if song_mid:
        data = qq_api.get_song_details(song_mid)
    # 如果没有 mid，再处理 q 参数
    elif keyword:
        # 修改：将album参数传递给后端的API方法
        data = qq_api.search_and_get_details(keyword, album=album)
    # 如果两个参数都没有，则返回错误
    else:
        return jsonify({"code": 400, "message": "必须提供 'mid' 或 'q' (关键词) 参数。"}), 400
    
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

# --- 本地音乐API路由 ---
@app.route('/api/local/search')
@require_api_key
def handle_local_search():
    """根据 '歌手 - 歌名' 搜索本地音乐"""
    query = request.args.get('q')
    album = request.args.get('album')
    quality = request.args.get('quality')
    if not query:
        return jsonify({"error": "缺少查询参数 'q'"}), 400
    
    data = local_api.search_song(query, album=album, quality=quality)
    if not data:
        return jsonify({"error": f"未在本地库中找到歌曲: '{query}'"}), 404
    
    return jsonify({"code": 200, "message": "成功", "data": data})

@app.route('/api/local/download/<int:song_id>')
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
    # --- 修正结束 ---
    
    return response


# 4.--- 定时任务 ---
scheduler = APScheduler()

def refresh_qq_cookie_job():
    # 在应用上下文中执行，确保访问app.config等
    with app.app_context():
        refresher = QQCookieRefresher()
        refresher.refresh()
# --------------------------------------------------------------------------
# 5. 启动服务器
# --------------------------------------------------------------------------
if __name__ == '__main__':
    # 手动调用一次，确保启动时立即刷新
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

