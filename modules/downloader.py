# -*- coding: utf-8 -*-
"""视频下载器模块 —— 统一处理本地文件、抖音、TikTok、YouTube、普通URL

支持的源：
  - 本地视频文件
  - 抖音分享链接（v.douyin.com / www.douyin.com / www.iesdouyin.com）
  - TikTok 链接（www.tiktok.com / vm.tiktok.com / vt.tiktok.com）—— 走 yt-dlp
  - YouTube 链接（youtube.com / youtu.be）
  - 其他 yt-dlp 支持的网站（B站、Vimeo、Twitter 等）
  - 普通直链URL
"""

import os
import re
import shutil
from pathlib import Path
from typing import Optional

from config import Config
from modules.douyin_parser import DouyinParser, DouyinVideoInfo
from modules.youtube_parser import YouTubeParser, YouTubeVideoInfo


class VideoDownloader:
    """统一视频下载器"""

    def __init__(self, config: Config):
        self.config = config
        self.douyin_parser = DouyinParser()
        # YouTube 解析器（使用配置的代理 + TikTok cookies 浏览器）
        self.youtube_parser = YouTubeParser(
            proxy=config.network_proxy or None,
            tiktok_cookies_browser=getattr(config, "tiktok_cookies_browser", "") or None,
        )

    def is_local_file(self, source: str) -> bool:
        """判断是否为本地文件路径"""
        return os.path.isfile(source)

    def is_douyin_link(self, source: str) -> bool:
        """判断是否为抖音分享链接（国内版）"""
        patterns = [
            r'https?://v\.douyin\.com/',
            r'https?://www\.iesdouyin\.com/',
            r'https?://www\.douyin\.com/',
        ]
        return any(re.search(p, source) for p in patterns)

    def is_tiktok_link(self, source: str) -> bool:
        """判断是否为 TikTok 链接（国际版）

        TikTok 链接格式：
          - https://www.tiktok.com/@username/video/1234567890
          - https://vm.tiktok.com/ZMxxxxxxx/  (短链接)
          - https://vt.tiktok.com/ZSxxxxxxx/  (短链接)
          - https://m.tiktok.com/v/1234567890.html
        """
        patterns = [
            r'https?://(?:www\.|m\.)?tiktok\.com/',
            r'https?://vm\.tiktok\.com/',
            r'https?://vt\.tiktok\.com/',
        ]
        return any(re.search(p, source, re.IGNORECASE) for p in patterns)

    def is_youtube_link(self, source: str) -> bool:
        """判断是否为 YouTube 链接"""
        return self.youtube_parser.is_youtube_url(source)

    def is_streaming_site(self, source: str) -> bool:
        """判断是否为 yt-dlp 支持的流媒体网站"""
        return self.youtube_parser.is_supported_url(source)

    def is_url(self, source: str) -> bool:
        """判断是否为普通URL"""
        return source.startswith(('http://', 'https://'))

    def download(self, source: str, output_dir: Optional[str] = None) -> tuple:
        """
        下载视频，返回 (video_path, info)
        - 本地文件：直接返回路径
        - 抖音链接：解析+下载
        - TikTok 链接：走 yt-dlp 流媒体分支
        - YouTube/流媒体：yt-dlp 解析+下载
        - 普通URL：直接下载
        """
        out_dir = output_dir or self.config.output_dir
        Path(out_dir).mkdir(parents=True, exist_ok=True)

        # ===== 本地文件 =====
        if self.is_local_file(source):
            print(f"[downloader] 本地文件: {source}")
            filename = os.path.basename(source)
            dest = os.path.join(out_dir, filename)
            if os.path.abspath(source) != os.path.abspath(dest):
                shutil.copy2(source, dest)
            return dest, {"source": "local", "filename": filename}

        # ===== 抖音（国内版）=====
        if self.is_douyin_link(source):
            print("[downloader] 检测到抖音链接，开始解析...")
            info = self.douyin_parser.parse(source)
            safe_title = re.sub(r'[\\/:*?"<>|\n\r\']', '_', info.desc[:40])
            filename = f"{safe_title}_{info.aweme_id}.mp4"
            output_path = os.path.join(out_dir, filename)
            self.douyin_parser.download_video(info, output_path)
            return output_path, {
                "source": "douyin",
                "aweme_id": info.aweme_id,
                "desc": info.desc,
                "author": info.author_nickname,
                "duration_ms": info.duration_ms,
                "width": info.width,
                "height": info.height,
            }

        # ===== TikTok（国际版，走 yt-dlp）=====
        if self.is_tiktok_link(source):
            print("[downloader] 检测到 TikTok 链接，使用 yt-dlp 解析...")
            # TikTok 需要代理（国内无法直连）
            if self.config.network_proxy:
                print(f"[downloader] 使用代理: {self.config.network_proxy}")
            else:
                print(f"[downloader] {Color.YELLOW}⚠ TikTok 在国内需要代理，若失败请在配置中设置 network.proxy{Color.RESET}")

            try:
                yt_info = self.youtube_parser.parse(source)
            except Exception as e:
                err_msg = str(e)
                if "universal data" in err_msg or "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
                    print(f"[downloader] {Color.RED}✗ TikTok 反爬拦截{Color.RESET}")
                    print(f"[downloader] {Color.DIM}解决方法：{Color.RESET}")
                    print(f"[downloader] {Color.DIM}  1. 在浏览器中登录 https://www.tiktok.com{Color.RESET}")
                    print(f"[downloader] {Color.DIM}  2. 在「修改配置」中设置 TikTok cookies 浏览器{Color.RESET}")
                    print(f"[downloader] {Color.DIM}  3. 确保代理可用（TikTok 在国内需要代理）{Color.RESET}")
                    print(f"[downloader] {Color.DIM}  4. 更新 yt-dlp: pip install -U yt-dlp{Color.RESET}")
                raise RuntimeError(f"TikTok 解析失败: {e}")

            # 生成安全文件名（TikTok 标题可能很长且含很多标签）
            safe_title = re.sub(r'[\\/:*?"<>|\n\r#\']', '_', yt_info.title[:40])
            video_id = yt_info.video_id or ""
            filename = f"{safe_title}_{video_id}.mp4" if video_id else f"{safe_title}.mp4"
            # 限制文件名长度（Windows 路径长度限制）
            if len(filename) > 100:
                filename = f"{safe_title[:50]}_{video_id}.mp4"
            output_path = os.path.join(out_dir, filename)

            # 下载
            self.youtube_parser.download(source, output_path)

            return output_path, {
                "source": "tiktok",
                "video_id": yt_info.video_id,
                "desc": yt_info.title,
                "author": yt_info.author,
                "duration_ms": int(yt_info.duration * 1000),
                "width": yt_info.width,
                "height": yt_info.height,
                "view_count": yt_info.view_count,
                "upload_date": yt_info.upload_date,
                "extractor": yt_info.extractor,
                "description": yt_info.description,
            }

        # ===== YouTube 及其他流媒体 =====
        if self.is_streaming_site(source):
            site_name = "YouTube" if self.is_youtube_link(source) else "流媒体"
            print(f"[downloader] 检测到{site_name}链接，开始解析（yt-dlp）...")

            # 提示代理状态
            if self.config.network_proxy:
                print(f"[downloader] 使用代理: {self.config.network_proxy}")
            elif self.is_youtube_link(source):
                print(f"[downloader] {Color.YELLOW}⚠ YouTube 在国内需要代理，若失败请在配置中设置 network.proxy{Color.RESET}")

            try:
                yt_info = self.youtube_parser.parse(source)
            except Exception as e:
                err_msg = str(e)
                if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
                    print(f"[downloader] {Color.RED}✗ YouTube 反爬拦截，需要配置代理或登录 cookie{Color.RESET}")
                    print(f"[downloader] {Color.DIM}解决方法：在 .env 中设置 NETWORK_PROXY{Color.RESET}")
                raise RuntimeError(f"YouTube 解析失败: {e}")

            # 生成安全文件名（单引号 ' 会导致 ffmpeg 路径解析失败，需一并替换）
            safe_title = re.sub(r'[\\/:*?"<>|\n\r\']', '_', yt_info.title[:40])
            video_id = yt_info.video_id or ""
            filename = f"{safe_title}_{video_id}.mp4" if video_id else f"{safe_title}.mp4"
            output_path = os.path.join(out_dir, filename)

            # 下载
            self.youtube_parser.download(source, output_path)

            return output_path, {
                "source": "youtube" if self.is_youtube_link(source) else "streaming",
                "video_id": yt_info.video_id,
                "desc": yt_info.title,
                "author": yt_info.author,
                "duration_ms": int(yt_info.duration * 1000),
                "width": yt_info.width,
                "height": yt_info.height,
                "view_count": yt_info.view_count,
                "upload_date": yt_info.upload_date,
                "extractor": yt_info.extractor,
                "description": yt_info.description,
            }

        # ===== 普通URL直链 =====
        if self.is_url(source):
            print(f"[downloader] 普通URL下载: {source}")
            import requests
            filename = source.split('/')[-1].split('?')[0] or "video.mp4"
            if not filename.endswith('.mp4'):
                filename += ".mp4"
            output_path = os.path.join(out_dir, filename)
            proxies = {"http": self.config.network_proxy, "https": self.config.network_proxy} if self.config.network_proxy else None
            resp = requests.get(source, stream=True, timeout=60, proxies=proxies)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            return output_path, {"source": "url", "filename": filename}

        raise ValueError(f"无法识别的输入源: {source}")


# 简单的 ANSI 颜色（避免循环依赖 cli.py）
class Color:
    RED = '\033[91m'
    YELLOW = '\033[93m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    DIM = '\033[2m'
    RESET = '\033[0m'
