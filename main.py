import urllib.parse
from functools import wraps
from flask import Flask, request, jsonify

# 从我们创建的模块中导入所需的类
from core.config import Config
from api.netease import NeteaseMusicAPI
from api.qq import QQMusicAPI

# --------------------------------------------------------------------------
# 1. 初始化应用和API客户端
# --------------------------------------------------------------------------
app = Flask(__name__)
# 设置JSON响应确保中文字符正确显示
app.config['JSON_AS_ASCII'] = False

# 使用Config类中的配置来实例化API客户端
netease_api = NeteaseMusicAPI(Config.NETEASE_COOKIE_STR)
qq_api = QQMusicAPI(Config.QQ_COOKIE_DICT)

# --------------------------------------------------------------------------
# 2. 创建API密钥验证装饰器 (核心安全逻辑)
# --------------------------------------------------------------------------
def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 我们约定，前端请求时必须在请求头(Header)中包含一个名为 'X-API-Key' 的字段
        provided_key = request.headers.get('X-API-Key')
        if not provided_key or provided_key != Config.API_SECRET_KEY:
            # 如果密钥缺失或不正确，则返回 401 Unauthorized 错误
            return jsonify({"code": 401, "message": "认证失败：无效的API密钥。"}), 401
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------------------------------
# 3. 定义Flask路由，并应用装饰器
# --------------------------------------------------------------------------
@app.route('/')
def index():
    return "<h1>模块化音乐API服务器正在运行</h1><p>所有API端点都需要密钥认证。</p>"

@app.route('/api/netease', methods=['GET'])
@require_api_key  # <-- 为网易云路由加上“门锁”
def handle_netease_request():
    song_id = request.args.get('id')
    level = request.args.get('level', 'standard')

    if not song_id:
        return jsonify({"code": 400, "message": "缺少 'id' 参数。"}), 400
    
    # 智能解析URL
    if 'music.163.com' in song_id:
        try:
            # 注意: 网易云的ID可能在 hash 中, 需要特殊处理
            parsed_url = urllib.parse.urlparse(song_id)
            query_in_hash = urllib.parse.parse_qs(parsed_url.fragment)
            if 'id' in query_in_hash:
                 song_id = query_in_hash['id'][0]
            else: # 兼容直接 query 的情况
                 song_id = urllib.parse.parse_qs(parsed_url.query)['id'][0]
        except (KeyError, IndexError):
            return jsonify({"code": 400, "message": "提供的网易云URL无效。"}), 400

    # 调用NeteaseMusicAPI实例的方法
    data = netease_api.get_song_details(song_id, level)
    
    if "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
        
    return jsonify({"code": 200, "message": "成功", "data": data})


@app.route('/api/qq', methods=['GET'])
@require_api_key # <-- 同样为QQ音乐路由加上“门锁”
def handle_qq_request(): # <-- 修正函数名
    song_mid = request.args.get('mid')
    
    if not song_mid:
        return jsonify({"code": 400, "message": "缺少 'mid' (歌曲的mid) 参数。"}), 400
    
    # 智能解析URL
    if 'y.qq.com' in song_mid:
        try:
            path_parts = urllib.parse.urlparse(song_mid).path.split('/')
            # 查找看起来像 song mid 的部分
            song_mid = next(part for part in path_parts if part.endswith('.html') and len(part) > 15)[:-5]
        except (StopIteration, IndexError):
             # 兼容其他格式的URL
            try:
                song_mid = urllib.parse.parse_qs(urllib.parse.urlparse(song_mid).query)['id'][0]
            except (KeyError, IndexError):
                return jsonify({"code": 400, "message": "提供的QQ音乐URL无效。"}), 400

    # 调用QQMusicAPI实例的方法
    data = qq_api.get_song_details(song_mid)

    if "error" in data:
        return jsonify({"code": 404, "message": data["error"]}), 404
    
    return jsonify({"code": 200, "message": "成功", "data": data})

# --------------------------------------------------------------------------
# 4. 启动服务器
# --------------------------------------------------------------------------
if __name__ == '__main__':
    print(f"服务器启动于 http://{Config.HOST}:{Config.PORT}")
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG_MODE)
