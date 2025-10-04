# --- 1. 标准库导入 ---
import os
import json
import re
import base64
import datetime
import threading
import urllib.parse
from hashlib import md5
from random import randrange
import time

# --- 2. 第三方库导入 ---
import requests
from opencc import OpenCC

# mutagen (处理音乐元数据)
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, USLT, TIT2, TPE1, TALB, TPE2, TDRC, TRCK, TYER, TPOS, TCON, TPUB, TDOR, TCOM, TEXT, TPE4, TBPM, TXXX
from mutagen.flac import FLAC, Picture

# cryptography (处理网易云加密)
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# --- 3. 您自己的模块导入 ---
from utils.helpers import Utils
from core.config import Config

class APIConstants:
    """API相关常量"""
    AES_KEY = b"e82ckenh8dichen8"
    USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Safari/537.36 Chrome/91.0.4472.164 NeteaseMusicDesktop/8.9.75'
    DEFAULT_CONFIG = {"os": "pc", "appver": "8.9.75", "osver": "", "deviceId": "pyncm!"}
    SONG_URL_V1 = "https://interface3.music.163.com/eapi/song/enhance/player/url/v1"
    SONG_DETAIL_V3 = "https://interface3.music.163.com/api/v3/song/detail"
    LYRIC_API = "https://interface3.music.163.com/api/song/lyric"
    SEARCH_API = "https://interface.music.163.com/eapi/cloudsearch/pc"
    PLAYLIST_DETAIL_API = 'https://music.163.com/api/v6/playlist/detail'
    ALBUM_DETAIL_API = 'https://music.163.com/api/v1/album/'
    ALBUM_V3_DETAIL = "https://music.163.com/eapi/album/v3/detail"
    CACHE_KEY_AES_KEY = b')(13daqP@ssw0rd~'
    SONG_WIKI_API = "https://interface3.music.163.com/api/link/page/parent/relation/construct/info"

