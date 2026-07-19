# -*- coding: utf-8 -*-
"""
抖音短视频解析工具 (Douyin Video Parser)
==========================================
功能：输入抖音分享文本/短链接，解析出无水印CDN真实视频下载地址

原理：
  1. 从分享文本中提取 v.douyin.com 短链接
  2. 跟随 302 重定向到 iesdouyin.com 分享页，提取视频ID (aweme_id)
  3. 请求分享页 HTML，解析内嵌的 window._ROUTER_DATA JSON
  4. 从 JSON 中提取 play_addr.url_list（带水印的 playwm 链接）
  5. 将 playwm 替换为 play，得到无水印视频链接
  6. 无水印链接会 302 跳转到 CDN 真实地址 (douyinvod.com)

用法：
  python douyin_parser.py "分享文本或短链接"
  python douyin_parser.py "https://v.douyin.com/xxxxx/"
  python douyin_parser.py --download "分享文本"  # 下载视频到当前目录

依赖：pip install requests
"""

import re
import sys
import json
import os
import time
import requests
from urllib.parse import urlparse


class DouyinParser:
    """抖音短视频解析器"""

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
        # 从 URL 路径中提取视频ID
        # 格式: /share/video/XXXXXXXXX 或 /video/XXXXXXXXX
        match = re.search(r'/(?:share/video/|video/|note/)(\d+)', final_url)
        if match:
            return match.group(1)
        # 如果 URL 本身就包含 video ID（iesdouyin 格式）
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
        # 使用括号深度匹配提取完整 JSON
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

    def extract_video_info(self, router_data: dict) -> dict:
        """从 _ROUTER_DATA 中提取视频信息"""
        # 数据路径: loaderData -> video_(id)/page -> videoInfoRes -> item_list[0]
        loader_data = router_data.get('loaderData', {})

        # 查找包含 videoInfoRes 的 key（key名含特殊字符）
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
        music = item.get('music', {})
        statistics = item.get('statistics', {})

        # 提取播放地址
        play_addr = video.get('play_addr', {})
        url_list = play_addr.get('url_list', [])

        if not url_list:
            raise ValueError("未找到视频播放地址")

        # 带水印链接 (playwm) → 无水印链接 (play)
        watermarked_url = url_list[0]
        no_watermark_url = watermarked_url.replace('playwm', 'play')

        # 提取封面图
        cover_urls = video.get('cover', {}).get('url_list', [])

        return {
            'aweme_id': item.get('aweme_id', ''),
            'desc': item.get('desc', ''),
            'author': {
                'nickname': author.get('nickname', ''),
                'uid': author.get('uid', ''),
                'sec_uid': author.get('sec_uid', ''),
            },
            'music': {
                'title': music.get('title', ''),
                'duration': music.get('duration', 0),
            },
            'video': {
                'width': video.get('width', 0),
                'height': video.get('height', 0),
                'duration': video.get('duration', 0),  # 毫秒
                'watermarked_url': watermarked_url,
                'no_watermark_url': no_watermark_url,
                'cover_url': cover_urls[0] if cover_urls else '',
            },
            'statistics': {
                'digg_count': statistics.get('digg_count', 0),
                'comment_count': statistics.get('comment_count', 0),
                'share_count': statistics.get('share_count', 0),
                'play_count': statistics.get('play_count', 0),
            },
        }

    def resolve_cdn_url(self, no_watermark_url: str) -> str:
        """跟随无水印链接的重定向，获取 CDN 真实地址"""
        resp = self.session.get(no_watermark_url, allow_redirects=True, timeout=15, stream=True)
        return resp.url, resp.headers

    def parse(self, text: str) -> dict:
        """完整解析流程：分享文本 → 视频信息 + CDN地址"""
        print("[*] 步骤1: 提取短链接...")
        short_url = self.extract_short_url(text)
        print(f"    短链接: {short_url}")

        print("[*] 步骤2: 跟随重定向，提取视频ID...")
        video_id = self.get_video_id(short_url)
        print(f"    视频ID: {video_id}")

        print("[*] 步骤3: 请求分享页，解析 _ROUTER_DATA...")
        html = self.get_share_page(video_id)
        router_data = self.parse_router_data(html)
        print(f"    _ROUTER_DATA 解析成功 ({len(json.dumps(router_data))} 字符)")

        print("[*] 步骤4: 提取视频信息...")
        info = self.extract_video_info(router_data)
        print(f"    标题: {info['desc'][:50]}...")

        print("[*] 步骤5: 解析CDN真实地址...")
        cdn_url, headers = self.resolve_cdn_url(info['video']['no_watermark_url'])
        info['video']['cdn_url'] = cdn_url
        info['video']['content_type'] = headers.get('Content-Type', '')
        info['video']['content_length'] = int(headers.get('Content-Length', 0))
        print(f"    CDN地址: {cdn_url[:80]}...")
        print(f"    类型: {info['video']['content_type']}")
        print(f"    大小: {info['video']['content_length'] / 1024 / 1024:.1f} MB")

        return info

    def download_video(self, info: dict, output_dir: str = '.') -> str:
        """下载视频到本地"""
        cdn_url = info['video']['cdn_url']
        # 生成文件名
        safe_title = re.sub(r'[\\/:*?"<>|\n\r]', '_', info['desc'][:40])
        filename = f"{safe_title}_{info['aweme_id']}.mp4"
        filepath = os.path.join(output_dir, filename)

        print(f"[*] 开始下载: {filename}")
        resp = self.session.get(cdn_url, stream=True, timeout=60)
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        start_time = time.time()

        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1MB chunks
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded / total * 100
                        elapsed = time.time() - start_time
                        speed = downloaded / 1024 / 1024 / max(elapsed, 0.01)
                        print(f"\r    进度: {pct:.1f}% ({downloaded // 1024 // 1024}MB / {total // 1024 // 1024}MB) {speed:.1f}MB/s", end='', flush=True)

        print(f"\n    下载完成: {filepath}")
        return filepath


