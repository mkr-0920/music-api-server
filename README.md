# 🎵 全能音乐API服务器

> 一个集成了Web界面、智能工具、AI 音轨分离和多源音乐API（本地、QQ音乐、网易云音乐）的私有化解决方案。

为您的音乐管理和播放提供强大而统一的后台，支持：
- 智能搜索与下载
- AI 伴奏无损提取 (基于 BS Roformer)
- 批量下载
- 元数据自动补全与克隆
- 文件自动整理与分流
- 歌单自动同步
- Cookie 续期
- 密钥保护

---

## ✨ 核心特性

### 🔗 多源聚合
无缝整合三大音乐来源与先进 AI 算力：
- 本地音乐库 (由 `scanner.py` 索引)
- QQ 音乐
- 网易云音乐
- MVSep AI 集群 (提供音轨分离算力)

---

### 🖥️ Web 操作界面

#### `instrumental_maker.html` — 🎙️ 伴奏批量提取工坊
- 基于 **BS Roformer 2025.07** 模型，从本地无损曲库一键抽取纯净伴奏。
- 支持多选批量提交，后台自动排队、分发、下载、物理克隆原曲元数据（含歌词）并入库。
- 前端实时监控排队、AI 计算、合并、下载等状态流转。

#### `music_parser.html` — 主操作面板
图形化界面，支持通过以下方式调用所有 API 功能：
- **单曲解析**: 输入歌曲 ID/MID，获取信息与播放链接。
- **后台批量下载**: 输入歌单/专辑 ID，触发后台批量下载。
- **关键词搜索**: 跨平台搜索。

#### `delete_songs.html` — 本地库管理后台
- 浏览本地歌曲库，支持实时关键词筛选。
- 复选框批量选择，安全删除（数据库 + 硬盘同步）。

#### `playlist_importer.html` — Navidrome 歌单导入
- 将在线歌单（QQ音乐/网易云）一键导入您的 Navidrome。
- 自动在本地库中匹配歌曲，并创建同名歌单。
- 自动在数据库中创建**映射关系**，为歌单同步做准备。

#### `netease_crypto_tool.html` — 开发者工具
- 用于调试网易云 EAPI 接口。
- 支持**加密**请求体 (Payload -> params)。
- 支持**解密**请求体 (params -> Payload)。
- 支持**解密**响应体 (Response -> JSON)。

---

### 📥 歌曲下载
- 在`config.py`中通过 `DOWNLOADS_ENABLED` 开启或关闭。
- `ENABLE_MASTER_DOWNLOAD` 开启或关闭master音质下载。
- `ENABLE_FLAC_DOWNLOAD` 开启或关闭无损音质下载。
- `ENABLE_LOSSY_DOWNLOAD` 是否允许有损兜底 (如果设为 True，当且仅当音乐平台没有高音质，本地库中没有任何 MP3 时，自动拉取 320k，没有 320k 则拉取 128k)
- 支持通过 **歌单 ID** 或 **专辑 ID** 异步加入后台下载队列。

---

### 🏷️ 元数据自动补全

#### 下载时写入
在线歌曲下载时，自动抓取并嵌入多达 **14 项元数据**，包括：
- 标题, 艺术家, 专辑, 专辑艺术家
- 作词, 作曲, 编曲, 制作人, 混音, 母带
- 发行年份, 曲风, BPM, 封面

#### 伴奏克隆注入
AI 分离出伴奏后，系统会**原封不动地将原唱的全部元数据克隆进伴奏文件**。

#### 本地库增强 (`metadata_enhancer.py`)
扫描现有本地音乐库，使用网易云数据智能补全缺失字段（如“曲风”）。
```bash
python metadata_enhancer.py --dry-run   # 预览改动
python metadata_enhancer.py           # 执行写入
```

---

### 🛡️ 健壮性设计

| 功能 | 说明 |
|---|---|
| 自动重试 | 下载失败自动重试，解决临时网络问题 |
| 独立代理通道 | MVSep 等海外接口拥有专属 httpx 代理通道，国内音乐源保持直连，防止触发异地风控 |
| 失败日志 | 记录无法下载的歌曲，便于后续处理 |
| Cookie 自动续期 | 内置定时任务刷新 QQ 音乐 Cookie，一次配置长期有效 |
| API 密钥保护 | 所有接口需携带 `X-API-Key` 请求头或 `api_key` 参数认证 |

---

## 🚀 快速开始

### 1. 克隆项目 & 配置

```bash
git clone http://github.com/mkr-0920/music-api-server.git
cd music-api-server
cp core/config.py.template core/config.py
nano core/config.py
```

> ⚠️ **请务必填写以下关键配置项：**
>
>   - `API_SECRET_KEY`
>   - `QQ_USER_CONFIG`
>   - `NETEASE_USERS`  *(支持网易云多用户 Cookie 隔离配置)*
>   - `MVSEP_API_KEY`  *(AI 伴奏提取所需)*
>   - `MASTER_DIRECTORY`
>   - `FLAC_DIRECTORY`
>   - `INSTRUMENTAL_DIRECTORY`

