# -*- coding: utf-8 -*-
"""抖音解析模块 —— 封装现有 douyin_parser.py，提供模块化接口

复用根目录的 DouyinParser 类，适配为 TransVideo 流水线所需的接口。
"""

import os
import sys
import re
import time
import json
import requests
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Optional


@dataclass
class DouyinVideoInfo:
    """抖音视频信息"""
    aweme_id: str
    desc: str
    author_nickname: str
    duration_ms: int
    width: int
    height: int
    cdn_url: str
    no_watermark_url: str
    cover_url: str
    content_type: str
    content_length: int  # 字节


class DouyinParser:
    """抖音短视频解析器（从 douyin_parser.py 移植，模块化）"""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) '
                'Version/16.6 Mobile/15E148 Safari/604.1'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9',
            'Referer': 'https://www.douyin.com/',
        })

    def extract_short_url(self, text: str) -> str:
        """从分享文本中提取 v.douyin.com 短链接"""
        patterns = [
            r'https?://v\.douyin\.com/[A-Za-z0-9]+/?',
            r'https?://www\.iesdouyin\.com/share/video/\d+/?',
            r'https?://www\.douyin\.com/video/\d+/?',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0).rstrip('/')
        raise ValueError("无法从输入文本中提取抖音链接")

    def get_video_id(self, short_url: str) -> str:
        """跟随短链接重定向，提取视频ID"""
        resp = self.session.get(short_url, allow_redirects=True, timeout=15)
        final_url = resp.url
        match = re.search(r'/(?:share/video/|video/|note/)(\d+)', final_url)
        if match:
            return match.group(1)
        match = re.search(r'/(\d{15,})', final_url)
        if match:
            return match.group(1)
        raise ValueError(f"无法从重定向URL提取视频ID: {final_url}")

    def get_share_page(self, video_id: str) -> str:
        """请求 iesdouyin.com 分享页 HTML"""
        share_url = f"https://www.iesdouyin.com/share/video/{video_id}/"
        resp = self.session.get(share_url, timeout=15)
        return resp.text

    def parse_router_data(self, html: str) -> dict:
        """从 HTML 中提取并解析 window._ROUTER_DATA JSON"""
        marker = 'window._ROUTER_DATA = '
        start = html.find(marker)
        if start == -1:
            raise ValueError("页面中未找到 _ROUTER_DATA")

        json_start = html.find('{', start)
        depth = 0
        in_string = False
        escape = False
        json_end = json_start

        for i in range(json_start, len(html)):
            c = html[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    json_end = i + 1
                    break

        json_str = html[json_start:json_end]
        return json.loads(json_str)

    def extract_video_info(self, router_data: dict) -> DouyinVideoInfo:
        """从 _ROUTER_DATA 中提取视频信息"""
        loader_data = router_data.get('loaderData', {})

        page_data = None
        for key, value in loader_data.items():
            if isinstance(value, dict) and 'videoInfoRes' in value:
                page_data = value
                break

        if not page_data:
            raise ValueError("未找到 videoInfoRes 数据")

        item_list = page_data.get('videoInfoRes', {}).get('item_list', [])
        if not item_list:
            raise ValueError("item_list 为空")

        item = item_list[0]
        video = item.get('video', {})
        author = item.get('author', {})

        play_addr = video.get('play_addr', {})
        url_list = play_addr.get('url_list', [])

        if not url_list:
            raise ValueError("未找到视频播放地址")

        watermarked_url = url_list[0]
        no_watermark_url = watermarked_url.replace('playwm', 'play')

        cover_urls = video.get('cover', {}).get('url_list', [])

        # 先返回带无水印链接的基础信息，CDN 地址需单独解析
        return DouyinVideoInfo(
            aweme_id=item.get('aweme_id', ''),
            desc=item.get('desc', ''),
            author_nickname=author.get('nickname', ''),
            duration_ms=video.get('duration', 0),
            width=video.get('width', 0),
            height=video.get('height', 0),
            cdn_url="",  # 待 resolve
            no_watermark_url=no_watermark_url,
            cover_url=cover_urls[0] if cover_urls else '',
            content_type='',
            content_length=0,
        )

    def resolve_cdn_url(self, no_watermark_url: str) -> tuple:
        """跟随无水印链接的重定向，获取 CDN 真实地址"""
        resp = self.session.get(no_watermark_url, allow_redirects=True, timeout=15, stream=True)
        return resp.url, resp.headers

    def parse(self, text: str) -> DouyinVideoInfo:
        """完整解析流程：分享文本 → 视频信息 + CDN地址"""
        print("[douyin] 步骤1: 提取短链接...")
        short_url = self.extract_short_url(text)
        print(f"[douyin]   短链接: {short_url}")

        print("[douyin] 步骤2: 跟随重定向，提取视频ID...")
        video_id = self.get_video_id(short_url)
        print(f"[douyin]   视频ID: {video_id}")

        print("[douyin] 步骤3: 请求分享页，解析 _ROUTER_DATA...")
        html = self.get_share_page(video_id)
        router_data = self.parse_router_data(html)
        print(f"[douyin]   _ROUTER_DATA 解析成功 ({len(json.dumps(router_data))} 字符)")

        print("[douyin] 步骤4: 提取视频信息...")
        info = self.extract_video_info(router_data)
        print(f"[douyin]   标题: {info.desc[:50]}...")

        print("[douyin] 步骤5: 解析CDN真实地址...")
        cdn_url, headers = self.resolve_cdn_url(info.no_watermark_url)
        info.cdn_url = cdn_url
        info.content_type = headers.get('Content-Type', '')
        info.content_length = int(headers.get('Content-Length', 0))
        size_mb = info.content_length / 1024 / 1024
        print(f"[douyin]   CDN: {cdn_url[:80]}...")
        print(f"[douyin]   类型: {info.content_type}  大小: {size_mb:.1f} MB")

        return info

    def download_video(self, info: DouyinVideoInfo, output_path: str) -> str:
        """下载视频到指定路径"""
        print(f"[douyin] 开始下载: {output_path}")
        resp = self.session.get(info.cdn_url, stream=True, timeout=60)
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        start_time = time.time()

        with open(output_path, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        elapsed = time.time() - start_time
                        speed = downloaded / 1024 / 1024 / max(elapsed, 0.01)
                        print(f"\r[douyin]   进度: {pct:.1f}% "
                              f"({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB) "
                              f"{speed:.1f}MB/s", end='', flush=True)

        print(f"\n[douyin] 下载完成: {output_path}")
        return output_path
