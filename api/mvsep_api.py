# api/mvsep_api.py
import logging
import os
from typing import Any, Dict

import aiofiles
import httpx


class MVSepAPI:
    """
    mvsep.com 官方 API 异步客户端
    用于提交音频分离任务、轮询进度并下载伴奏。
    """

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.base_url = "https://mvsep.com/api/separation"
        self._setup_logger()

    def _setup_logger(self):
        logger = logging.getLogger("MVSepAPI")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - [MVSep] %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        self.logger = logger

    @property
    def client(self):
        """复用异步请求客户端，专属海外代理通道"""
        if getattr(self, "_client", None) is None or self._client.is_closed:
            # AI 分离平台由于上传文件较大，需要较长的超时时间
            self._client = httpx.AsyncClient(
                timeout=120.0,
                follow_redirects=True,
                proxy="http://127.0.0.1:7890",  # 给 MVSep 专属挂上本地 HTTP 代理
                # 如果你的 httpx 版本较低报错，请改成: proxies="http://127.0.0.1:7890"
            )
        return self._client

    async def create_separation(
        self, file_path: str, sep_type: int = 40
    ) -> Dict[str, Any]:
        """
        向 mvsep 上传本地音频文件并创建分离任务。

        :param file_path: 本地音频文件路径 (推荐 FLAC)
        :param sep_type: 分离模型 ID
        :return: 包含 'hash' 和 'success' 的字典
        """
        if not os.path.exists(file_path):
            return {"error": f"物理文件不存在: {file_path}"}

        url = f"{self.base_url}/create"
        self.logger.info(
            f"正在将 '{os.path.basename(file_path)}' 上传至 MVSep (模型: {sep_type})..."
        )

        try:
            with open(file_path, "rb") as f:
                files = {
                    "audiofile": (
                        os.path.basename(file_path),
                        f,
                        "application/octet-stream",
                    )
                }

                data = {
                    "api_token": self.api_token,
                    "sep_type": "40",  # 主模型: BS Roformer
                    "add_opt1": "81",  # 子模型: 强绑 ver 2025.07 (SDR vocals: 11.89)
                    "output_format": "2",  # 输出格式: 2 代表 flac (lossless, 16 bit)
                    "is_demo": "0",  # 隐私保护: 0 代表不发布到公开示例页
                }

                self.logger.info(
                    f"正在上传并以顶级配置 (BS Roformer + FLAC) 处理: {os.path.basename(file_path)}"
                )

                response = await self.client.post(
                    url, data=data, files=files, timeout=600.0
                )
                response.raise_for_status()

                result = response.json()
                if result.get("success"):
                    self.logger.info(
                        f"任务创建成功! 获得 Hash: {result.get('data', {}).get('hash')}"
                    )
                else:
                    self.logger.error(f"创建任务失败: {result}")
                return result

        except httpx.RequestError as e:
            self.logger.error(f"网络请求发生异常: {e}")
            return {"error": f"网络请求发生异常: {e}"}
        except Exception as e:
            self.logger.error(f"发生未知异常: {e}")
            return {"error": str(e)}

    async def get_separation_status(self, task_hash: str) -> Dict[str, Any]:
        """
        轮询查询分离任务状态。

        :param task_hash: create_separation 返回的任务唯一哈希值
        :return: 任务状态字典，包含状态码和最终的下载链接
        """
        url = f"{self.base_url}/get"
        params = {"hash": task_hash, "api_token": self.api_token}

        try:
            response = await self.client.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.logger.error(f"获取状态时网络请求失败: {e}")
            return {"error": str(e)}

    async def download_track(self, download_url: str, save_path: str) -> bool:
        """
        将 MVSep 处理好的音轨下载回本地。

        :param download_url: API 提供的伴奏或人声下载链接
        :param save_path: 完整的本地保存路径
        :return: 布尔值，下载是否成功
        """
        self.logger.info(
            f"正在从 MVSep 下载处理结果至 -> {os.path.basename(save_path)}"
        )

        # 确保保存目录存在
        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        try:
            async with self.client.stream("GET", download_url, timeout=300.0) as r:
                r.raise_for_status()
                # 使用 aiofiles 确保大文件写入时不会阻塞事件循环
                async with aiofiles.open(save_path, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=8192):
                        await f.write(chunk)

            self.logger.info(f"下载完毕: {os.path.basename(save_path)}")
            return True

        except Exception as e:
            self.logger.error(f"下载文件时发生严重错误: {e}")
            if os.path.exists(save_path):
                os.remove(save_path)  # 清理下载失败的残缺文件
            return False

    async def close(self):
        """关闭 HTTP 客户端，释放连接池"""
        if getattr(self, "_client", None) and not self._client.is_closed:
            await self._client.aclose()
