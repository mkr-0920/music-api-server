import requests
import json
import re
from pathlib import Path
from core.config import Config
from .utils import sign

class QQCookieRefresher:
    def __init__(self):
        self.user_config = Config.QQ_USER_CONFIG
        self.config_path = Path(__file__).parent.parent.parent / "core" / "config.py"

    def _build_request_body(self) -> dict:
        """构建简化的请求体"""
        return {
            "comm": {
                "fPersonality": "0",
                "tmeLoginType": "2" if self.user_config["qqmusic_key"].startswith("Q_H_L") else "1",
                "qq": str(self.user_config["uin"]),
                "authst": self.user_config["qqmusic_key"],
                "ct": "11",
                "cv": "12080008",
                "v": "12080008",
                "tmeAppID": "qqmusic",
            },
            "req1": {
                "module": "music.login.LoginServer",
                "method": "Login",
                "param": {
                    "str_musicid": str(self.user_config["uin"]),
                    "musickey": self.user_config["qqmusic_key"],
                    "refresh_token": self.user_config.get("refresh_token", ""),
                },
            },
        }

    def _update_config_file(self, new_data: dict):
        """将新的 uin, qqmusic_key, qm_keyst, refresh_token 写回 config.py"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            updates = {
                "uin": str(new_data.get("musicid", self.user_config["uin"])),
                "qqmusic_key": new_data.get("musickey", self.user_config["qqmusic_key"]),
                "refresh_token": new_data.get("refresh_token", self.user_config.get("refresh_token", "")),
            }
            updates["qm_keyst"] = updates["qqmusic_key"]

            for key, value in updates.items():
                pattern = f'("{key}":\s*").*?(")'
                replacement_func = lambda m, v=value: m.group(1) + v + m.group(2)
                content = re.sub(pattern, replacement_func, content)

            with open(self.config_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            print(f"成功将新的Cookie和Token更新到 config.py。")
            Config.QQ_USER_CONFIG.update(updates)

        except Exception as e:
            print(f"更新 config.py 文件失败: {e}")

    def refresh(self):
        print("开始执行QQ音乐Cookie刷新任务...")
        if not self.user_config.get("uin") or not self.user_config.get("qqmusic_key"):
            print("错误: 'uin' 或 'qqmusic_key' 未在 config.py 中配置，无法刷新。")
            return

        try:
            request_body = self._build_request_body()
            json_body = json.dumps(request_body, ensure_ascii=False)
            signature = sign(json_body)
            url = f"https://u6.y.qq.com/cgi-bin/musics.fcg?sign={signature}"
            headers = {'Content-Type': 'application/json', 'User-Agent': 'okhttp/3.14.9'}

            response = requests.post(url, data=json_body.encode('utf-8'), headers=headers, timeout=10)
            response.raise_for_status()
            response_data = response.json()

            if response_data.get("req1", {}).get("code") != 0:
                # 失败时打印完整的服务器响应，方便调试
                print(f"收到服务器响应: \n{json.dumps(response_data, indent=2, ensure_ascii=False)}")
                print(f"刷新失败 [账号: {self.user_config['uin']}] 错误码: {response_data.get('req1', {}).get('code')}")
                return

            print(f"刷新成功 [账号: {self.user_config['uin']}]")
            self._update_config_file(response_data["req1"]["data"])

        except Exception as e:
            print(f"刷新过程中发生异常: {e}")