class NeteaseMusicAPI:
    def __init__(self, cookie_str: str, local_api_instance, music_directory: str, flac_directory: str):
        self.cookies = Utils.parse_cookie_str(cookie_str)
        self.local_api = local_api_instance
        self.music_directory = music_directory
        self.flac_directory = flac_directory
        self.converter = OpenCC('t2s')
        self.headers = {'User-Agent': APIConstants.USER_AGENT}
        self.quality_map = {
            "standard": "128", "exhigh": "320", "lossless": "flac",
            "hires": "hires", "jyeffect": "jyeffect", "sky": "sky",
            "jymaster": "master"
        }
        self.album_cache = {}

    def _eapi_encrypt(self, url_path: str, payload: dict) -> dict:
        digest = md5(f"nobody{url_path}use{json.dumps(payload)}md5forencrypt".encode('utf-8')).hexdigest()
        params_str = f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
        padder = padding.PKCS7(algorithms.AES(APIConstants.AES_KEY).block_size).padder()
        padded_data = padder.update(params_str.encode()) + padder.finalize()
        cipher = Cipher(algorithms.AES(APIConstants.AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        return {'params': encrypted_data.hex().upper()}
        
    def _eapi_decrypt(self, encrypted_bytes: bytes) -> str:
        # EAPI 响应使用的主密钥
        AES_KEY = b"e82ckenh8dichen8"
        
        try:
            cipher = Cipher(algorithms.AES(AES_KEY), modes.ECB())
            decryptor = cipher.decryptor()
            unpadder = padding.PKCS7(algorithms.AES(AES_KEY).block_size).unpadder()

            decrypted_padded_data = decryptor.update(encrypted_bytes) + decryptor.finalize()
            unpadded_data = unpadder.update(decrypted_padded_data) + unpadder.finalize()
            
            return unpadded_data.decode('utf-8')
        except Exception as e:
            raise ValueError(f"EAPI 响应解密失败: {e}")

    def _generate_cache_key(self, params: dict) -> str:
        # 1. 按照 key 的第一个字母的 code point 排序
        sorted_keys = sorted(params.keys(), key=lambda k: ord(k[0]))
        
        # 2. 连接成 query string
        query_string = "&".join([f"{k}={params[k]}" for k in sorted_keys])
        
        # 3. 使用 AES-128-ECB 加密 (cryptography 实现)
        cipher = Cipher(algorithms.AES(APIConstants.CACHE_KEY_AES_KEY), modes.ECB())
        encryptor = cipher.encryptor()
        padder = padding.PKCS7(algorithms.AES(APIConstants.CACHE_KEY_AES_KEY).block_size).padder()

        padded_data = padder.update(query_string.encode('utf-8')) + padder.finalize()
        encrypted_data = encryptor.update(padded_data) + encryptor.finalize()
        
        # 4. Base64 编码
        cache_key = base64.b64encode(encrypted_data).decode('utf-8')
        return cache_key

    def _post_request(self, url: str, data: dict, is_eapi=False):
        try:
            # 加密请求体（如果需要）
            if is_eapi:
                url_path = urllib.parse.urlparse(url).path.replace("/eapi/", "/api/")
                data = self._eapi_encrypt(url_path, data)

            response = requests.post(url, headers=self.headers, cookies=self.cookies, data=data, timeout=20)
            response.raise_for_status()

            if not response.content:
                return None

            if is_eapi:
                try:
                    # 优先尝试直接解析JSON，处理未加密的eapi响应
                    return response.json()
                except json.JSONDecodeError:
                    # 如果直接解析失败，则认为响应是加密的，执行解密
                    decrypted_text = self._eapi_decrypt(response.content)
                    return json.loads(decrypted_text)
            else:
                # 非eapi请求，行为不变
                return response.json()

        except (requests.RequestException, json.JSONDecodeError, ValueError) as e:
            print(f"网易云请求或处理出错: {e}")
            return None

    def _post_song_wiki_request(self, song_id: str):
        """
        【最终修正版】专门用于请求歌曲百科接口的函数。
        正确处理其“加密请求体”和“明文JSON响应”，并手动解析JSON文本。
        """
        # 1. 准备数据
        ext_json = json.dumps({"states": {"playingResource": {"current": str(song_id)}}})
        url_query_params = {
            'extJson': ext_json,
            'positionCode': "songWikiMainPosition"
        }
        
        eapi_payload_to_encrypt = {
            "extJson": ext_json,
            "positionCode": "songWikiMainPosition",
            "header": "{}",
            "e_r": True
        }

        # 2. 加密 POST Body
        url_path = urllib.parse.urlparse(APIConstants.SONG_WIKI_API).path
        encrypted_post_body = self._eapi_encrypt(url_path, eapi_payload_to_encrypt)

        # 3. 发起请求
        try:
            response = requests.post(
                APIConstants.SONG_WIKI_API,
                params=url_query_params,
                data=encrypted_post_body,
                headers=self.headers,
                cookies=self.cookies,
                timeout=20
            )
            response.raise_for_status()

            # 4. 【核心修正】先获取响应的文本内容，再手动使用 json.loads() 进行解析
            return json.loads(response.text)
            
        except (requests.RequestException, json.JSONDecodeError) as e:
            print(f"网易云百科接口请求或处理出错: {e}")
            return None

    def _get_song_url_data(self, song_id: str, level: str, meta_info: dict):
        config = APIConstants.DEFAULT_CONFIG.copy()
        config["requestId"] = str(randrange(20000000, 30000000))
        payload = {'ids': [str(song_id)], 'level': level, 'header': json.dumps(config)}
        if level == 'hires': payload['encodeType'] = 'hires'
        else: payload['encodeType'] = 'flac'
        if level == 'sky': payload['immerseType'] = 'c51'
        if level == 'jyeffect':
            try:
                charge_info_list = meta_info.get('privilege', {}).get('chargeInfoList', [])
                jyeffect_info = next((item for item in charge_info_list if item.get('chargeType') == 10), None)
                if jyeffect_info and jyeffect_info.get('bizId'):
                    payload['soundEffect'] = {"type": "jyeffect", "bizId": jyeffect_info['bizId']}
            except Exception: pass
        return self._post_request(APIConstants.SONG_URL_V1, payload, is_eapi=True)
        
    def _get_song_metadata(self, song_id: str):
        data = {'c': json.dumps([{"id": song_id, "v": 0}])}
        response = self._post_request(APIConstants.SONG_DETAIL_V3, data)
        # print(f"song_data: {response}")
        return response

    def _get_song_wiki_details(self, song_id: str) -> dict:
        """【升级版】调用歌曲百科接口，获取详细的创作者和属性信息。"""
        response_data = self._post_song_wiki_request(song_id)
        
        if not response_data or response_data.get('code') != 200:
            return {}

        details = {}
        try:
            blocks = response_data.get('data', {}).get('blocks', [])
            wiki_block = next((b for b in blocks if b.get('bizCode') == 'songDetailNewSongWiki'), None)
            
            if not wiki_block: return {}

            nested_blocks = wiki_block.get('rnData', {}).get('blocks', [])
            
            # --- 【核心修改：增强解析逻辑】---
            # 1. 解析创作信息
            info_block = next((b for b in nested_blocks if b.get('blockCode') == 'wikiSubBlockSongInfoVo'), None)
            if info_block:
                creators = {} # 使用临时字典来收集所有创作者
                elements = info_block.get('blockInfo', {}).get('wikiSubElementVos', [])
                for element in elements:
                    title = element.get('title', '').strip() # 使用strip()处理潜在的前后空格
                    names = [meta.get('text') for meta in element.get('wikiSubMetaVos', []) if meta.get('text')]
                    if not title or not names:
                        continue
                    
                    # 使用“包含”逻辑来匹配，更健壮
                    if '作词' in title:
                        creators.setdefault('lyricist', []).extend(names)
                    elif '作曲' in title:
                        creators.setdefault('composer', []).extend(names)
                    elif '制作人' in title:
                        creators.setdefault('producer', []).extend(names)
                    elif '编曲' in title:
                        creators.setdefault('arranger', []).extend(names) # 新增：编曲
                    elif '混音' in title:
                        creators.setdefault('mix', []).extend(names)
                    elif '母带' in title:
                        creators.setdefault('mastering', []).extend(names)

                # 将收集到的创作者列表用分号连接成字符串
                for key, value in creators.items():
                    details[key] = ";".join(value)

            # 2. 解析基本信息 (逻辑不变)
            base_info_block = next((b for b in nested_blocks if b.get('blockCode') == 'wikiSubBlockBaseInfoVo'), None)
            if base_info_block:
                elements = base_info_block.get('blockInfo', {}).get('wikiSubElementVos', [])
                for element in elements:
                    title = element.get('title', '').strip()
                    if title == '曲风' and element.get('wikiSubMetaVos'):
                        details['genre_from_wiki'] = element['wikiSubMetaVos'][0].get('text')
                    if title == 'BPM' and element.get('content'):
                        details['bpm'] = element.get('content')
                        
        except Exception as e:
            print(f"解析网易云歌曲百科信息时出错: {e}")

        print(details)    
        return details

    def _get_lyric_data(self, song_id: str):
        # 这是一套基于较新API调用整理的、更完整的参数
        data = {'id': song_id, 'cp': 'false', 'tv': '0', 'lv': '0', 'rv': '0', 'kv': '0'}
        return self._post_request(APIConstants.LYRIC_API, data)

    def _get_album_details_by_id(self, album_id: str) -> dict:
        """
        【最终版】使用正确的 URL 参数 和 POST Body 调用 /eapi/album/v3/detail 接口，
        以获取最可靠的专辑详情。
        """
        if not album_id:
            return None
            
        try:
            # 1. 准备用于生成 cache_key 的参数
            params_for_cache_key = {
                'id': str(album_id),
                'e_r': 'true'
            }

            # 2. 生成 cache_key
            cache_key = self._generate_cache_key(params_for_cache_key)
            
            # 3. 构造最终的 URL，将 cache_key 作为 GET 参数
            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"
            
            # 4. 构造最终的 eapi 请求体 (POST Body)
            #    根据您的解密结果，我们只需要一个最小化的 header 即可
            eapi_payload = {
                "id": str(album_id),
                "e_r": "true",
                "header": json.dumps(APIConstants.DEFAULT_CONFIG) # 使用您代码中已有的默认header
            }

            # 5. 使用您已有的 _post_request 方法发送加密请求
            response_json = self._post_request(final_url, eapi_payload, is_eapi=True)

            if response_json and response_json.get('code') == 200:
                print(f">>> 从 eapi 专辑接口 (ID: {album_id}) 获取到详情。")
                return response_json.get('album')
            else:
                 print(f">>> 警告: 查询 eapi 专辑接口 (ID: {album_id}) 失败: {response_json}")

        except Exception as e:
            print(f">>> 警告: 调用 eapi 专辑接口 (ID: {album_id}) 时发生异常: {e}")
            
        return None


    def _embed_metadata(self, file_path: str, song_info: dict, lyric: str, tlyric: str):
        """【最终修正版】写入包括百科接口在内的所有可用元数据。"""
        try:
            # --- 1. 提取所有元数据 ---
            album_info = song_info.get('al', {})
            artists = song_info.get('ar', [])
            
            song_name = song_info.get('name')
            aliases = song_info.get('alia', [])
            if aliases:
                song_name = f"{song_name} ({' / '.join(aliases)})"
                
            album_name = album_info.get('name')
            album_id = str(album_info.get('id'))
            artist_names = ";".join([artist['name'] for artist in artists])
            track_number = song_info.get('no')
            total_tracks = song_info.get('size')
            disc_number = song_info.get('cd')
            
            lyricist = song_info.get('lyricist')
            composer = song_info.get('composer')
            producer = song_info.get('producer')
            arranger = song_info.get('arranger') # <-- 新增：编曲
            mix_engineer = song_info.get('mix')
            mastering_engineer = song_info.get('mastering')
            bpm = song_info.get('bpm')
            genre_from_wiki = song_info.get('genre_from_wiki')

            # --- 2. 通过专辑接口获取权威元数据 ---
            album_details = self.album_cache.get(album_id)
            if album_details is None and album_id not in self.album_cache:
                album_details = self._get_album_details_by_id(album_id)
                self.album_cache[album_id] = album_details

            if album_details:
                album_artist = ";".join([artist['name'] for artist in album_details.get('artists', [])]) or artist_names
                publisher = album_details.get('company')
                genre = genre_from_wiki or album_details.get('subType')
                publish_time_ms = album_details.get('publishTime', 0)
            else:
                album_artist = ";".join([ar['name'] for ar in album_info.get('ar', artists)]) or artist_names
                publisher = album_info.get('company')
                genre = genre_from_wiki or album_info.get('subType')
                publish_time_ms = song_info.get('publishTime', 0)
            
            release_date_str, release_year_str = None, None
            if publish_time_ms and publish_time_ms > 0:
                dt_object = datetime.datetime.fromtimestamp(publish_time_ms / 1000)
                release_date_str = dt_object.strftime('%Y-%m-%d')
                release_year_str = dt_object.strftime('%Y')

            # --- 3. 歌词、封面处理 ---
            full_lyric = f"{lyric}\n\n--- 翻译 ---\n\n{tlyric}" if tlyric and lyric else lyric
            image_data = None
            if album_info.get('picUrl'):
                try:
                    image_response = requests.get(album_info['picUrl'], timeout=30)
                    if image_response.status_code == 200: image_data = image_response.content
                except requests.RequestException: pass
            
            # --- 4. 文件写入 ---
            if file_path.lower().endswith('.mp3'):
                audio = MP3(file_path, ID3=ID3)
                if audio.tags is None: audio.add_tags()
                
                audio.tags.add(TIT2(encoding=3, text=song_name))
                audio.tags.add(TPE1(encoding=3, text=artist_names))
                audio.tags.add(TALB(encoding=3, text=album_name))
                if album_artist: audio.tags.add(TPE2(encoding=3, text=album_artist))
                if track_number: audio.tags.add(TRCK(encoding=3, text=f"{track_number}/{total_tracks}" if total_tracks else str(track_number)))
                if disc_number: audio.tags.add(TPOS(encoding=3, text=str(disc_number)))
                if genre: audio.tags.add(TCON(encoding=3, text=genre))
                if publisher: audio.tags.add(TPUB(encoding=3, text=publisher))
                if lyricist: audio.tags.add(TEXT(encoding=3, text=lyricist))
                if composer: audio.tags.add(TCOM(encoding=3, text=composer))
                if producer: audio.tags.add(TPE4(encoding=3, text=producer))
                if arranger: audio.tags.add(TPE4(encoding=3, text=arranger)) # <-- 新增：写入编曲
                if bpm: audio.tags.add(TBPM(encoding=3, text=str(bpm)))
                if mix_engineer: audio.tags.add(TXXX(encoding=3, desc='MIXING', text=mix_engineer))
                if mastering_engineer: audio.tags.add(TXXX(encoding=3, desc='MASTERING', text=mastering_engineer))
                if release_date_str:
                    audio.tags.add(TDRC(encoding=3, text=release_date_str))
                    audio.tags.add(TDOR(encoding=3, text=release_date_str))
                if release_year_str: audio.tags.add(TYER(encoding=3, text=release_year_str))
                if image_data: audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='Cover', data=image_data))
                if full_lyric: audio.tags.add(USLT(encoding=3, text=full_lyric))
                audio.save()

            elif file_path.lower().endswith('.flac'):
                audio = FLAC(file_path)
                audio['title'] = song_name
                audio['artist'] = artist_names
                audio['album'] = album_name
                if album_artist: audio['albumartist'] = album_artist
                if track_number: audio['tracknumber'] = str(track_number)
                if total_tracks: audio['tracktotal'] = str(total_tracks)
                if disc_number: audio['discnumber'] = str(disc_number)
                if genre: audio['genre'] = genre
                if publisher: audio['organization'] = publisher
                if lyricist: audio['lyricist'] = lyricist
                if composer: audio['composer'] = composer
                if producer: audio['producer'] = producer
                if arranger: audio['arranger'] = arranger # <-- 新增：写入编曲
                if mix_engineer: audio['mixing engineer'] = mix_engineer
                if mastering_engineer: audio['mastering engineer'] = mastering_engineer
                if bpm: audio['bpm'] = str(bpm)
                if release_date_str: audio['date'] = release_date_str
                if release_year_str: audio['year'] = release_year_str
                if full_lyric: audio['lyrics'] = full_lyric
                
                audio.clear_pictures()
                if image_data:
                    picture = Picture()
                    picture.type = 3; picture.mime = "image/jpeg"; picture.desc = "Cover"; picture.data = image_data
                    audio.add_picture(picture)
                audio.save()

            print(f"后台任务: 已将最终元数据嵌入 - {os.path.basename(file_path)}")
        except Exception as e:
            print(f"后台任务: 嵌入元数据时发生严重错误 - {e}")



    def _download_and_process_single_version(self, search_key, quality, download_url, extension, song_info, lyric, tlyric):
        album_name = song_info.get('al', {}).get('name', '')
        safe_album_name = re.sub(r'[\\/*?:"<>|]', "", album_name) if album_name else ""
        base_name = f"{search_key} {safe_album_name}" if safe_album_name else search_key
        
        # --- 【核心修改：动态决定保存路径】---
        
        save_directory = self.music_directory # 默认保存在主音乐目录

        # 规则：当且仅当要下载的是 'flac' 音质时，进行判断
        if quality == 'flac':
            # 查询数据库，看这首歌是否已存在 master 版本
            existing_qualities = self.local_api.get_existing_qualities(search_key, album_name)
            if 'master' in existing_qualities:
                # 如果 master 已存在，则将 flac 的保存路径指向子目录
                print(f">>> 检测到已存在 master 版本，将把 flac 版本下载到 'flac' 子目录。")
                save_directory = self.flac_directory

        # 根据最终确定的目录，构造完整的文件路径
        filename_suffix = " [M]" if quality == 'master' else ""
        safe_filename = re.sub(r'[\\/*?:"<>|]', "", base_name)
        file_path = os.path.join(save_directory, f"{safe_filename}{filename_suffix}{extension}")
        
        # --- 后续的下载、重试、日志记录逻辑 ---
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"后台任务: 开始下载 '{base_name}' ({quality}) 到 {file_path} (第 {attempt + 1} 次尝试)")
                with requests.get(download_url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(file_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                
                print(f"后台任务: 下载成功 - {file_path}")
                self._embed_metadata(file_path, song_info, lyric, tlyric)
                self.local_api.add_song_to_db(
                    search_key=search_key, file_path=file_path,
                    duration=song_info.get('dt', 0), album=album_name, quality=quality
                )
                return True
            except requests.RequestException as e:
                print(f"后台任务: 下载 '{search_key}' 失败 (第 {attempt + 1} 次尝试)，错误: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                else:
                    song_id = song_info.get('id', '未知ID')
                    self.logger.error(f"歌曲下载失败 - ID: {song_id}, 名称: '{search_key}', 音质: {quality}, 错误: {e}")
                    if os.path.exists(file_path): os.remove(file_path)
                    return False
        return False

    def _background_download_task(self, song_id: str, meta_info: dict, lyric: str, tlyric: str):
        """(后台线程) 智能分层下载，并自动写入数据库。"""
        artist_string = "、".join([artist['name'] for artist in meta_info['ar']])
        song_name = meta_info['name']
        search_key = self.converter.convert(f"{artist_string} - {song_name}")

        # 1. 从数据库获取该歌曲所有音质
        album_name = meta_info.get('al', {}).get('name', '') if meta_info else ''
        existing_qualities = self.local_api.get_existing_qualities(search_key, album_name)
        print(f"后台任务: 本地库中 '{search_key}' 已有音质: {existing_qualities}")

        # 2. 场景一: 如果有master则跳过下载
        if 'master' in existing_qualities:
            print(f"后台任务: 已存在 master 版本，任务结束。")
            return

        # 3. 场景二: 没有master有flac，尝试下载jymaster
        if 'flac' in existing_qualities:
            print(f"后台任务: 已存在 flac 版本，尝试补充 jymaster 版本...")
            url_info_data = self._get_song_url_data(song_id, 'jymaster', meta_info)
            if url_info_data and url_info_data.get('data'):
                song_info_url = url_info_data['data'][0]
                # 严格检查返回的是否是 jymaster
                if song_info_url.get('url') and song_info_url.get('level') == 'jymaster':
                    self._download_and_process_single_version(
                        search_key, 'master', song_info_url['url'], '.flac', meta_info, lyric, tlyric
                    )
            return # 无论是否成功，任务都结束

        # 4. 场景三: 有320k或128k，尝试下载jymaster和lossless
        if '320' in existing_qualities or '128' in existing_qualities:
            print(f"后台任务: 已存在低品质版本，尝试补充 jymaster 和 lossless...")
            for level in ['jymaster', 'lossless']:
                url_info_data = self._get_song_url_data(song_id, level, meta_info)
                if url_info_data and url_info_data.get('data'):
                    song_info_url = url_info_data['data'][0]
                    if song_info_url.get('url') and song_info_url.get('level') == level:
                        db_quality = self.quality_map.get(level)
                        self._download_and_process_single_version(
                            search_key, db_quality, song_info_url['url'], '.flac', meta_info, lyric, tlyric
                        )
            return

        # 5. 场景四: 如果完全没有这首歌
        if not existing_qualities:
            print(f"后台任务: 本地库无此歌曲，开始智能下载...")
            # 先请求jymaster
            url_info_data = self._get_song_url_data(song_id, 'jymaster', meta_info)
            if not url_info_data or not url_info_data.get('data'):
                print(f"后台任务: 无法为 '{search_key}' 获取任何音质的URL。")
                return

            song_info_url = url_info_data['data'][0]
            download_url = song_info_url.get('url')
            actual_level = song_info_url.get('level')

            if not download_url or not actual_level: return
            
            db_quality = self.quality_map.get(actual_level)
            extension = f".{song_info_url.get('type', 'mp3')}"

            # 如果返回的正是jymaster
            if actual_level == 'jymaster':
                self._download_and_process_single_version(search_key, 'master', download_url, extension, meta_info, lyric, tlyric)
                # 然后再请求lossless下载
                print(f"后台任务: 已下载 master, 继续请求 lossless...")
                lossless_data = self._get_song_url_data(song_id, 'lossless', meta_info)
                if lossless_data and lossless_data.get('data'):
                    lossless_info = lossless_data['data'][0]
                    if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                         self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)
            
            # 如果返回的是exhigh或standard
            elif actual_level in ['exhigh', 'standard']:
                print("只有低品质版本...")
                self._download_and_process_single_version(search_key, db_quality, download_url, extension, meta_info, lyric, tlyric)

            # 如果返回的不是上面几种情况 (例如返回了lossless)
            else:
                # 请求lossless下载
                print(f"后台任务: 请求 jymaster 返回了 {actual_level}，现在请求 lossless...")
                lossless_data = self._get_song_url_data(song_id, 'lossless', meta_info)
                if lossless_data and lossless_data.get('data'):
                    lossless_info = lossless_data['data'][0]
                    if lossless_info.get('url') and lossless_info.get('level') == 'lossless':
                         self._download_and_process_single_version(search_key, 'flac', lossless_info['url'], '.flac', meta_info, lyric, tlyric)

    def search_song(self, keyword: str, album: str = None, limit: int = 10):
        """
        根据关键词搜索歌曲，并可选地根据专辑名进行过滤。
        """
        payload = {'s': keyword, 'type': 1, 'limit': limit, 'offset': 0}
        search_data = self._post_request(APIConstants.SEARCH_API, payload, is_eapi=True)
        # print(f"search_data: {search_data}")

        if not search_data or search_data.get('code') != 200 or not search_data.get('result', {}).get('songs'):
            return []
        
        song_list = search_data['result']['songs']
        
        songs_to_process = []
        if album:
            for song in song_list:
                song_album = song.get('al', {}).get('name')
                if song_album and album.lower() in song_album.lower():
                    songs_to_process.append(song)
        else:
            songs_to_process = song_list
        
        formatted_results = []
        for song in songs_to_process:
            formatted_results.append({
                'id': song.get('id'),
                'name': song.get('name'),
                'artist': "、".join([ar.get('name') for ar in song.get('ar', []) if ar.get('name')]),
                'album': song.get('al', {}).get('name'),
            })
        return formatted_results

    def get_song_details(self, song_id, level):
        """
        获取歌曲完整信息，并触发后台下载。
        """
        meta_data = self._get_song_metadata(song_id)
        if not meta_data or not meta_data.get('songs'): return {"error": "获取歌曲元数据失败。"}
        meta_info = meta_data['songs'][0]

        wiki_details = self._get_song_wiki_details(song_id)
        # 将获取到的详细信息合并到主信息字典中
        if wiki_details:
            print(f">>> 成功从百科接口获取到 {list(wiki_details.keys())} 等详细信息。")
            meta_info.update(wiki_details)

        lyric_data = self._get_lyric_data(song_id)
        lyric = lyric_data.get('lrc', {}).get('lyric', '') if lyric_data else ''
        tlyric = lyric_data.get('tlyric', {}).get('lyric', '') if lyric_data else ''

        if self.local_api and Config.DOWNLOADS_ENABLED:
            threading.Thread(target=self._background_download_task, args=(song_id, meta_info, lyric, tlyric)).start()

        url_data = self._get_song_url_data(song_id, level, meta_info)
        if not url_data or not url_data.get('data') or not url_data.get('data')[0].get('url'):
            return {"error": f"获取歌曲URL失败(请求音质:{level})"}
        song_info_url = url_data['data'][0]
        
        actual_quality = song_info_url.get('level')
        formatted_data = {
            "name": meta_info['name'],
            "artist": "、".join([artist['name'] for artist in meta_info['ar']]),
            "album": meta_info['al']['name'],
            "cover_url": meta_info['al']['picUrl'],
            "quality_requested": level,
            "quality_actual": actual_quality,
            "size": Utils.format_size(song_info_url.get('size')),
            "url": song_info_url['url'].replace("http://", "https://"),
            "lyric": lyric,
            "tlyric": tlyric
        }
        return formatted_data
    

    def search_and_get_details(self, keyword: str, level: str, album: str = None):
        """
        根据关键词和可选的专辑名进行搜索，并验证结果的准确性，然后获取最匹配歌曲的详细信息。
        """
        try:
            target_artist, target_song = [x.strip().lower() for x in keyword.split(' - ', 1)]
        except ValueError:
            return {"error": "关键词格式不正确，请使用 '歌手 - 歌曲名' 的格式。"}

        def find_exact_match(results):
            for song in results:
                result_artist = song.get('artist', '').lower()
                result_song = song.get('name', '').lower()
                if target_song == result_song and target_artist in result_artist:
                    return song.get('id')
            return None

        search_results = self.search_song(keyword, album=album, limit=5)
        best_match_id = find_exact_match(search_results)
        
        if not best_match_id and album:
            print(f"未能从专辑 '{album}' 中找到精确匹配，尝试在所有专辑中搜索...")
            search_results = self.search_song(keyword, album=None, limit=5)
            best_match_id = find_exact_match(search_results)

        if not best_match_id:
            return {"error": "未能找到精确匹配的歌曲"}
            
        return self.get_song_details(best_match_id, level)

    def download_playlist_by_id(self, playlist_id: str, level: str) -> dict:
        """
        根据歌单ID，将整个歌单的歌曲加入后台下载队列。
        """
        data = {'id': playlist_id, 'n': 100000, 's': 0}
        response = self._post_request(APIConstants.PLAYLIST_DETAIL_API, data)
        if not response or response.get('code') != 200:
            return {"error": f"获取歌单 (ID: {playlist_id}) 详情失败，请检查ID是否正确。"}
        
        playlist_info = response.get('playlist', {})
        track_ids = [str(t['id']) for t in playlist_info.get('trackIds', [])]
        total_songs = len(track_ids)

        if total_songs == 0:
            return {"error": f"歌单 (ID: {playlist_id}) 中没有找到任何歌曲。"}

        print(f"开始处理歌单 '{playlist_info.get('name')}'，共 {total_songs} 首歌曲。")

        # 遍历歌单中的所有歌曲ID
        for i, song_id in enumerate(track_ids):
            print(f"  -> 正在将第 {i+1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列...")
            # 调用已有的 get_song_details 方法，它会自动触发后台下载线程
            self.get_song_details(song_id, level)
            time.sleep(1) # 添加1秒延迟，避免因请求过快被服务器限制
        
        return {"message": f"歌单 '{playlist_info.get('name')}' 已成功加入下载队列，共 {total_songs} 首歌曲。"}

    def download_album_by_id(self, album_id: str, level: str) -> dict:
        """
        根据专辑ID，将整个专辑的歌曲加入后台下载队列。
        """
        # 直接调用最可靠的 eapi 专辑接口来获取包含所有歌曲信息的完整响应
        album_response = {}
        try:
            params_for_cache_key = {'id': str(album_id), 'e_r': 'true'}
            cache_key = self._generate_cache_key(params_for_cache_key)
            final_url = f"{APIConstants.ALBUM_V3_DETAIL}?cache_key={urllib.parse.quote(cache_key)}"
            eapi_payload = {
                "id": str(album_id),
                "e_r": "true",
                "header": json.dumps(APIConstants.DEFAULT_CONFIG)
            }
            album_response = self._post_request(final_url, eapi_payload, is_eapi=True)
        except Exception as e:
            return {"error": f"请求专辑 (ID: {album_id}) 数据时发生异常: {e}"}

        if not album_response or album_response.get('code') != 200:
            return {"error": f"获取专辑 (ID: {album_id}) 详情失败，请检查ID是否正确。"}
        
        songs = album_response.get('songs', [])
        album_name = album_response.get('album', {}).get('name', '未知专辑')
        total_songs = len(songs)

        if total_songs == 0:
            return {"error": f"专辑 (ID: {album_id}) 中没有找到任何歌曲。"}

        print(f"开始处理专辑 '{album_name}'，共 {total_songs} 首歌曲。")
        
        # 遍历专辑中的所有歌曲
        for i, song in enumerate(songs):
            song_id = str(song['id'])
            print(f"  -> 正在将第 {i+1}/{total_songs} 首歌曲 (ID: {song_id}) 加入队列...")
            # 同样调用 get_song_details 来触发下载
            self.get_song_details(song_id, level)
            time.sleep(1) # 添加1秒延迟
        
        return {"message": f"专辑 '{album_name}' 已成功加入下载队列，共 {total_songs} 首歌曲。"}

    def start_background_playlist_download(self, playlist_id: str, level: str):
        """
        启动一个后台线程来执行整个歌单的下载任务。
        这个函数会立即返回。
        """
        # 创建并启动一个新线程，目标是我们之前写的 download_playlist_by_id 方法
        thread = threading.Thread(target=self.download_playlist_by_id, args=(playlist_id, level))
        thread.daemon = True  # 设置为守护线程，主程序退出时线程也会退出
        thread.start()
        print(f"已为歌单 {playlist_id} 启动后台下载线程。")

    def start_background_album_download(self, album_id: str, level: str):
        """
        启动一个后台线程来执行整个专辑的下载任务。
        这个函数会立即返回。
        """
        # 创建并启动一个新线程，目标是我们之前写的 download_album_by_id 方法
        thread = threading.Thread(target=self.download_album_by_id, args=(album_id, level))
        thread.daemon = True
        thread.start()
        print(f"已为专辑 {album_id} 启动后台下载线程。")


