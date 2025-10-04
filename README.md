# 🎵 全能音乐 API 服务器

> 一个集成了 Web 界面、智能工具和多源音乐 API（本地、QQ音乐、网易云音乐）的私有化解决方案。

为您的音乐管理和播放提供强大而统一的后台，支持：
- 智能搜索
- 批量下载
- 元数据自动补全与增强
- 文件自动整理
- Cookie 续期
- 密钥保护

---

## ✨ 核心特性

### 🔗 多源聚合
无缝整合三大音乐来源：
- 本地音乐库
- QQ 音乐
- 网易云音乐

---

### 🖥️ Web 操作界面

#### `music_parser.html` — 主操作面板
图形化界面，支持通过以下方式调用所有 API 功能：
- 单曲 ID / MID
- 歌单或专辑 ID
- 关键词搜索

#### `delete_songs.html` — 本地库管理后台
- 浏览本地歌曲库
- 实时关键词筛选
- 复选框批量选择
- 安全删除（数据库 + 硬盘同步）

---

### 📥 智能批量下载
- 在`config.py`开启或关闭，有`NAS`或是想自建音乐库的可以开启
- 支持通过 **歌单 ID** 或 **专辑 ID** 异步加入后台下载队列。
- **智能分层下载**：自动下载最高音质版本，按设置（如 `master` 优先）智能补充或跳过。

---

### 🏷️ 元数据自动补全

#### 下载时写入
在线歌曲下载时，自动抓取并嵌入多达 **14 项元数据**，包括：
- 标题
- 艺术家
- 作词
- 作曲
- 发行年份
- 专辑艺术家等

#### 本地库增强
独立脚本：`metadata_enhancer.py`  
扫描现有本地音乐库，使用网易云数据智能补全缺失字段（如“曲风”）。

```bash
python metadata_enhancer.py --dry-run    # 预览改动
python metadata_enhancer.py              # 执行写入
```

---

### 🗂️ 文件自动整理

#### 智能分流
- 若 `master` 版本已存在，`flac` 版本自动下载至备用目录。

---

### 🛡️ 健壮性设计

| 功能             | 说明 |
|------------------|------|
| 自动重试         | 下载失败自动重试，解决临时网络问题 |
| 失败日志         | 记录无法下载的歌曲，便于后续处理 |
| Cookie 自动续期  | 内置定时任务刷新 QQ 音乐 Cookie，一次配置长期有效 |
| API 密钥保护     | 所有接口需携带 `X-API-Key` 请求头认证 |

---

## 🚀 快速开始

### 1. 克隆项目 & 配置

```bash
git clone https://github.com/mkr-0920/music-api-server.git
cd music-api-server
cp core/config.py.template core/config.py
nano core/config.py
```

> ⚠️ **请务必填写以下关键配置项：**
> - `API_SECRET_KEY`
> - `QQ_USER_CONFIG`
> - `NETEASE_COOKIE_STR`
> - `MUSIC_DIRECTORY`
> - `FLAC_DIRECTORY`

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

首次运行或新增功能后，请执行：

```bash
python scanner.py
```

> 此脚本会扫描 `MUSIC_DIRECTORY` 和 `FLAC_DIRECTORY`，并将信息存入数据库。

---

### 4. 启动服务

```bash
python main.py
```

✅ 服务启动后访问：  
👉 `http://<你的服务器IP>:5000`

> 若已配置 Nginx，可直接访问你的域名。

---

## 🌐 Web 界面使用

### 🎛️ `music_parser.html`（主解析器）

支持功能：
- **单曲解析**：输入歌曲 ID/MID，获取信息与播放链接
- **ID 下载**：输入歌单/专辑 ID，触发后台批量下载
- **关键词搜索**：跨平台搜索并自动下载最高音质版本

---

### 🗑️ `delete_songs.html`（本地库管理）

- 完整本地音乐库视图
- 实时关键词筛选
- 复选框批量操作
- 安全删除（数据库 + 文件系统同步）

---

## 🛠️ 命令行工具

### `scanner.py` — 扫描与索引

```bash
python scanner.py
```

> 扫描音乐目录并更新数据库。

---

### `metadata_enhancer.py` — 元数据增强

```bash
python metadata_enhancer.py --dry-run    # 预览
python metadata_enhancer.py              # 写入
```

> 使用网易云数据补全本地库缺失元数据（如“曲风”）。

---

### `separate_flac.py` — FLAC 分离整理

```bash
python separate_flac.py --dry-run    # 预览
python separate_flac.py              # 执行
```

> 若歌曲同时存在 `master` 和 `flac`，自动将 `flac` 移至备用目录并更新数据库。

---

### `delete_songs.py` — 歌曲删除工具

```bash
python delete_songs.py
```

> 交互式 CLI，安全删除一首或多首歌曲（数据库 + 硬盘）。

---

## 📖 API 使用说明

> 📌 **所有请求必须携带认证头：**  
> `X-API-Key: YOUR_SECRET_KEY`

---

### 1. 在线音乐 API（`/api/qq`, `/api/netease`）

**方法：** `GET`  
**描述：** 搜索、获取详情、触发批量下载

#### 核心参数

| 参数          | 平台       | 说明                     |
|---------------|------------|--------------------------|
| `q`           | 通用       | 关键词，格式：`歌手 - 歌曲名` |
| `album`       | 通用       | 专辑名，用于精确过滤搜索     |
| `id` / `mid`  | QQ音乐     | songid（数字）或 songmid     |
| `id`          | 网易云     | 歌曲 id                   |
| `playlist_id` | 通用       | 歌单 ID，触发批量下载        |
| `album_id`    | 通用       | 专辑 ID，触发批量下载        |
| `level`       | 网易云     | 音质等级（如 `hires`）       |

---

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

##### 📥按 ID 下载歌单

```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/netease?playlist_id=8473556052"
```

##### 📦按 ID 下载专辑

```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/qq?album_id=003DF0bQ31w25h"
```

---

### 2. 本地音乐 API

#### a) 搜索本地歌曲 `/api/local/search`

```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=周杰伦 - 可爱女人 \
  "http://127.0.0.1:5000/api/local/search"
```

#### b) 下载本地歌曲 `/api/local/download/<id>`


```bash
curl -L -o "song.flac" "http://127.0.0.1:5000/api/local/download/123"
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
  "http://127.0.0.1:5000/api/local/delete
```
---

