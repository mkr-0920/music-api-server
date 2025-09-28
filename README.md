
---

# 🎵 Music API Server — 多源聚合音乐 API 服务

> 一个集成本地、QQ音乐与网易云音乐的私有化统一 API 服务器。支持智能搜索、自动下载、Cookie 续期、密钥保护，为前端或播放器提供标准化音乐数据接口。

---

## ✨ 核心特性

- **多源聚合**  
  同时支持本地音乐库、QQ音乐、网易云音乐三大来源。

- **智能搜索**  
  支持按 `歌手 - 歌曲名` 模糊搜索，可附加 `专辑名` 精确过滤。

- **自动下载 & 入库**  
  在线平台搜索时，自动下载最高音质版本至本地目录，并写入元数据。

- **Cookie 自动续期**  
  内置定时任务自动刷新 QQ 音乐 Cookie，一次配置，长期有效。

- **统一响应结构**  
  无论数据来自哪个平台，均返回一致的 JSON 格式，便于前端调用。

- **API 密钥保护**  
  接口通过 `X-API-Key` 请求头认证，保障私有服务安全。

---

## 🚀 快速开始

### 1. 克隆项目

```bash
git clone https://github.com/mkr-0920/music-api-server.git
cd music-api-server
```

---

### 2. 配置环境

#### 复制配置模板：

```bash
cp core/config.py.template core/config.py
```

#### 编辑配置文件：

```bash
nano core/config.py
```

请务必填写以下关键配置项：

| 配置项             | 说明                         |
|--------------------|------------------------------|
| `API_SECRET_KEY`   | 用于 API 认证的密钥           |
| `QQ_USER_CONFIG`  | QQ音乐相关配置 |
| `NETEASE_COOKIE_STR`   | 网易云音乐 Cookies     |
| `MUSIC_DIRECTORY`  | 本地音乐文件存储路径          |

> 💡 建议使用虚拟环境隔离依赖（见下一步）

---

### 3. 安装依赖（推荐虚拟环境）

```bash
# 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

# 安装依赖
pip install -r requirements.txt
```

---

### 4. 启动服务

```bash
python main.py
```

✅ 服务启动后，默认监听地址：  
👉 `http://0.0.0.0:5000`

---

## 📖 API 使用说明

所有请求 **必须携带认证头**：

```http
X-API-Key: YOUR_SECRET_KEY
```

---

### 1. 本地音乐搜索 `/api/local/search`

**方法**：`GET`  
**描述**：在本地音乐库中搜索匹配的歌曲。

#### 参数：

| 参数     | 必需 | 说明                     |
|----------|------|--------------------------|
| `q`      | ✅   | 搜索关键词，格式：`歌手 - 歌曲名` |
| `album`  | ❌   | 专辑名称，用于精确匹配     |
| `quality`| ❌   | 音质过滤（如：lossless）   |

#### 示例：

```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=周杰伦 - 梯田" \
  --data-urlencode "album=叶惠美" \
  "http://127.0.0.1:5000/api/local/search"
```

---

### 2. QQ音乐搜索 `/api/qq`

**方法**：`GET`  
**描述**：通过歌曲 MID 或关键词搜索 QQ 音乐。

#### 参数：

| 参数     | 必需          | 说明                          |
|----------|---------------|-------------------------------|
| `mid`    | ✅（二选一）   | 歌曲的 Song MID               |
| `q`      | ✅（二选一）   | 搜索关键词，格式：`歌手 - 歌曲名` |
| `album`  | ❌            | 专辑名称，用于精确过滤         |

> ⚠️ `mid` 和 `q` 至少提供一个

#### 示例：

##### 按 MID 获取：

```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/qq?mid=002WCV372xJd69"
```

##### 按关键词搜索：

```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=周杰伦 - 稻香" \
  "http://127.0.0.1:5000/api/qq"
```

---

### 3. 网易云音乐搜索 `/api/netease`

**方法**：`GET`  
**描述**：通过歌曲 ID 或关键词搜索网易云音乐。

#### 参数：

| 参数     | 必需          | 说明                          |
|----------|---------------|-------------------------------|
| `id`     | ✅（二选一）   | 歌曲 ID                       |
| `q`      | ✅（二选一）   | 搜索关键词，格式：`歌手 - 歌曲名` |
| `album`  | ❌            | 专辑名称                      |
| `level`  | ❌            | 音质等级，默认 `lossless`<br>可选：`standard`, `exhigh`, `lossless`, `hires` |

> ⚠️ `id` 和 `q` 至少提供一个

#### 示例：

##### 按 ID 获取（指定音质）：

```bash
curl -H "X-API-Key: YOUR_SECRET_KEY" \
  "http://127.0.0.1:5000/api/netease?id=191179&level=hires"
```

##### 按关键词 + 专辑搜索：

```bash
curl -G -H "X-API-Key: YOUR_SECRET_KEY" \
  --data-urlencode "q=G.E.M.邓紫棋 - 龙卷风" \
  --data-urlencode "album=T-Time" \
  "http://127.0.0.1:5000/api/netease"
```

