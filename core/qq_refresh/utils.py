import re
import base64
import hashlib

# ==================== Base64 & 签名逻辑 ====================

def createBase64Encode(data_bytes: bytearray) -> str:
    """对字节进行Base64编码。"""
    encoded_data = base64.b64encode(data_bytes)
    return encoded_data.decode("utf-8")

PART_1_INDEXES_RAW: list[int] = [23, 14, 6, 36, 16, 40, 7, 19]
PART_2_INDEXES: list[int] = [16, 1, 32, 12, 19, 27, 8, 5]
SCRAMBLE_VALUES: list[int] = [
    89, 39, 179, 150, 218, 82, 58, 252, 177, 52, 186, 123, 120, 64, 242,
    133, 143, 161, 121, 179,
]
PART_1_INDEXES: list[int] = list(filter(lambda x: x < 40, PART_1_INDEXES_RAW))

def sign(payload: str) -> str:
    """对请求体进行签名 (原 signBody 函数)。"""
    hash_val = hashlib.sha1(payload.encode("utf-8")).hexdigest().upper()
    part1 = "".join(map(lambda i: hash_val[i], PART_1_INDEXES))
    part2 = "".join(map(lambda i: hash_val[i], PART_2_INDEXES))
    part3 = bytearray(20)

    for i, v in enumerate(SCRAMBLE_VALUES):
        value = v ^ int(hash_val[i * 2 : i * 2 + 2], 16)
        part3[i] = value

    b64_part = re.sub(r"[\\/+=]", "", createBase64Encode(part3))
    return f"zzc{part1}{b64_part}{part2}".lower()

