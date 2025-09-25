class Utils:
    """存放共享的辅助函数。"""
    @staticmethod
    def format_size(value: int) -> str:
        """将字节大小转换为人类可读的格式 (KB, MB, GB)。"""
        if not isinstance(value, (int, float)):
            return "0B"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = 1024.0
        for i in range(len(units)):
            if (value / size) < 1:
                return f"{value:.2f}{units[i]}"
            value /= size
        return f"{value:.2f}{units[-1]}"

    @staticmethod
    def parse_cookie_str(text: str) -> dict:
        """将Cookie字符串解析为字典。"""
        cookie_dict = {}
        if not text:
            return cookie_dict
        for item in text.strip().split(';'):
            if '=' in item:
                key, value = item.strip().split('=', 1)
                cookie_dict[key.strip()] = value.strip()
        return cookie_dict