# -*- coding: utf-8 -*-
"""YouTube / 流媒体视频解析模块 —— 基于 yt-dlp

功能：
  - 解析 YouTube 视频信息（标题、作者、时长、分辨率等）
  - 下载 YouTube 视频（支持代理）
  - 同时支持其他 yt-dlp 兼容网站（B站、Vimeo、Twitter 等）
  - 针对 TikTok 的反爬措施，提供多策略下载（cookies + extractor-args + 回退）

国内使用：
  YouTube 在国内被墙，需要配置代理。可通过环境变量 HTTP_PROXY/HTTPS_PROXY
  或在 .env 中设置 NETWORK_PROXY 字段。

TikTok 特别说明：
  TikTok 有严格的反爬措施，经常导致 yt-dlp 提取失败（"Unable to extract
  universal data for rehydration"）。本模块实现多策略回退：
    1. 默认策略（网页提取）
    2. 使用浏览器 cookies（需用户在浏览器登录 TikTok）
    3. 使用移动 API（extractor-args app_info）
    4. 使用不同的 device_id 重试
"""

import os
import re
import random
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class YouTubeVideoInfo:
    """YouTube 视频信息"""
    video_id: str
    url: str
    title: str
    description: str
    author: str
    duration: float          # 秒
    width: int
    height: int
    view_count: int
    upload_date: str         # YYYYMMDD
    webpage_url: str
    extractor: str            # youtube / bilibili / vimeo 等


