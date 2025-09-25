# 🚀 快速开始
## 1. 克隆项目
```bash
git clone https://github.com/mkr-0920/music-api-server.git
cd music-api-server
```

## 2. 配置环境
您需要创建自己的配置文件。
```bash
# 1. 从模板复制一份配置文件
cp core/config.py.template core/config.py

# 2. 编辑新的配置文件
nano core/config.py
```
在 core/config.py 文件中，**填入您自己的 API密钥** 和 **音乐平台的Cookie**s。

## 3. 安装依赖
建议在Python虚拟环境中安装。

创建虚拟环境 (可选)
```bash
python3 -m venv venv
source venv/bin/activate
```
安装所有依赖库
```bash
pip install -r requirements.txt
```
## 4. 启动服务器
```bash
python main.py
```
服务器将在 http://0.0.0.0:5000 上启动。

# 📖 API 使用说明
所有API请求都需要通过请求头（Header）进行认证。

 - 认证头: X-API-Key

 - 值: 您在 core/config.py 中设置的 API_SECRET_KEY

## 网易云音乐 (/api/netease)
 - 方法: GET

 - 参数:

    - id (必需): 歌曲的ID。

    - level (可选): 音质。可选值为 standard, exhigh, lossless, hires, jyeffect, jymaster。**默认为 lossless**。

示例 (使用 curl):
```bash
curl -H "X-API-Key: YOUR_SUPER_SECRET_KEY_HERE" "http://127.0.0.1:5000/api/netease?id=191179&level=lossless"
```
## QQ音乐 (/api/qq)
- 方法: GET

- 参数:

   - mid (必需): 歌曲的 Song MID。

   - level (可选): 音质。可选值为 flac, 320, 128。

示例 (使用 curl):
```bash
curl -H "X-API-Key: YOUR_SUPER_SECRET_KEY_HERE" "http://127.0.0.1:5000/api/qq?mid=002WCV372xJd69"
```
