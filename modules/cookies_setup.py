# -*- coding: utf-8 -*-
"""YouTube cookies 多格式导入 —— 自动识别并统一转为 Netscape 格式

支持三种来源（自动识别，无需用户选择格式）：
  1. Cookie-Editor 扩展导出的 JSON（数组，字段 name/value/domain/path/secure/expirationDate...）
  2. Netscape 格式文本/文件（Get cookies.txt LOCALLY 等扩展导出）
  3. 浏览器开发者工具直接复制的 Cookie 请求头字符串（"a=b; c=d"）

转换结果写入项目根目录 youtube_cookies.txt，并把路径写进 .env 的
YOUTUBE_COOKIES_FILE。yt-dlp 只认 Netscape 格式，统一在此转换。
"""

import json
import os
import time

# 转换后的统一保存位置（项目根目录，已加入 .gitignore）
SAVE_NAME = "youtube_cookies.txt"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 浏览器复制的 Cookie 请求头没有域名信息，默认归属 youtube.com
DEFAULT_DOMAIN = ".youtube.com"


def _netscape_line(domain: str, path: str, secure: bool, expires: int,
                   name: str, value: str) -> str:
    include_sub = "TRUE" if domain.startswith(".") else "FALSE"
    secure_s = "TRUE" if secure else "FALSE"
    return f"{domain}\t{include_sub}\t{path or '/'}\t{secure_s}\t{expires}\t{name}\t{value}"


def _looks_netscape(text: str) -> bool:
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line.count("\t") >= 6
    return False


def _from_json(text: str) -> list:
    """Cookie-Editor JSON 数组 → Netscape 行列表"""
    data = json.loads(text)
    if isinstance(data, dict):  # 有些扩展导出 {"cookies": [...]} 结构
        data = data.get("cookies") or data.get("Cookie") or []
    if not isinstance(data, list):
        raise ValueError("JSON 中没有 cookie 数组")
    lines = []
    for c in data:
        name = c.get("name")
        value = c.get("value", "")
        if not name:
            continue
        domain = c.get("domain") or DEFAULT_DOMAIN
        if c.get("hostOnly") and not domain.startswith("."):
            pass  # hostOnly 保持原样
        elif not domain.startswith("."):
            domain = "." + domain
        # expirationDate 可能是 float 秒级时间戳；会话 cookie 没有该字段 → 给 1 年
        try:
            expires = int(float(c.get("expirationDate") or 0))
        except (TypeError, ValueError):
            expires = 0
        if expires <= 0:
            expires = int(time.time()) + 365 * 86400
        lines.append(_netscape_line(domain, c.get("path", "/"),
                                    bool(c.get("secure", True)), expires,
                                    name, str(value)))
    return lines


def _from_header(text: str) -> list:
    """浏览器复制的 Cookie 请求头字符串 → Netscape 行列表"""
    # 用户可能连 "Cookie: " 前缀一起复制了
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    expires = int(time.time()) + 365 * 86400
    lines = []
    for pair in text.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        lines.append(_netscape_line(DEFAULT_DOMAIN, "/", True, expires,
                                    name.strip(), value.strip()))
    return lines


def convert_to_netscape(text: str) -> list:
    """自动识别格式并转换为 Netscape 行列表，返回行列表（空列表=没有有效 cookie）"""
    text = text.strip()
    if not text:
        raise ValueError("内容为空")
    if _looks_netscape(text):
        return [l for l in text.splitlines()
                if l.strip() and (l.startswith("#") or l.count("\t") >= 6)]
    if text.startswith("[") or text.startswith("{"):
        return _from_json(text)
    return _from_header(text)


def import_youtube_cookies(source: str) -> tuple:
    """从文件路径或粘贴内容导入 YouTube cookies

    source 是已存在的文件路径时读文件，否则当作粘贴的 cookie 内容。
    返回 (ok, message)。成功时写入 youtube_cookies.txt 并更新 .env。
    """
    if os.path.isfile(source):
        with open(source, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        origin = f"文件 {os.path.basename(source)}"
    else:
        text = source
        origin = "粘贴的内容"

    try:
        lines = convert_to_netscape(text)
    except Exception as e:
        return False, f"无法识别的 cookie 格式: {e}"

    # 过滤掉换行都可能破坏 Netscape 格式的非法值
    lines = [l for l in lines if "\n" not in l]
    if not lines:
        return False, "没有解析到任何有效 cookie"

    dest = os.path.join(PROJECT_ROOT, SAVE_NAME)
    with open(dest, "w", encoding="utf-8", newline="\n") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# 由 TransVideo 自动转换（来源: " + origin + "）\n")
        f.write("\n".join(l for l in lines if not l.startswith("#")) + "\n")

    _save_to_env(dest)
    return True, f"已导入 {len(lines)} 条 cookies → {dest}"


def _save_to_env(path: str):
    """把 YOUTUBE_COOKIES_FILE 写入项目 .env（已存在则替换该行）"""
    env_path = os.path.join(PROJECT_ROOT, ".env")
    lines = []
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    key = "YOUTUBE_COOKIES_FILE"
    replaced = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={path}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={path}")
    with open(env_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


def clear_youtube_cookies() -> str:
    """清除设置：删除转换后的文件并清空 .env 配置"""
    dest = os.path.join(PROJECT_ROOT, SAVE_NAME)
    if os.path.isfile(dest):
        os.remove(dest)
    _save_to_env("")
    return "已清除 YouTube cookies 设置"