class YouTubeParser:
    """YouTube 视频解析器（基于 yt-dlp）"""

    # TikTok 链接模式
    TIKTOK_PATTERNS = [
        r'https?://(?:www\.|m\.)?tiktok\.com/',
        r'https?://vm\.tiktok\.com/',
        r'https?://vt\.tiktok\.com/',
    ]

    # 支持的浏览器（用于 cookies 提取）
    SUPPORTED_BROWSERS = ["chrome", "firefox", "edge", "brave", "opera", "safari"]

    def __init__(self, proxy: Optional[str] = None,
                 tiktok_cookies_browser: Optional[str] = None):
        """
        Args:
            proxy: 代理地址，如 http://127.0.0.1:7890
                    None 则从环境变量 HTTP_PROXY/HTTPS_PROXY 读取
            tiktok_cookies_browser: 用于 TikTok 的浏览器（如 "chrome"），
                    从该浏览器提取 cookies 绕过反爬。None 则不使用。
        """
        self.proxy = proxy or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
        self.tiktok_cookies_browser = tiktok_cookies_browser

    def _is_tiktok_url(self, url: str) -> bool:
        """判断是否为 TikTok 链接"""
        return any(re.search(p, url, re.IGNORECASE) for p in self.TIKTOK_PATTERNS)

    def _build_opts(self, download: bool = False, output_path: Optional[str] = None,
                    tiktok_strategy: int = 0, is_tiktok: bool = False) -> dict:
        """构建 yt-dlp 选项

        Args:
            download: 是否下载模式
            output_path: 输出路径
            tiktok_strategy: TikTok 下载策略（0=默认, 1=cookies, 2=移动API, 3=组合）
            is_tiktok: 是否为 TikTok 链接（影响格式选择）
        """
        opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "noprogress": not download,
        }

        # 代理
        if self.proxy:
            opts["proxy"] = self.proxy

        # TikTok 特殊策略
        if tiktok_strategy > 0:
            # 策略 1：使用浏览器 cookies 绕过反爬
            if tiktok_strategy == 1 and self.tiktok_cookies_browser:
                opts["cookiesfrombrowser"] = (self.tiktok_cookies_browser,)
                print(f"[youtube] TikTok 策略1: 使用 {self.tiktok_cookies_browser} cookies")

            # 策略 2：使用移动 API（extractor-args）
            # 格式: app_info=iid/app_name/app_version/manifest_app_version/aid
            # 使用一个已知的 iid 池（yt-dlp 内置的默认值）
            elif tiktok_strategy == 2:
                # 生成随机 device_id（在 yt-dlp 源码范围内）
                device_id = str(random.randint(7250000000000000000, 7325099899999994577))
                opts["extractor_args"] = {
                    "tiktok": {
                        "device_id": [device_id],
                    }
                }
                print(f"[youtube] TikTok 策略2: 使用移动 API (device_id={device_id[:10]}...)")

            # 策略 3：同时使用 cookies + 移动 API
            elif tiktok_strategy == 3:
                device_id = str(random.randint(7250000000000000000, 7325099899999994577))
                opts["extractor_args"] = {
                    "tiktok": {
                        "device_id": [device_id],
                    }
                }
                if self.tiktok_cookies_browser:
                    opts["cookiesfrombrowser"] = (self.tiktok_cookies_browser,)
                    print(f"[youtube] TikTok 策略3: cookies + 移动 API")
                else:
                    print(f"[youtube] TikTok 策略3: 移动 API + 随机 device_id")

        if download and output_path:
            # 格式选择：TikTok 与普通流媒体不同
            # TikTok 使用 DASH 格式，视频流和音频流是分开存储的。
            # yt-dlp 源码中 TikTok extractor 的 COMMON_FORMAT_INFO 硬编码了 acodec='aac'，
            # 但视频流实际上是 video-only。我们已通过补丁让 yt-dlp 也提取 bitrateAudioInfo 中的音频流，
            # 所以 TikTok 优先使用 bestvideo+bestaudio 合并，确保音视频都有。
            if is_tiktok:
                # TikTok: 优先视频流 + 音频流合并（补丁后 yt-dlp 会识别独立的音频流）
                # 注意：TikTok 竖屏视频的 height 可能 > 1080（如 1080x1920），
                # 所以不限制 height，改用 filesize 限制避免下载过大文件
                format_str = (
                    "bestvideo[ext=mp4][filesize<50M]+bestaudio/m4a/"  # 视频流 + 音频流合并（首选）
                    "bestvideo[filesize<50M]+bestaudio/"                 # 任意格式的视频 + 音频
                    "best[ext=mp4][filesize<50M]/"                       # 合一格式（回退）
                    "best"                                                 # 最终回退
                )
            else:
                # YouTube/流媒体: 标准的 video+audio 组合
                format_str = (
                    "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
                    "best[height<=1080]/"
                    "best"
                )
            opts.update({
                "outtmpl": output_path,
                "format": format_str,
                "merge_output_format": "mp4",
                # 字幕
                "writesubtitles": False,
                "writeautomaticsub": False,
            })

        return opts

    def is_youtube_url(self, url: str) -> bool:
        """判断是否为 YouTube 链接"""
        patterns = [
            r'https?://(?:www\.)?youtube\.com/watch\?v=',
            r'https?://youtu\.be/',
            r'https?://(?:www\.)?youtube\.com/shorts/',
            r'https?://(?:m\.)?youtube\.com/watch\?v=',
            r'https?://(?:www\.)?youtube\.com/embed/',
        ]
        return any(re.search(p, url) for p in patterns)

    def is_supported_url(self, url: str) -> bool:
        """判断是否为 yt-dlp 支持的链接（YouTube/B站/Vimeo等）"""
        # 先检查是否为 TikTok（单独处理）
        if self._is_tiktok_url(url):
            return True
        # yt-dlp 支持数千个网站，常见模式
        patterns = [
            r'https?://(?:www\.|m\.)?youtube\.com',
            r'https?://youtu\.be/',
            r'https?://(?:www\.)?bilibili\.com',
            r'https?://b23\.tv/',
            r'https?://(?:www\.)?vimeo\.com',
            r'https?://(?:www\.)?twitter\.com',
            r'https?://(?:www\.)?x\.com',
            r'https?://(?:www\.)?twitch\.tv',
            r'https?://(?:www\.)?dailymotion\.com',
            r'https?://(?:www\.)?facebook\.com',
            r'https?://(?:www\.)?instagram\.com',
        ]
        return any(re.search(p, url) for p in patterns)

    def extract_video_id(self, url: str) -> str:
        """从 YouTube URL 提取视频ID"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/)([A-Za-z0-9_-]{11})',
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return ""

    def _try_extract_with_strategies(self, url: str, download: bool,
                                      output_path: Optional[str] = None) -> dict:
        """使用多策略尝试提取/下载 TikTok 视频

        策略顺序（仅对 TikTok 生效，非 TikTok 只用策略0）：
          0. 默认（网页提取）
          1. 使用浏览器 cookies（仅当 tiktok_cookies_browser 已配置时启用）
          2. 使用移动 API（extractor-args + 随机 device_id）
          3. cookies + 移动 API 组合（仅当 tiktok_cookies_browser 已配置时启用）

        返回：yt-dlp 的 info 字典
        失败时抛出 RuntimeError（含所有策略的错误信息 + 解决方案）
        """
        import yt_dlp

        is_tiktok = self._is_tiktok_url(url)
        has_cookies = bool(self.tiktok_cookies_browser)

        # 构建策略列表：非 TikTok 只用默认策略；TikTok 根据是否配置 cookies 选择策略
        if not is_tiktok:
            strategies = [0]
        else:
            # 策略0（默认）→ 策略2（移动API）总是启用
            # 策略1（cookies）和策略3（组合）仅在配置了 cookies 时启用
            strategies = [0]
            if has_cookies:
                strategies.append(1)
            strategies.append(2)
            if has_cookies:
                strategies.append(3)

        strategy_names = {0: "默认", 1: "cookies", 2: "移动API", 3: "组合"}
        errors = []

        for idx, strategy in enumerate(strategies):
            try:
                opts = self._build_opts(download=download, output_path=output_path,
                                         tiktok_strategy=strategy, is_tiktok=is_tiktok)
                # 对 TikTok 使用更宽松的错误处理
                if is_tiktok:
                    opts["ignoreerrors"] = False

                with yt_dlp.YoutubeDL(opts) as ydl:
                    if download:
                        # 下载模式：extract_info(download=True) 一步完成提取+下载
                        # 避免先 extract_info(download=False) 再 download([url]) 的二次提取
                        # （二次提取会被 TikTok 反爬拦截）
                        info = ydl.extract_info(url, download=True)
                        if info and "entries" in info:
                            entries = info["entries"]
                            if not entries:
                                raise ValueError("播放列表为空")
                            info = entries[0]

                        if not info:
                            raise ValueError("无法提取视频信息")

                        # 检查文件（yt-dlp 可能添加扩展名）
                        if output_path and not os.path.isfile(output_path):
                            base = os.path.splitext(output_path)[0]
                            for ext in [".mp4", ".webm", ".mkv"]:
                                candidate = base + ext
                                if os.path.isfile(candidate):
                                    if candidate != output_path:
                                        os.rename(candidate, output_path)
                                    break

                        return info
                    else:
                        # 仅解析模式
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            raise ValueError("无法提取视频信息")
                        if "entries" in info:
                            entries = info["entries"]
                            if not entries:
                                raise ValueError("播放列表为空")
                            info = entries[0]
                        return info

            except Exception as e:
                err_str = str(e)
                errors.append(f"{strategy_names[strategy]}策略: {err_str[:200]}")

                # 非 TikTok 错误：直接抛出（不聚合，保留原始异常类型）
                if not is_tiktok:
                    raise

                # TikTok 错误：判断是否可以尝试下一个策略
                is_last = (idx == len(strategies) - 1)
                if is_last:
                    # 最后一个策略也失败了，抛出聚合错误
                    break

                # 对于"universal data"错误，尝试下一个策略
                # 对于其他错误（如网络超时、代理错误），也尝试下一个策略
                next_name = strategy_names[strategies[idx + 1]]
                print(f"[youtube] {strategy_names[strategy]}策略失败，尝试{next_name}策略...")

        # 所有策略都失败（仅 TikTok 会走到这里）
        cookies_hint = (
            "  ★ 在配置中设置 tiktok_cookies_browser（chrome/firefox/edge 等）\n"
            "    先在浏览器中登录 https://www.tiktok.com，然后程序可自动提取 cookies\n"
        ) if not has_cookies else ""
        raise RuntimeError(
            f"TikTok 下载失败（尝试了 {len(errors)} 种策略）:\n" +
            "\n".join(f"  - {e}" for e in errors) +
            "\n\n解决方法:\n"
            "  1. 更新 yt-dlp: pip install -U yt-dlp\n"
            f"{cookies_hint}"
            "  2. 确保代理可用（TikTok 在国内需要代理）\n"
            "  3. 尝试使用 TikTok 短链接（vm.tiktok.com 或 vt.tiktok.com）\n"
            "  4. 视频可能已被删除或地区受限"
        )

    def parse(self, url: str) -> YouTubeVideoInfo:
        """解析视频信息（不下载）"""
        import yt_dlp

        print(f"[youtube] 解析视频信息: {url}")
        if self.proxy:
            print(f"[youtube] 使用代理: {self.proxy}")

        # 使用多策略提取
        info = self._try_extract_with_strategies(url, download=False)

        if not info:
            raise ValueError(f"无法解析视频: {url}")

        video_id = info.get("id", "")
        title = info.get("title", "")
        author = info.get("uploader", "") or info.get("channel", "")
        duration = float(info.get("duration", 0) or 0)
        width = int(info.get("width", 0) or 0)
        height = int(info.get("height", 0) or 0)
        view_count = int(info.get("view_count", 0) or 0)
        upload_date = info.get("upload_date", "")
        description = info.get("description", "") or ""
        extractor = info.get("extractor_key", "").lower() or info.get("extractor", "")

        # 格式化时长显示
        if duration > 0:
            dur_str = f"{int(duration // 60)}分{int(duration % 60)}秒"
        else:
            dur_str = "未知"

        print(f"[youtube]   标题: {title}")
        print(f"[youtube]   作者: {author}")
        print(f"[youtube]   时长: {dur_str}")
        print(f"[youtube]   分辨率: {width}x{height}" if width else "[youtube]   分辨率: 未知")
        if view_count:
            print(f"[youtube]   观看: {view_count:,}")
        print(f"[youtube]   来源: {extractor}")

        return YouTubeVideoInfo(
            video_id=video_id,
            url=url,
            title=title,
            description=description,
            author=author,
            duration=duration,
            width=width,
            height=height,
            view_count=view_count,
            upload_date=upload_date,
            webpage_url=info.get("webpage_url", url),
            extractor=extractor,
        )

    def _check_has_audio(self, video_path: str) -> bool:
        """用 ffprobe 检查视频文件是否包含音频流"""
        try:
            # 查找 ffprobe
            ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
            if not ffprobe:
                # 尝试常见路径
                for p in [r"D:\Program Files\ffmpeg\bin\ffprobe.exe",
                          r"C:\ffmpeg\bin\ffprobe.exe"]:
                    if os.path.isfile(p):
                        ffprobe = p
                        break
            if not ffprobe:
                return True  # 无法检查，假设有音频

            cmd = [
                ffprobe, "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1",
                video_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
            return "audio" in (result.stdout or "").lower()
        except Exception:
            return True  # 检查失败，假设有音频

    def download(self, url: str, output_path: str) -> str:
        """下载视频到指定路径（支持多策略回退 + 音频验证）"""
        print(f"[youtube] 开始下载: {url}")
        if self.proxy:
            print(f"[youtube] 使用代理: {self.proxy}")

        is_tiktok = self._is_tiktok_url(url)

        # 使用多策略提取+下载
        info = self._try_extract_with_strategies(url, download=True, output_path=output_path)

        # 检查文件是否生成
        if not os.path.isfile(output_path):
            base = os.path.splitext(output_path)[0]
            for ext in [".mp4", ".webm", ".mkv"]:
                candidate = base + ext
                if os.path.isfile(candidate):
                    if candidate != output_path:
                        os.rename(candidate, output_path)
                    break

        if not os.path.isfile(output_path):
            raise FileNotFoundError(f"下载完成但未找到文件: {output_path}")

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        print(f"[youtube] 下载完成: {output_path} ({size_mb:.1f}MB)")

        # TikTok 视频验证音频流：DASH 格式有时只下载了视频流
        if is_tiktok:
            has_audio = self._check_has_audio(output_path)
            if not has_audio:
                print(f"[youtube] ⚠ 下载的视频没有音频流，尝试重新下载（强制合并音视频）...")
                # 删除无音频的文件
                try:
                    os.remove(output_path)
                except OSError:
                    pass

                # 重新下载，使用更严格的格式选择
                self._redownload_with_audio(url, output_path)

                # 再次检查
                if os.path.isfile(output_path):
                    has_audio = self._check_has_audio(output_path)
                    if has_audio:
                        size_mb = os.path.getsize(output_path) / 1024 / 1024
                        print(f"[youtube] ✓ 重新下载成功，视频已包含音频: {size_mb:.1f}MB")
                    else:
                        print(f"[youtube] ⚠ 重新下载后仍无音频，尝试单独下载音频并合并...")
                        self._download_audio_and_merge(url, output_path)
                else:
                    raise RuntimeError("重新下载失败：文件未生成")

        return output_path

    def _redownload_with_audio(self, url: str, output_path: str):
        """重新下载，使用强制合并音视频的格式选择"""
        import yt_dlp

        opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "noprogress": False,
        }
        if self.proxy:
            opts["proxy"] = self.proxy
        # 注意：不使用 cookies，因为 cookies 提取不稳定（Chrome database 复制失败）
        # 禁用 impersonate 后，网页提取是稳定的

        # 强制使用 bestvideo+bestaudio，不回退到 best（避免只下载视频流）
        opts["format"] = (
            "bestvideo+bestaudio/bestvideo+bestaudio/best"
        )
        opts["outtmpl"] = output_path
        opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.extract_info(url, download=True)
        except Exception as e:
            print(f"[youtube] 重新下载失败: {e}")

    def _download_audio_and_merge(self, url: str, video_path: str):
        """单独下载音频流，用 ffmpeg 合并到视频中

        方案：直接从 TikTok 网页 HTML 提取音频 URL，用 urllib + cookies 下载。
        这个方案完全独立于 yt-dlp，避免 yt-dlp 的 TikTok 提取不稳定问题。
        """
        import urllib.request
        import urllib.error
        import re
        import json

        print(f"[youtube] 从网页提取音频 URL...")

        # 步骤1：获取网页 HTML + cookies
        proxy_handler = urllib.request.ProxyHandler({
            "http": self.proxy, "https": self.proxy,
        }) if self.proxy else urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)

        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        try:
            with opener.open(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
                # 获取 cookies（Set-Cookie 头）
                cookies = resp.headers.get_all("Set-Cookie") or []
        except Exception as e:
            raise RuntimeError(f"获取 TikTok 网页失败: {e}")

        if not html:
            raise RuntimeError("网页内容为空")

        # 构建 cookie 字符串
        cookie_str = "; ".join(c.split(";")[0] for c in cookies) if cookies else ""
        print(f"[youtube] 网页大小: {len(html)} 字符, cookies: {len(cookies)} 个")

        # 步骤2：从 __UNIVERSAL_DATA_FOR_REHYDRATION__ 提取音频 URL
        pattern = r'<script[^>]*id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>'
        match = re.search(pattern, html, re.DOTALL)
        if not match:
            raise RuntimeError("网页中未找到 __UNIVERSAL_DATA_FOR_REHYDRATION__")

        try:
            universal_data = json.loads(match.group(1))
            video_detail = universal_data["__DEFAULT_SCOPE__"]["webapp.video-detail"]
            item_struct = video_detail["itemInfo"]["itemStruct"]
            video_info = item_struct.get("video", {})
            bitrate_audio_info = video_info.get("bitrateAudioInfo", [])
        except (KeyError, json.JSONDecodeError) as e:
            raise RuntimeError(f"解析视频数据失败: {e}")

        if not bitrate_audio_info:
            raise RuntimeError("视频数据中没有 bitrateAudioInfo（音频流信息）")

        audio_url = bitrate_audio_info[0]["UrlList"]["MainUrl"]
        print(f"[youtube] 音频 URL: {audio_url[:80]}...")

        # 步骤3：用 urllib + cookies 下载音频
        audio_path = video_path + ".audio.m4a"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.tiktok.com/",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Range": "bytes=0-",
        }
        if cookie_str:
            headers["Cookie"] = cookie_str

        audio_req = urllib.request.Request(audio_url, headers=headers)
        try:
            with opener.open(audio_req, timeout=60) as resp2:
                data = resp2.read()
                with open(audio_path, "wb") as f:
                    f.write(data)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"下载音频失败: HTTP {e.code} {e.reason}")
        except Exception as e:
            raise RuntimeError(f"下载音频失败: {e}")

        size_mb = os.path.getsize(audio_path) / 1024 / 1024
        print(f"[youtube] 音频下载成功: {size_mb:.2f} MB")

        # 步骤4：用 ffmpeg 合并视频和音频
        merged_path = video_path + ".merged.mp4"
        ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
        if not ffmpeg:
            for p in [r"D:\Program Files\ffmpeg\bin\ffmpeg.exe",
                      r"C:\ffmpeg\bin\ffmpeg.exe"]:
                if os.path.isfile(p):
                    ffmpeg = p
                    break

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c", "copy",
            "-map", "0:v",
            "-map", "1:a",
            "-shortest",
            merged_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg 合并失败: {result.stderr[-500:]}")

        # 替换原文件
        os.remove(video_path)
        if os.path.isfile(audio_path):
            os.remove(audio_path)
        os.rename(merged_path, video_path)

        size_mb = os.path.getsize(video_path) / 1024 / 1024
        print(f"[youtube] ✓ 音视频合并完成: {size_mb:.1f}MB")

    def parse_and_download(self, url: str, output_path: str) -> tuple:
        """解析并下载，返回 (info, downloaded_path)"""
        info = self.parse(url)
        downloaded = self.download(url, output_path)
        return info, downloaded