def format_info(info: dict) -> str:
    """格式化输出视频信息"""
    lines = []
    lines.append("=" * 60)
    lines.append("  抖音视频解析结果")
    lines.append("=" * 60)
    lines.append(f"  视频ID:   {info['aweme_id']}")
    lines.append(f"  作者:     {info['author']['nickname']} (UID: {info['author']['uid']})")
    lines.append(f"  描述:     {info['desc'][:80]}...")
    lines.append(f"  时长:     {info['video']['duration'] / 1000:.1f} 秒")
    lines.append(f"  分辨率:   {info['video']['width']}x{info['video']['height']}")
    lines.append(f"  点赞:     {info['statistics']['digg_count']}")
    lines.append(f"  评论:     {info['statistics']['comment_count']}")
    lines.append(f"  分享:     {info['statistics']['share_count']}")
    lines.append("-" * 60)
    lines.append(f"  无水印链接: {info['video']['no_watermark_url']}")
    lines.append(f"  CDN真实地址: {info['video']['cdn_url']}")
    lines.append(f"  文件大小: {info['video']['content_length'] / 1024 / 1024:.1f} MB")
    lines.append(f"  Content-Type: {info['video']['content_type']}")
    lines.append("-" * 60)
    lines.append(f"  封面图:   {info['video']['cover_url'][:80]}...")
    lines.append("=" * 60)
    return '\n'.join(lines)


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print('  python douyin_parser.py "抖音分享文本或链接"')
        print('  python douyin_parser.py --download "抖音分享文本或链接"')
        print()
        print("示例:")
        print('  python douyin_parser.py "https://v.douyin.com/irU13248ry8/"')
        sys.exit(1)

    download_mode = False
    if '--download' in sys.argv:
        download_mode = True
        sys.argv.remove('--download')

    text = ' '.join(sys.argv[1:])

    parser = DouyinParser()
    try:
        info = parser.parse(text)
        print()
        print(format_info(info))

        if download_mode:
            print()
            parser.download_video(info)

    except Exception as e:
        print(f"\n[!] 解析失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