---

### 2. 安装依赖

```bash
# （推荐）创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

---

### 3. 初始化数据库

如果本地已有歌曲，首次运行前请执行：

```bash
python scanner.py
```

> 此脚本会扫描 `MASTER_DIRECTORY` 和 `FLAC_DIRECTORY`，并将信息存入数据库。

---

### 4. 启动服务

```bash
uvicorn main:app --host 0.0.0.0 --port 5000
```

---

## 🛠️ 命令行工具

### `scanner.py` — 扫描与索引
```bash
python scanner.py
```
> 扫描音乐目录并更新数据库。

### `metadata_enhancer.py` — 元数据增强
```bash
python metadata_enhancer.py --dry-run   # 预览
python metadata_enhancer.py           # 写入
```
> 使用网易云数据补全本地库缺失元数据（如“曲风”）。

### `delete_songs.py` — 歌曲删除工具
```bash
python delete_songs.py
```
> 交互式 CLI，安全删除一首或多首歌曲（数据库 + 硬盘）。

### `playlist_sync.py` — Navidrome 歌单同步
```bash
# 示例：同步所有已映射的歌单
python playlist_sync.py --navidrome-url http://... --username ... --password ... --all
  
# 示例：只同步映射ID为 1 的歌单
python playlist_sync.py --navidrome-url http://... --username ... --password ... --id 1
```
> 检查已导入的在线歌单是否有更新，并自动将变动（新增、移除和顺序变更）同步到 Navidrome。

---

## 📖 API 使用说明

> 📌 **所有请求必须携带认证头：** > `X-API-Key: YOUR_SECRET_KEY`

---

### 1. AI 伴奏提取 API (MVSep)

#### a) 批量提交分离任务 `/api/instrumental/batch_submit`
```bash
curl -X POST -H "X-API-Key: YOUR_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"song_ids": [21, 22, 23]}' \
  "http://127.0.0.1:5000/api/instrumental/batch_submit"
```

#### b) 获取队列与处理状态 `/api/instrumental/queue_status`
```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/instrumental/queue_status"
```

---

### 2. 在线音乐 API (`/api/qq`, `/api/netease`)

**方法：** `GET`  
**描述：** 搜索、获取详情、触发批量下载

#### 核心参数

| 参数 | 平台 | 说明 |
|---|---|---|
| `q` | 通用 | 关键词，格式：`歌手 - 歌曲名` |
| `album` | 通用 | 专辑名，用于精确过滤搜索 |
| `id` / `mid` | QQ音乐 | songid（数字）或 songmid |
| `id` | 网易云 | 歌曲 id |
| `playlist_id` | 通用 | 歌单 ID，触发后台批量下载 |
| `album_id` | 通用 | 专辑 ID，触发后台批量下载 |
| `album_mid` | qq音乐 | 专辑 MID，触发后台批量下载 |
| `level` | 网易云 | 音质等级（如 `hires`） |

#### 示例请求

##### 🔍 按关键词搜索
```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=周杰伦 - 稻香" \
  "http://127.0.0.1:5000/api/qq"
```

##### 🎵 按 ID 获取单曲
```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/netease?id=191179"
```

##### 📥 按 ID 下载歌单
```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/netease?playlist_id=8473556052"
```

##### 📦 按 ID 下载专辑
```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/qq?album_id=8218"

curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/qq?album_mid=003DF0bQ31w25h"
```

---

### 3. 本地音乐 API

#### a) 搜索本地歌曲 `/api/local/search`
```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=周杰伦 - 可爱女人" \
  "http://127.0.0.1:5000/api/local/search"
```

#### b) 获取流媒体链接 `/api/local/stream_url/<id>`
```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/local/stream_url/123"
```

#### c) 列出所有本地歌曲 `/api/local/list`
```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/local/list"
```

#### d) 删除本地歌曲 `/api/local/delete`
```bash
curl -X POST -H "X-API-Key: YOUR_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"ids": [123, 456]}' \
  "http://127.0.0.1:5000/api/local/delete"
```

---

### 4. 歌单导入 API

#### a) 获取在线歌单信息 `/api/playlist/info`
```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "platform=netease" \
  --data-urlencode "id=8473556052" \
  "http://127.0.0.1:5000/api/playlist/info"
```

#### b) 导入歌单到 Navidrome `/api/navidrome/import`
```bash
curl -X POST -H "X-API-Key: YOUR_SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{
        "navidrome_url": "http://127.0.0.1:4533",
        "username": "YOUR_NAVI_USER",
        "password": "YOUR_NAVI_PASSWORD",
        "platform": "netease",
        "online_playlist_id": "8473556052"
      }' \
  "http://127.0.0.1:5000/api/navidrome/import"
```