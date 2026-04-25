import json
import re
from pathlib import Path

import requests

from core.config import Config

from .utils import sign


class QQCookieRefresher:
    def __init__(self):
        self.user_config = Config.QQ_USER_CONFIG
        self.config_path = Path(__file__).parent.parent.parent / "core" / "config.py"

    def _build_request_body(self) -> dict:
        """构建请求体"""
        return {
            "comm": {
                # 1. 基础设备参数升级版本
                "ct": "11",
                "cv": "14080008",
                "v": "14080008",
                "chid": "2005000982",
                "tmeAppID": "qqmusic",
                "format": "json",
                "inCharset": "utf-8",
                "outCharset": "utf-8",
                # 2. 补全账号校验参数
                "qq": str(self.user_config["uin"]),
                "authst": self.user_config["qqmusic_key"],
                "tmeLoginType": "2"
                if self.user_config["qqmusic_key"].startswith("Q_H_L")
                else "1",
                # 3. 最关键的一步：补充设备指纹对抗风控 (以下可以使用固定值或随机生成)
                "os_ver": "10",
                "phonetype": "MI 6",
                "devicelevel": "29",
                "rom": "xiaomi/iarim/sagit:10/eomam.200122.001/6543210:user/release-keys",
                "aid": "ffffffffbff94f7d000000000033c587",
                "nettype": "wifi",
                "udid": "ffffffffbff94f7d000000000033c587",
                "OpenUDID": "ffffffffbff94f7d000000000033c587",
                "OpenUDID2": "ffffffffbff94f7d000001996c7fddff",
                "QIMEI36": "0fd8b521df8415e5d25da4ba100012e19915",  # 如果有条件，最好用 qimei.py 动态获取
                "QIMEI": "",
            },
            "req1": {
                "module": "music.login.LoginServer",
                "method": "Login",
                "param": {
                    "openid": self.user_config.get("openId", ""),
                    "access_token": self.user_config.get("accessToken", ""),
                    "refresh_token": "",
                    "expired_in": 0,
                    "musicid": int(self.user_config["uin"]),  # 建议强转为 int
                    "musickey": self.user_config["qqmusic_key"],
                    "refresh_key": self.user_config.get("refresh_token", ""),
                    "loginMode": 2,
                },
            },
        }

    def _update_config_file(self, new_data: dict):
        """将新的 uin, qqmusic_key, qm_keyst, refresh_token 写回 config.py"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                content = f.read()

            updates = {
                "uin": str(new_data.get("musicid", self.user_config["uin"])),
                "qqmusic_key": new_data.get(
                    "musickey", self.user_config["qqmusic_key"]
                ),
                "refresh_token": new_data.get(
                    "refresh_token", self.user_config.get("refresh_token", "")
                ),
            }
            updates["qm_keyst"] = updates["qqmusic_key"]

            for key, value in updates.items():
                pattern = f'("{key}":\s*").*?(")'
                replacement_func = lambda m, v=value: m.group(1) + v + m.group(2)
                content = re.sub(pattern, replacement_func, content)

            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(content)

            print("成功将新的Cookie和Token更新到 config.py。")
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
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "okhttp/3.14.9",
            }

            response = requests.post(
                url, data=json_body.encode("utf-8"), headers=headers, timeout=10
            )
            response.raise_for_status()
            response_data = response.json()
            print(f"qq音乐刷新响应：{response_data}")

            if response_data.get("req1", {}).get("code") != 0:
                # 失败时打印完整的服务器响应，方便调试
                print(
                    f"收到服务器响应: \n{json.dumps(response_data, indent=2, ensure_ascii=False)}"
                )
                print(
                    f"刷新失败 [账号: {self.user_config['uin']}] 错误码: {response_data.get('req1', {}).get('code')}"
                )
                return

            print(f"刷新成功 [账号: {self.user_config['uin']}]")
            self._update_config_file(response_data["req1"]["data"])

        except Exception as e:
            print(f"刷新过程中发生异常: {e}")
