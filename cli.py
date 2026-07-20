# -*- coding: utf-8 -*-
"""TransVideo CLI —— 交互式视频翻译配音工具

用法:
  python cli.py                          # 进入交互模式（推荐）
  python cli.py video input.mp4 -t en   # 直接处理本地视频
  python cli.py douyin "<分享文本>" -t en # 直接处理抖音视频
  python cli.py config                   # 查看配置
"""

# 必须在任何其他 import 之前设置：解决 Windows 上 faster-whisper (Intel OpenMP)
# 与其他库的 OpenMP runtime 冲突错误（OMP Error #15）
import os as _os
_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 sys.path 中
ROOT = Path(__file__).parent.absolute()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config, load_config, create_config_template, CONFIG_FILE_NAME
from modules.dependency_check import ensure_required_or_exit, check_all, format_report, get_install_command
from modules.keyboard_ui import (
    keyboard_select, keyboard_confirm, keyboard_pause,
    can_read_key, enable_vt100, Color,
)


# 启用 Windows VT100 颜色支持（必须在第一次输出前调用）
enable_vt100()

import re as _re
import unicodedata as _uc


def _strip_ansi(s: str) -> str:
    """移除 ANSI 颜色代码"""
    return _re.sub(r'\x1b\[[0-9;]*m', '', s)


def _disp_w(s: str) -> int:
    """计算字符串的终端显示宽度

    中文字符占 2 列，英文/数字占 1 列，ANSI 颜色代码不计宽度。
    """
    s = _strip_ansi(s)
    w = 0
    for ch in s:
        if _uc.east_asian_width(ch) in ('W', 'F'):
            w += 2
        else:
            w += 1
    return w


def _pad_right(s: str, target_width: int) -> str:
    """用空格右填充到目标显示宽度"""
    current = _disp_w(s)
    if current >= target_width:
        return s
    return s + ' ' * (target_width - current)


# ==================== 交互式 UI ====================

# ANSI 颜色代码已从 modules.keyboard_ui 导入（Color 类）
# 为兼容现有代码，保留对 Color 的引用（已在顶部 import）


def banner():
    """打印程序横幅（自动对齐，支持中文字符宽度）"""
    W = 66  # 框内宽度
    lines = [
        ("", None),
        ("  TransVideo 视频翻译配音工具", Color.BOLD),
        ("  v2.0 · 多语言互译 · AI 配音", Color.DIM),
        ("", None),
        ("  支持：本地视频 / 抖音 / TikTok / YouTube / B站", Color.DIM),
        ("  引擎：Whisper(GPU) · GPT · Ollama · edge-tts", Color.DIM),
        ("", None),
    ]
    print(f"\n{Color.CYAN}╔{'═' * W}╗")
    for text, color in lines:
        padded = _pad_right(text, W)
        if color:
            print(f"{Color.CYAN}║{color}{padded}{Color.RESET}{Color.CYAN}║")
        else:
            print(f"{Color.CYAN}║{padded}║")
    print(f"╚{'═' * W}╝{Color.RESET}\n")


def pause():
    """暂停等待用户回车或任意键"""
    if can_read_key():
        print(f"\n{Color.DIM}按任意键继续...{Color.RESET}")
        from modules.keyboard_ui import read_key
        read_key()
    else:
        input(f"\n{Color.DIM}按回车键继续...{Color.RESET}")


def input_with_default(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        s = input(f"{prompt} [{Color.YELLOW}{default}{Color.RESET}]: ").strip()
        return s if s else default
    return input(f"{prompt}: ").strip()


def confirm(prompt: str, default_yes: bool = True) -> bool:
    """确认提问（支持键盘选择）"""
    return keyboard_confirm(prompt, default_yes=default_yes)


def choose(prompt: str, options: list, default: int = 0) -> int:
    """选项菜单，返回选择的索引

    支持上下键选择（交互式终端）或数字输入（非交互环境）
    返回 None 时表示用户取消（按 Esc/q），调用方应处理 None
    """
    return keyboard_select(prompt, options, default=default)


def section(title: str):
    """打印分节标题（带装饰边框）"""
    # 根据标题内容选择图标
    icon = ""
    if any(k in title for k in ["抖音", "TikTok", "短视频"]):
        icon = "🌐"
    elif any(k in title for k in ["YouTube", "流媒体", "B站"]):
        icon = "▶️ "
    elif any(k in title for k in ["本地", "视频文件"]):
        icon = "📁"
    elif any(k in title for k in ["字幕"]):
        icon = "📝"
    elif any(k in title for k in ["录制", "参考音频", "声音"]):
        icon = "🎙️ "
    elif any(k in title for k in ["配置", "设置"]):
        icon = "⚙️ "
    elif any(k in title for k in ["缓存", "清理"]):
        icon = "🧹"
    elif any(k in title for k in ["确认"]):
        icon = "✅"
    # 去掉 title 开头已有的图标
    clean_title = title
    for prefix in ["🌐 ", "▶️  ", "📁 ", "📝 ", "🎙️  ", "⚙️  ", "🧹 ", "✅ "]:
        if clean_title.startswith(prefix):
            clean_title = clean_title[len(prefix):]
            break
    display = f"{icon} {clean_title}".strip() if icon else title
    line = "─" * max(10, 60 - _disp_w(display))
    print(f"\n{Color.HEADER}┌── {display} {line}{Color.RESET}")


def info(label: str, value: str):
    """打印信息行（标签+值）"""
    print(f"  {Color.CYAN}▸ {label}:{Color.RESET} {value}")


def success(msg: str):
    """打印成功消息"""
    print(f"{Color.GREEN}✓ {msg}{Color.RESET}")


def warn(msg: str):
    """打印警告消息"""
    print(f"{Color.YELLOW}⚠ {msg}{Color.RESET}")


def error(msg: str):
    """打印错误消息"""
    print(f"{Color.RED}✗ {msg}{Color.RESET}")


# ==================== 交互流程 ====================

def _apply_global_proxy(config):
    """设置全局代理环境变量

    在程序启动时调用，让所有网络库都走代理：
    - HuggingFace 模型下载（huggingface_hub / requests）
    - yt-dlp 视频下载
    - OpenAI SDK
    - 直接使用 requests 的模块

    设置的环境变量：
    - HTTP_PROXY / HTTPS_PROXY（大写，requests/urllib3 读取）
    - http_proxy / https_proxy（小写，部分库读取）
    - ALL_PROXY（ socks 代理，部分库读取）
    - NO_PROXY（本地地址不走代理：localhost, 127.0.0.1, Ollama 地址）
    """
    proxy = getattr(config, "network_proxy", "") or ""
    if not proxy:
        return

    import os as _os

    # 标准化代理地址（补全 http:// 前缀）
    if not proxy.startswith(("http://", "https://", "socks5://", "socks4://")):
        proxy = f"http://{proxy}"

    _os.environ["HTTP_PROXY"] = proxy
    _os.environ["HTTPS_PROXY"] = proxy
    _os.environ["http_proxy"] = proxy
    _os.environ["https_proxy"] = proxy

    # 本地地址不走代理（重要：Ollama 服务在本地，必须排除）
    # 包括：localhost, 127.0.0.1, 0.0.0.0, ::1
    no_proxy = "localhost,127.0.0.1,0.0.0.0,::1"

    # 如果配置了 Ollama，把它的地址也加入 NO_PROXY
    ollama_url = getattr(config, "ollama_url", "") or ""
    if ollama_url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(ollama_url)
            if parsed.hostname:
                no_proxy += f",{parsed.hostname}"
        except Exception:
            pass

    _os.environ["NO_PROXY"] = no_proxy
    _os.environ["no_proxy"] = no_proxy

    print(f"{Color.DIM}[config] 全局代理已设置: {proxy}{Color.RESET}")
    print(f"{Color.DIM}[config] 本地地址不走代理: {no_proxy}{Color.RESET}")


def interactive_main():
    """交互式主流程"""
    banner()

    config = load_config()

    # 全局代理：在程序启动时设置，让所有网络请求都走代理
    # 包括：HuggingFace 模型下载、yt-dlp 视频下载、requests 库、OpenAI SDK 等
    _apply_global_proxy(config)

    # 依赖检查（必需缺失则退出，可选缺失给出提示）
    all_ok, missing_required, missing_optional = check_all(config)
    if missing_optional:
        print(f"{Color.YELLOW}⚠ 部分可选依赖/服务未就绪（不影响核心功能）：{Color.RESET}")
        for cat, module, pip_name, desc in missing_optional:
            cat_name = {"asr": "ASR", "translate": "翻译",
                        "tts": "TTS", "youtube": "YouTube",
                        "ollama": "Ollama"}.get(cat, cat)
            if pip_name == "_service_":
                # 服务未运行提示
                print(f"  {Color.YELLOW}- [{cat_name}] {desc}{Color.RESET}")
                if cat == "ollama":
                    print(f"  {Color.DIM}  启动: ollama serve{Color.RESET}")
            else:
                print(f"  {Color.DIM}- {pip_name} ({cat_name}: {desc}){Color.RESET}")
        # 只对 pip 包显示安装命令（排除服务）
        missing_pip = list({p for _, _, p, _ in missing_optional if p != "_service_"})
        if missing_pip:
            print(f"  {Color.DIM}一键安装：{get_install_command(missing_pip)}{Color.RESET}")
        print()
    if not all_ok:
        print(format_report(missing_required, missing_optional))
        sys.exit(1)

    # 显示当前配置状态
    section("当前配置")
    info("ASR 引擎", f"{config.asr_engine}" + (f" ({config.whisper_model})" if config.asr_engine == "whisper-api" else f" ({config.faster_whisper_model})"))
    if config.translate_engine == "ollama":
        info("翻译引擎", f"ollama / {config.ollama_model}")
    else:
        info("翻译引擎", f"{config.translate_engine} / {config.translate_model}")
    info("TTS 引擎", config.tts_engine)
    info("字幕", f"{config.subtitle_style} / {config.subtitle_fontsize}px / margin_v={config.subtitle_margin_v}")
    info("换行阈值", f"{config.subtitle_max_width_percent:.0%}")
    api_status = f"{Color.GREEN}已设置{Color.RESET}" if config.has_openai() else f"{Color.YELLOW}未设置（将使用免费方案）{Color.RESET}"
    info("OpenAI Key", api_status)
    print(f"  {Color.DIM}配置文件: {config.config_file_path or '(无)'}{Color.RESET}")

    # 主菜单
    while True:
        choice = choose("请选择操作", [
            "🌐 翻译抖音 / TikTok 视频",
            "▶️  翻译 YouTube / B站等流媒体视频",
            "📁 翻译本地视频文件",
            "📝 仅生成字幕（不配音）",
            "⚙️  修改配置",
            "🧹 清理缓存（清空 .work 目录）",
            "🚪 退出",
        ], default=0)

        if choice == 6 or choice is None:
            print(f"\n{Color.CYAN}再见！{Color.RESET}\n")
            break
        elif choice == 5:
            interactive_clear_cache(config)
        elif choice == 4:
            interactive_config(config)
        elif choice == 0:
            interactive_translate_douyin(config)
        elif choice == 1:
            interactive_translate_streaming(config)
        elif choice == 2:
            interactive_translate_video(config)
        elif choice == 3:
            interactive_subtitle_only(config)


def interactive_translate_douyin(config: Config):
    """交互式：翻译抖音 / TikTok 视频"""
    section("🌐 抖音 / TikTok 视频翻译")
    print(f"  {Color.DIM}请粘贴抖音 / TikTok 分享文本或链接（可直接粘贴整段分享文字）{Color.RESET}")
    print(f"  {Color.DIM}抖音示例: https://v.douyin.com/xxxxx/{Color.RESET}")
    print(f"  {Color.DIM}TikTok 示例: https://www.tiktok.com/@user/video/123{Color.RESET}")
    print(f"  {Color.DIM}TikTok 短链: https://vm.tiktok.com/ZMxxxxxxx/{Color.RESET}")
    print(f"  {Color.YELLOW}⚠ TikTok 在国内需要配置代理{Color.RESET}")
    if not config.tiktok_cookies_browser:
        print(f"  {Color.YELLOW}⚠ TikTok 反爬严格，若下载失败请在「修改配置」中设置 TikTok cookies 浏览器{Color.RESET}")
    else:
        print(f"  {Color.GREEN}✓ TikTok cookies 已配置: {config.tiktok_cookies_browser}{Color.RESET}")
    print()

    share_text = input(f"{Color.CYAN}分享文本或链接>{Color.RESET} ").strip()
    if not share_text:
        warn("未输入")
        pause()
        return

    # 目标语言选择
    lang_idx = choose("目标语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=1)
    if lang_idx is None:
        return
    target_lang = ["zh", "en", "ja", "ko"][lang_idx]

    # 高级选项
    subtitle_only = False
    keep_audio = False
    if confirm("进入高级选项?", default_yes=False):
        subtitle_only = confirm("仅生成字幕（不配音）?", default_yes=False)
        if not subtitle_only:
            keep_audio = confirm("保留原视频音频（背景音+配音）?", default_yes=False)

    # 确认开始
    section("确认")
    # 识别源类型
    from modules.downloader import VideoDownloader
    dl = VideoDownloader(config)
    if dl.is_tiktok_link(share_text):
        source_type = "TikTok 视频"
    elif dl.is_douyin_link(share_text):
        source_type = "抖音视频"
    else:
        source_type = "抖音/TikTok 链接"
    info("源", source_type)
    info("目标语言", target_lang)
    info("仅字幕", "是" if subtitle_only else "否")
    info("保留原音", "是" if keep_audio else "否")
    info("字幕样式", config.subtitle_style)
    info("字幕字号", str(config.subtitle_fontsize))

    if not confirm("\n开始翻译?"):
        warn("已取消")
        pause()
        return

    # 执行
    from pipeline import TranslationPipeline
    pipeline = TranslationPipeline(config)
    try:
        result = pipeline.run(
            source=share_text,
            target_lang=target_lang,
            source_lang="auto",
            subtitle_only=subtitle_only,
            keep_original_audio=keep_audio,
        )
        if result:
            success(f"翻译完成！输出目录: {pipeline.session_output_dir}")
    except Exception as e:
        error(f"翻译失败: {e}")
        import traceback
        traceback.print_exc()
    pause()


def interactive_translate_streaming(config: Config):
    """交互式：翻译 YouTube/B站等流媒体视频"""
    section("▶️  YouTube / 流媒体视频翻译")
    print(f"  {Color.DIM}支持 YouTube、B站、Vimeo、Twitter、Twitch 等数千个网站{Color.RESET}")
    print(f"  {Color.DIM}基于 yt-dlp 解析和下载{Color.RESET}")
    print(f"  {Color.DIM}示例:{Color.RESET}")
    print(f"  {Color.DIM}  https://www.youtube.com/watch?v=dQw4w9WgXcQ{Color.RESET}")
    print(f"  {Color.DIM}  https://youtu.be/dQw4w9WgXcQ{Color.RESET}")
    print(f"  {Color.DIM}  https://www.bilibili.com/video/BVxxxxx{Color.RESET}\n")

    # 代理提示
    if not config.network_proxy:
        print(f"  {Color.YELLOW}⚠ 未配置代理{Color.RESET}")
        print(f"  {Color.DIM}国内访问 YouTube 需要代理，请在配置中设置 network.proxy{Color.RESET}")
        print(f"  {Color.DIM}如 http://127.0.0.1:7890 (Clash/V2Ray){Color.RESET}\n")

    url = input(f"{Color.CYAN}视频URL>{Color.RESET} ").strip()
    if not url:
        warn("未输入")
        pause()
        return

    # 目标语言
    lang_idx = choose("目标语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=1)
    if lang_idx is None:
        return
    target_lang = ["zh", "en", "ja", "ko"][lang_idx]

    # 源语言
    source_lang = "auto"
    if not confirm("自动检测源语言?", default_yes=True):
        src_idx = choose("源语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=0)
        if src_idx is None:
            source_lang = "auto"  # 取消时用自动检测
        else:
            source_lang = ["zh", "en", "ja", "ko"][src_idx]

    # 高级选项
    subtitle_only = False
    keep_audio = False
    if confirm("进入高级选项?", default_yes=False):
        subtitle_only = confirm("仅生成字幕（不配音）?", default_yes=False)
        if not subtitle_only:
            keep_audio = confirm("保留原视频音频?", default_yes=False)

    # 确认
    section("确认")
    info("源", "YouTube/流媒体")
    info("URL", url[:60])
    info("目标语言", target_lang)
    info("仅字幕", "是" if subtitle_only else "否")
    info("保留原音", "是" if keep_audio else "否")
    info("代理", config.network_proxy if config.network_proxy else "无")

    if not confirm("\n开始翻译?"):
        warn("已取消")
        pause()
        return

    from pipeline import TranslationPipeline
    pipeline = TranslationPipeline(config)
    try:
        result = pipeline.run(
            source=url,
            target_lang=target_lang,
            source_lang=source_lang,
            subtitle_only=subtitle_only,
            keep_original_audio=keep_audio,
        )
        if result:
            success(f"翻译完成！输出目录: {pipeline.session_output_dir}")
    except Exception as e:
        err_msg = str(e)
        if "Sign in to confirm" in err_msg or "bot" in err_msg.lower():
            error("YouTube 反爬拦截，需要配置代理")
            print(f"  {Color.DIM}解决方法：编辑 .env 文件{Color.RESET}")
            print(f"  {Color.DIM}  NETWORK_PROXY=http://127.0.0.1:7890{Color.RESET}")
        elif "Proxy" in err_msg or "proxy" in err_msg.lower():
            error("代理连接失败，请检查代理配置")
        else:
            error(f"翻译失败: {e}")
        import traceback
        traceback.print_exc()
    pause()


def interactive_translate_video(config: Config):
    """交互式：翻译本地视频"""
    section("📁 本地视频翻译")
    print(f"  {Color.DIM}请输入视频文件路径（可拖拽文件到窗口）{Color.RESET}\n")

    video_path = input(f"{Color.CYAN}视频路径>{Color.RESET} ").strip().strip('"').strip("'")
    if not video_path or not os.path.isfile(video_path):
        warn(f"文件不存在: {video_path}")
        pause()
        return

    # 目标语言
    lang_idx = choose("目标语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=1)
    if lang_idx is None:
        return
    target_lang = ["zh", "en", "ja", "ko"][lang_idx]

    # 源语言（通常自动检测）
    source_lang = "auto"
    if not confirm("自动检测源语言?", default_yes=True):
        src_idx = choose("源语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=0)
        if src_idx is None:
            source_lang = "auto"  # 取消时用自动检测
        else:
            source_lang = ["zh", "en", "ja", "ko"][src_idx]

    # 高级选项
    subtitle_only = False
    keep_audio = False
    if confirm("进入高级选项?", default_yes=False):
        subtitle_only = confirm("仅生成字幕（不配音）?", default_yes=False)
        if not subtitle_only:
            keep_audio = confirm("保留原视频音频?", default_yes=False)

    if not confirm("\n开始翻译?"):
        warn("已取消")
        pause()
        return

    from pipeline import TranslationPipeline
    pipeline = TranslationPipeline(config)
    try:
        result = pipeline.run(
            source=video_path,
            target_lang=target_lang,
            source_lang=source_lang,
            subtitle_only=subtitle_only,
            keep_original_audio=keep_audio,
        )
        if result:
            success(f"翻译完成！输出目录: {pipeline.session_output_dir}")
    except Exception as e:
        error(f"翻译失败: {e}")
        import traceback
        traceback.print_exc()
    pause()


def interactive_subtitle_only(config: Config):
    """交互式：仅生成字幕"""
    section("📝 仅生成字幕（不配音）")
    print(f"  {Color.DIM}适用于已有音频不想替换，只想烧录字幕的场景{Color.RESET}\n")

    # 选择源
    src_choice = choose("视频来源", ["抖音/TikTok 链接", "本地文件"], default=0)
    if src_choice is None:
        return
    if src_choice == 0:
        share_text = input(f"{Color.CYAN}抖音/TikTok 分享文本或链接>{Color.RESET} ").strip()
        if not share_text:
            warn("未输入")
            pause()
            return
        source = share_text
    else:
        video_path = input(f"{Color.CYAN}视频路径>{Color.RESET} ").strip().strip('"').strip("'")
        if not video_path or not os.path.isfile(video_path):
            warn(f"文件不存在")
            pause()
            return
        source = video_path

    lang_idx = choose("目标语言", ["中文 (zh)", "英文 (en)", "日文 (ja)", "韩文 (ko)"], default=1)
    if lang_idx is None:
        return
    target_lang = ["zh", "en", "ja", "ko"][lang_idx]

    if not confirm("\n开始生成字幕?"):
        warn("已取消")
        pause()
        return

    from pipeline import TranslationPipeline
    pipeline = TranslationPipeline(config)
    try:
        result = pipeline.run(
            source=source,
            target_lang=target_lang,
            subtitle_only=True,
        )
        if result:
            success(f"字幕生成完成！输出目录: {pipeline.session_output_dir}")
    except Exception as e:
        error(f"失败: {e}")
        import traceback
        traceback.print_exc()
    pause()


def interactive_clear_cache(config: Config):
    """交互式：清理 .work 缓存目录

    清空所有中间产物（ASR、TTS、字幕中间文件、混合音频）。
    已完成的输出视频不受影响（在 output/ 目录下）。
    """
    import shutil
    section("🧹 清理缓存")
    work_dir = Path(config.work_dir)

    if not work_dir.exists():
        info("缓存目录", f"{work_dir} 不存在")
        pause()
        return

    # 统计文件
    all_files = [f for f in work_dir.rglob("*") if f.is_file()]
    file_count = len(all_files)
    total_size = sum(f.stat().st_size for f in all_files)
    size_mb = total_size / (1024 * 1024)

    info("缓存目录", str(work_dir))
    info("文件数量", f"{file_count} 个")
    info("占用空间", f"{size_mb:.1f} MB")

    # 列出主要子目录和文件类型分布
    subdirs = [d for d in work_dir.iterdir() if d.is_dir()]
    root_files = [f for f in work_dir.iterdir() if f.is_file()]
    print(f"\n{Color.DIM}目录结构:{Color.RESET}")
    print(f"  子目录: {len(subdirs)} 个")
    print(f"  根目录散落文件: {len(root_files)} 个")
    if subdirs:
        print(f"\n{Color.DIM}子目录详情（前 10 个）:{Color.RESET}")
        for d in subdirs[:10]:
            try:
                sub_files = [f for f in d.rglob("*") if f.is_file()]
                sub_size = sum(f.stat().st_size for f in sub_files)
                print(f"  {Color.CYAN}{d.name}/{Color.RESET}  ({len(sub_files)} 文件, {sub_size/1024/1024:.1f} MB)")
            except Exception:
                print(f"  {d.name}/  (读取失败)")
        if len(subdirs) > 10:
            print(f"  {Color.DIM}... 共 {len(subdirs)} 个子目录{Color.RESET}")

    # 确认删除
    print(f"\n{Color.YELLOW}⚠ 此操作将删除 .work 目录下所有文件（ASR、TTS、字幕中间产物）{Color.RESET}")
    print(f"{Color.DIM}已完成的输出视频不受影响（在 output/ 目录下）{Color.RESET}")
    print(f"{Color.DIM}下次运行时会自动创建新的工作目录{Color.RESET}")

    if file_count == 0:
        print(f"\n{Color.GREEN}✓ 缓存目录已为空{Color.RESET}")
        pause()
        return

    if not confirm("\n确认清理所有缓存?", default_yes=False):
        warn("已取消")
        pause()
        return

    # 执行清理
    try:
        shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=True)
        success(f"已清理 {file_count} 个文件，释放 {size_mb:.1f} MB 空间")
    except Exception as e:
        error(f"清理失败: {e}")
        # 尝试逐个删除
        try:
            deleted = 0
            for f in all_files:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    pass
            # 清理空目录
            for d in sorted(subdirs, key=lambda x: len(str(x)), reverse=True):
                try:
                    d.rmdir()
                except Exception:
                    pass
            success(f"部分清理: 删除 {deleted}/{file_count} 个文件")
        except Exception as e2:
            error(f"逐个删除也失败: {e2}")
    pause()


def interactive_config(config: Config):
    """交互式：修改配置"""
    while True:
        section("⚙️  配置管理")
        info("ASR 引擎", f"{config.asr_engine}" + (f" ({config.whisper_model})" if config.asr_engine == "whisper-api" else f" ({config.faster_whisper_model})"))
        if config.translate_engine == "ollama":
            info("翻译引擎", f"ollama / {config.ollama_model}")
        else:
            info("翻译引擎", f"{config.translate_engine} / {config.translate_model}")
        info("TTS 引擎", config.tts_engine)
        info("TTS 音色", f"zh={config.tts_voice_zh} / en={config.tts_voice_en}")
        info("字幕样式", f"{config.subtitle_style} / {config.subtitle_fontsize}px / margin_v={config.subtitle_margin_v}")
        info("换行阈值", f"{config.subtitle_max_width_percent:.0%}")
        info("OpenAI Key", "已设置" if config.has_openai() else "未设置")
        if config.has_openai():
            info("OpenAI URL", config.openai_base_url)
        info("代理", config.network_proxy or "未设置")
        info("TikTok cookies", config.tiktok_cookies_browser or "未设置")
        info("配置文件", config.config_file_path or "(无)")

        choice = choose("修改项", [
            "更改 ASR 引擎 / Whisper 模型",
            "更改翻译引擎 / Ollama 模型",
            "更改 TTS 引擎 / 音色",
            "更改字幕设置（样式/字号/边距/换行）",
            "OpenAI 配置（API Key / URL / 翻译模型 / 备选模型 / Whisper API）",
            "设置网络代理（YouTube 必需）",
            "设置 TikTok cookies 浏览器（绕过反爬）",
            "查看完整配置文件",
            "返回",
        ], default=8)

        if choice == 8 or choice is None:
            return
        elif choice == 0:
            _config_asr(config)
        elif choice == 1:
            _config_translate(config)
        elif choice == 2:
            _config_tts(config)
        elif choice == 3:
            _config_subtitle(config)
        elif choice == 4:
            _config_openai(config)
        elif choice == 5:
            # 设置网络代理
            print(f"\n{Color.DIM}当前代理: {config.network_proxy or '未设置'}{Color.RESET}")
            print(f"{Color.DIM}YouTube 国内访问需要代理，常用代理地址:{Color.RESET}")
            print(f"{Color.DIM}  Clash:  http://127.0.0.1:7890{Color.RESET}")
            print(f"{Color.DIM}  V2Ray:  http://127.0.0.1:10809{Color.RESET}")
            print(f"{Color.DIM}  留空清除代理设置{Color.RESET}\n")
            proxy = input(f"{Color.CYAN}代理地址: {Color.RESET}").strip()
            if proxy == "":
                if confirm("确认清除代理?", default_yes=False):
                    config.network_proxy = ""
                    success("已清除代理")
                    _save_config(config)
            else:
                config.network_proxy = proxy
                success(f"代理已设置为: {proxy}")
                _save_config(config)
        elif choice == 6:
            # 设置 TikTok cookies 浏览器（用上下键选择）
            print(f"\n{Color.DIM}TikTok 反爬严格，经常报 \"Unable to extract universal data\"{Color.RESET}")
            print(f"{Color.DIM}解决方法：在浏览器中登录 https://www.tiktok.com 后，{Color.RESET}")
            print(f"{Color.DIM}选择对应浏览器，程序会自动提取 cookies 绕过反爬{Color.RESET}")

            browsers = ["chrome", "firefox", "edge", "brave", "opera", "safari"]
            options = []
            for b in browsers:
                marker = f" {Color.GREEN}← 当前{Color.RESET}" if b == config.tiktok_cookies_browser.lower() else ""
                options.append(f"{b}{marker}")
            options.append(f"清除设置（不使用 cookies）")
            options.append("返回")

            b_idx = choose("选择 TikTok cookies 浏览器", options, default=0)
            if b_idx is None:
                pass  # 取消
            elif b_idx < len(browsers):
                config.tiktok_cookies_browser = browsers[b_idx]
                success(f"TikTok cookies 浏览器已设置为: {browsers[b_idx]}")
                print(f"  {Color.DIM}请确保已在该浏览器中登录 https://www.tiktok.com{Color.RESET}")
                _save_config(config)
            elif b_idx == len(browsers):
                config.tiktok_cookies_browser = ""
                success("已清除 TikTok cookies 设置")
                _save_config(config)
        elif choice == 7:
            if config.config_file_path and os.path.isfile(config.config_file_path):
                print(f"\n{Color.HEADER}=== 配置文件内容 ==={Color.RESET}\n")
                with open(config.config_file_path, "r", encoding="utf-8") as f:
                    print(f.read())
            else:
                warn("未找到配置文件")
            pause()


def _config_openai(config: Config):
    """OpenAI 配置：API Key / Base URL / 翻译模型 / 备选模型 / ASR API 模型"""
    last_choice = 0  # 记住上次选择，设置完不跳回"返回"
    while True:
        section("OpenAI 配置")
        key_display = (config.openai_api_key[:8] + "..." + config.openai_api_key[-4:]) if config.has_openai() else "未设置"
        info("API Key", key_display)
        info("Base URL", config.openai_base_url)
        info("翻译模型", config.translate_model)
        info("备选模型", ", ".join(config.translate_model_fallbacks) if config.translate_model_fallbacks else "无")
        info("ASR API 模型", config.whisper_model or "whisper-1")

        sub = choose("OpenAI 选项", [
            "设置 API Key",
            "设置 API Base URL",
            "设置翻译模型",
            "设置备选翻译模型链",
            "设置 ASR API 模型",
            "返回",
        ], default=last_choice)

        if sub is None or sub == 5:
            return
        last_choice = sub

        if sub == 0:
            print(f"\n{Color.DIM}当前: {key_display}{Color.RESET}")
            print(f"{Color.DIM}支持 OpenAI 官方或第三方兼容 API{Color.RESET}")
            key = input(f"{Color.CYAN}输入 API Key (留空跳过): {Color.RESET}").strip()
            if key:
                config.openai_api_key = key
                success("API Key 已设置")
                _save_config(config)
        elif sub == 1:
            print(f"\n{Color.DIM}当前: {config.openai_base_url}{Color.RESET}")
            print(f"{Color.DIM}OpenAI 官方: https://api.openai.com/v1{Color.RESET}")
            print(f"{Color.DIM}第三方兼容 API 示例:{Color.RESET}")
            print(f"{Color.DIM}  DeepSeek: https://api.deepseek.com/v1{Color.RESET}")
            print(f"{Color.DIM}  API2D:    https://oa.api2d.net/v1{Color.RESET}")
            print(f"{Color.DIM}  OpenRouter: https://openrouter.ai/api/v1{Color.RESET}")
            url = input_with_default("API Base URL", config.openai_base_url)
            if url:
                config.openai_base_url = url
                success(f"Base URL 已设置为: {url}")
                _save_config(config)
        elif sub == 2:
            print(f"\n{Color.DIM}当前: {config.translate_model}{Color.RESET}")
            print(f"{Color.DIM}常见模型: gpt-4o-mini / gpt-4o / gpt-3.5-turbo / deepseek-chat{Color.RESET}")
            model = input_with_default("翻译模型", config.translate_model)
            if model:
                config.translate_model = model
                success(f"翻译模型已设为: {model}")
                _save_config(config)
        elif sub == 3:
            print(f"\n{Color.DIM}当前备选模型链: {', '.join(config.translate_model_fallbacks) if config.translate_model_fallbacks else '无'}{Color.RESET}")
            print(f"{Color.DIM}主模型失败时，按顺序尝试备选模型{Color.RESET}")
            print(f"{Color.DIM}输入逗号分隔的模型名，如: gpt-4o-mini,gpt-3.5-turbo,gpt-4o{Color.RESET}")
            raw = input_with_default("备选模型链（逗号分隔，留空清除）", ",".join(config.translate_model_fallbacks))
            if raw.strip():
                config.translate_model_fallbacks = [m.strip() for m in raw.split(",") if m.strip()]
            else:
                config.translate_model_fallbacks = []
            success(f"备选模型链已设为: {', '.join(config.translate_model_fallbacks) or '无'}")
            _save_config(config)
        elif sub == 4:
            print(f"\n{Color.DIM}当前: {config.whisper_model or 'whisper-1'}{Color.RESET}")
            print(f"{Color.DIM}ASR API 模型（云端语音识别，需 ASR_ENGINE=whisper-api）{Color.RESET}")
            print(f"{Color.DIM}默认使用 whisper-1{Color.RESET}")
            model = input_with_default("ASR API 模型", config.whisper_model or "whisper-1")
            if model:
                config.whisper_model = model
                success(f"ASR API 模型已设为: {model}")
                _save_config(config)


def _config_translate(config: Config):
    """翻译引擎配置：切换引擎 + Ollama 模型选择"""
    last_choice = 0
    while True:
        section("翻译引擎配置")
        info("当前引擎", config.translate_engine)
        if config.translate_engine == "openai":
            info("翻译模型", config.translate_model)
        elif config.translate_engine == "ollama":
            info("Ollama 模型", config.ollama_model)
            info("Ollama URL", config.ollama_url)

        sub = choose("翻译选项", [
            "切换翻译引擎",
            "选择 Ollama 模型（列出本地已安装）" if config.translate_engine == "ollama" else "选择 Ollama 模型",
            "设置 Ollama 服务地址",
            "返回",
        ], default=last_choice)

        if sub is None or sub == 3:
            return
        last_choice = sub

        if sub == 0:
            idx = choose("翻译引擎", [
                "openai (GPT, 需 Key)",
                "ollama (本地免费, 需安装 Ollama)",
                "google (免费, 国内可能不可用)",
                "mymemory (免费, 国内可用, 有配额)",
            ], default={"openai":0, "ollama":1, "google":2, "mymemory":3}.get(config.translate_engine, 0))
            if idx is None:
                pass  # 取消
            else:
                config.translate_engine = ["openai", "ollama", "google", "mymemory"][idx]
                success(f"翻译引擎已更改为 {config.translate_engine}")
                if config.translate_engine == "openai":
                    print(f"  {Color.DIM}请在「OpenAI 配置」中设置 API Key 和模型{Color.RESET}")
                elif config.translate_engine == "ollama":
                    print(f"  {Color.DIM}请确保 Ollama 服务已启动{Color.RESET}")
                _save_config(config)
        elif sub == 1:
            _config_ollama_model(config)
        elif sub == 2:
            url = input_with_default("Ollama 服务地址", config.ollama_url)
            if url:
                config.ollama_url = url
                success(f"Ollama 服务地址已设为: {url}")
                _save_config(config)


def _config_ollama_model(config: Config):
    """Ollama 模型选择：列出本地已安装模型供选择"""
    from modules.ollama_translator import is_ollama_running, list_installed_models, RECOMMENDED_MODELS

    running = is_ollama_running(config.ollama_url)
    if not running:
        warn(f"Ollama 服务未运行: {config.ollama_url}")
        print(f"  {Color.DIM}请先安装并启动 Ollama:{Color.RESET}")
        print(f"  {Color.DIM}  1. 下载: https://ollama.com{Color.RESET}")
        print(f"  {Color.DIM}  2. 启动: ollama serve{Color.RESET}")
        print(f"  {Color.DIM}  3. 拉模型: ollama pull qwen2.5:7b{Color.RESET}")
        new_model = input_with_default("手动输入模型名", config.ollama_model)
        if new_model:
            config.ollama_model = new_model
            success(f"Ollama 模型已设为: {new_model}")
            _save_config(config)
        return

    success(f"Ollama 服务运行中: {config.ollama_url}")
    installed = list_installed_models(config.ollama_url)

    if installed:
        print(f"  {Color.DIM}已安装 {len(installed)} 个模型{Color.RESET}")
        options = []
        for m in installed:
            marker = f" {Color.GREEN}← 当前{Color.RESET}" if m == config.ollama_model else ""
            options.append(f"{m}{marker}")
        options.append("手动输入其他模型名")
        options.append("返回")

        m_idx = choose("选择 Ollama 模型", options, default=0)
        if m_idx is None:
            pass  # 取消
        elif m_idx < len(installed):
            config.ollama_model = installed[m_idx]
            success(f"Ollama 模型已设为: {installed[m_idx]}")
            _save_config(config)
        elif m_idx == len(installed):
            new_model = input_with_default("模型名 (如 qwen2.5:7b)", config.ollama_model)
            if new_model:
                config.ollama_model = new_model
                success(f"Ollama 模型已设为: {new_model}")
                _save_config(config)
    else:
        print(f"  {Color.YELLOW}⚠ 未安装任何模型{Color.RESET}")
        print(f"  {Color.DIM}推荐模型:{Color.RESET}")
        for name, desc in RECOMMENDED_MODELS[:5]:
            print(f"    {Color.CYAN}{name}{Color.RESET} - {desc}")
        print(f"  {Color.DIM}安装命令: ollama pull <模型名>{Color.RESET}")
        new_model = input_with_default("输入要使用的模型名", config.ollama_model)
        if new_model:
            config.ollama_model = new_model
            success(f"Ollama 模型已设为: {new_model}")
            print(f"  {Color.DIM}如未安装，请运行: ollama pull {new_model}{Color.RESET}")
            _save_config(config)


def _config_asr(config: Config):
    """ASR 引擎 + Whisper 模型配置"""
    last_choice = 0
    while True:
        section("ASR 引擎配置")
        info("当前引擎", config.asr_engine)
        if config.asr_engine == "whisper-api":
            info("ASR API 模型", config.whisper_model or "whisper-1")
        else:
            info("faster-whisper 模型", config.faster_whisper_model)

        sub = choose("ASR 选项", [
            "切换 ASR 引擎",
            "选择 faster-whisper 模型版本（本地，首次使用自动下载）",
            "返回",
        ], default=last_choice)

        if sub is None or sub == 2:
            return
        last_choice = sub

        if sub == 0:
            idx = choose("ASR 引擎", [
                "whisper-api (OpenAI 云端, 需 Key, 速度快)",
                "faster-whisper (本地免费, 首次需下载模型)",
            ], default=0 if config.asr_engine == "whisper-api" else 1)
            if idx is None:
                pass  # 取消
            else:
                config.asr_engine = ["whisper-api", "faster-whisper"][idx]
                success(f"ASR 引擎已更改为 {config.asr_engine}")
                _save_config(config)
        elif sub == 1:
            # faster-whisper 模型选择
            models = [
                ("tiny",    "39MB",  "最快, 精度最低, 适合快速预览"),
                ("base",    "142MB", "较快, 精度一般, 推荐入门"),
                ("small",   "466MB", "中等, 精度较好, 平衡选择"),
                ("medium",  "1.5GB", "较慢, 精度高, 需 GPU 加速"),
                ("large-v3","3GB",   "最慢, 精度最高, 强烈建议 GPU"),
            ]
            print(f"\n{Color.DIM}faster-whisper 模型版本（首次使用时自动从 HuggingFace 下载）:{Color.RESET}\n")
            options = []
            for name, size, desc in models:
                current = f" {Color.GREEN}← 当前{Color.RESET}" if name == config.faster_whisper_model else ""
                options.append(f"{name} ({size}) - {desc}{current}")
            options.append("返回")

            # 找到当前模型在列表中的位置作为默认选中
            default_idx = 0
            for i, (name, _, _) in enumerate(models):
                if name == config.faster_whisper_model:
                    default_idx = i
                    break

            m_idx = choose("选择模型", options, default=default_idx)
            if m_idx is None or m_idx == len(models):
                pass  # 取消或返回
            elif m_idx < len(models):
                model_name = models[m_idx][0]
                model_size = models[m_idx][1]
                config.faster_whisper_model = model_name
                success(f"faster-whisper 模型已设为: {model_name} ({model_size})")
                print(f"  {Color.DIM}模型将在首次使用 faster-whisper 时自动下载到 .models/ 目录{Color.RESET}")
                print(f"  {Color.DIM}国内建议配置代理以加速下载，或使用 HuggingFace 镜像（已内置）{Color.RESET}")

                # 检查模型是否已下载
                import os as _os
                models_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), ".models")
                # HuggingFace 实际目录格式: models--Systran--faster-whisper-{model_name}
                model_dirs = [d for d in _os.listdir(models_dir) if d.startswith("models--")] if _os.path.isdir(models_dir) else []
                # 用模型名后缀匹配，兼容不同组织前缀（Systran / 其他）
                expected_suffix = f"faster-whisper-{model_name}"
                already = any(expected_suffix in d for d in model_dirs)
                if already:
                    print(f"  {Color.GREEN}✓ 该模型已下载{Color.RESET}")
                else:
                    print(f"  {Color.YELLOW}ⓘ 该模型尚未下载，将在首次使用时自动下载{Color.RESET}")
                    if confirm("现在预下载该模型?", default_yes=False):
                        from modules.transcriber import pre_download_faster_whisper_model
                        pre_download_faster_whisper_model(model_name, config)

                _save_config(config)


# edge-tts 常用音色列表
EDGE_TTS_VOICES = {
    "zh": [
        ("zh-CN-YunxiNeural",    "云希 (男, 沉稳)"),
        ("zh-CN-XiaoxiaoNeural", "晓晓 (女, 温柔)"),
        ("zh-CN-YunyangNeural",  "云扬 (男, 新闻)"),
        ("zh-CN-XiaoyiNeural",   "晓伊 (女, 活泼)"),
        ("zh-CN-YunjianNeural",  "云健 (男, 运动)"),
        ("zh-CN-XiaochenNeural", "晓辰 (女, 知性)"),
    ],
    "en": [
        ("en-US-GuyNeural",        "Guy (男, 沉稳)"),
        ("en-US-AriaNeural",       "Aria (女, 自然)"),
        ("en-US-ChristopherNeural","Christopher (男, 深沉)"),
        ("en-US-JennyNeural",      "Jenny (女, 亲切)"),
        ("en-GB-RyanNeural",       "Ryan (男, 英式)"),
        ("en-GB-SoniaNeural",      "Sonia (女, 英式)"),
    ],
    "ja": [
        ("ja-JP-KeitaNeural",  "圭太 (男)"),
        ("ja-JP-NanamiNeural", "七海 (女)"),
    ],
    "ko": [
        ("ko-KR-InJoonNeural", "仁俊 (男)"),
        ("ko-KR-SunHiNeural",   "善熙 (女)"),
    ],
}


def _config_tts(config: Config):
    """TTS 引擎 + 音色配置"""
    last_choice = 0
    while True:
        section("TTS 配音配置")
        info("当前引擎", config.tts_engine)
        info("中文音色", f"{config.tts_voice_zh}")
        info("英文音色", f"{config.tts_voice_en}")
        info("日文音色", f"{config.tts_voice_ja}")
        info("韩文音色", f"{config.tts_voice_ko}")
        info("语速", config.tts_rate)
        info("音量", config.tts_volume)

        sub = choose("TTS 选项", [
            "切换 TTS 引擎",
            "选择中文音色",
            "选择英文音色",
            "选择日文音色",
            "选择韩文音色",
            "调整语速 / 音量",
            "返回",
        ], default=last_choice)

        if sub is None or sub == 6:
            return
        last_choice = sub

        if sub == 0:
            idx = choose("TTS 引擎", [
                "edge (免费, 微软在线)",
                "azure (需 Key, 质量更好)",
            ], default={"edge":0, "azure":1}.get(config.tts_engine, 0))
            if idx is None:
                pass  # 取消
            else:
                config.tts_engine = ["edge", "azure"][idx]
                success(f"TTS 引擎已更改为 {config.tts_engine}")
                _save_config(config)
        elif 1 <= sub <= 4:
            lang_map = {1: ("zh", "tts_voice_zh", "中文"),
                        2: ("en", "tts_voice_en", "英文"),
                        3: ("ja", "tts_voice_ja", "日文"),
                        4: ("ko", "tts_voice_ko", "韩文")}
            lang_code, attr_name, lang_label = lang_map[sub]
            voices = EDGE_TTS_VOICES.get(lang_code, [])
            current_voice = getattr(config, attr_name)
            options = []
            # 找到当前音色在列表中的位置
            default_idx = 0
            for i, (vid, vdesc) in enumerate(voices):
                marker = f" {Color.GREEN}← 当前{Color.RESET}" if vid == current_voice else ""
                options.append(f"{vid} - {vdesc}{marker}")
                if vid == current_voice:
                    default_idx = i
            options.append("手动输入音色 ID")
            options.append("返回")

            v_idx = choose(f"选择{lang_label}音色", options, default=default_idx)
            if v_idx is None or v_idx == len(voices) + 1:
                pass  # 取消或返回
            elif v_idx < len(voices):
                setattr(config, attr_name, voices[v_idx][0])
                success(f"{lang_label}音色已设为: {voices[v_idx][0]}")
                _save_config(config)
            elif v_idx == len(voices):
                # 手动输入
                vid = input_with_default(f"输入{lang_label}音色 ID", current_voice)
                if vid:
                    setattr(config, attr_name, vid)
                    success(f"{lang_label}音色已设为: {vid}")
                    _save_config(config)
        elif sub == 5:
            # 语速和音量
            rate = input_with_default("语速 (如 +0% / +10% / -10%)", config.tts_rate)
            config.tts_rate = rate
            volume = input_with_default("音量 (如 +0% / +10% / -10%)", config.tts_volume)
            config.tts_volume = volume
            success(f"语速={rate}, 音量={volume}")
            _save_config(config)


def _config_subtitle(config: Config):
    """字幕配置：样式/字号/边距/换行/颜色"""
    last_choice = 0
    while True:
        section("字幕设置")
        info("样式", f"{config.subtitle_style} ({'双语' if config.subtitle_style == 'dual' else '仅目标语言'})")
        info("字体", f"{config.subtitle_font} / {config.subtitle_fontsize}px")
        info("边距", f"margin_v={config.subtitle_margin_v}")
        info("换行阈值", f"{config.subtitle_max_width_percent:.0%}")
        info("最大行数", str(config.subtitle_max_lines))
        info("颜色", f"文字={config.subtitle_primary_color} / 背景={config.subtitle_outline_color}")

        sub = choose("字幕选项", [
            "字幕样式 (单语/双语)",
            "字幕字号",
            "字幕边距 margin_v",
            "换行阈值",
            "最大行数",
            "字体",
            "返回",
        ], default=last_choice)

        if sub is None or sub == 6:
            return
        last_choice = sub

        if sub == 0:
            idx = choose("字幕样式", ["single (仅目标语言)", "dual (双语: 原文+译文)"],
                         default=0 if config.subtitle_style == "single" else 1)
            if idx is None:
                pass  # 取消
            else:
                config.subtitle_style = ["single", "dual"][idx]
                success(f"字幕样式已设为 {config.subtitle_style}")
                _save_config(config)
        elif sub == 1:
            size = input_with_default("字幕字号 (12-72)", str(config.subtitle_fontsize))
            try:
                config.subtitle_fontsize = max(12, min(72, int(size)))
                success(f"字号已设为 {config.subtitle_fontsize}")
                _save_config(config)
            except ValueError:
                warn("无效字号")
        elif sub == 2:
            mv = input_with_default("字幕距底部边距 (0-200)", str(config.subtitle_margin_v))
            try:
                config.subtitle_margin_v = max(0, min(300, int(mv)))
                success(f"margin_v 已设为 {config.subtitle_margin_v}")
                _save_config(config)
            except ValueError:
                warn("无效数值")
        elif sub == 3:
            pct = input_with_default("换行阈值 (0.3-0.9, 如 0.6)", f"{config.subtitle_max_width_percent}")
            try:
                config.subtitle_max_width_percent = max(0.3, min(0.9, float(pct)))
                success(f"换行阈值已设为 {config.subtitle_max_width_percent:.0%}")
                _save_config(config)
            except ValueError:
                warn("无效数值")
        elif sub == 4:
            ml = input_with_default("最大行数 (1-4)", str(config.subtitle_max_lines))
            try:
                config.subtitle_max_lines = max(1, min(4, int(ml)))
                success(f"最大行数已设为 {config.subtitle_max_lines}")
                _save_config(config)
            except ValueError:
                warn("无效数值")
        elif sub == 5:
            font = input_with_default("字体名称 (如 Arial / 微软雅黑 / SimHei)", config.subtitle_font)
            if font:
                config.subtitle_font = font
                success(f"字体已设为 {font}")
                _save_config(config)


def _save_config(config: Config):
    """保存配置到 YAML 文件"""
    from config import save_config
    if not config.config_file_path:
        config.config_file_path = CONFIG_FILE_NAME
    save_config(config, config.config_file_path)
    success(f"配置已保存到: {config.config_file_path}")


# ==================== 命令行模式（保留兼容） ====================

def add_common_args(parser):
    parser.add_argument("-t", "--target-lang", required=True, help="目标语言代码")
    parser.add_argument("-s", "--source-lang", default="auto", help="源语言代码")
    parser.add_argument("-o", "--output-dir", default=None, help="输出目录")
    parser.add_argument("--asr-engine", choices=["whisper-api", "faster-whisper"], default=None)
    parser.add_argument("--translate-engine", choices=["openai", "ollama", "google", "mymemory"], default=None)
    parser.add_argument("--tts-engine", choices=["edge", "azure"], default=None)
    parser.add_argument("--translate-model", default=None)
    parser.add_argument("--whisper-model", default=None)
    parser.add_argument("--faster-whisper-model", default=None, help="faster-whisper 模型版本: tiny/base/small/medium/large-v3")
    parser.add_argument("--subtitle-style", choices=["single", "dual"], default=None)
    parser.add_argument("--subtitle-only", action="store_true")
    parser.add_argument("--subtitle-font", default=None)
    parser.add_argument("--keep-original-audio", action="store_true")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")


def apply_overrides(config: Config, args) -> Config:
    if args.output_dir: config.output_dir = args.output_dir
    if args.asr_engine: config.asr_engine = args.asr_engine
    if args.translate_engine: config.translate_engine = args.translate_engine
    if args.tts_engine: config.tts_engine = args.tts_engine
    if args.translate_model: config.translate_model = args.translate_model
    if args.whisper_model: config.whisper_model = args.whisper_model
    if args.faster_whisper_model: config.faster_whisper_model = args.faster_whisper_model
    if args.subtitle_style: config.subtitle_style = args.subtitle_style
    if args.subtitle_font: config.subtitle_font = args.subtitle_font
    if args.keep_original_audio: config.keep_original_audio = True
    # 应用全局代理（命令行模式）
    _apply_global_proxy(config)
    return config


def cmd_video(args):
    from pipeline import TranslationPipeline
    config = load_config()
    config = apply_overrides(config, args)
    pipeline = TranslationPipeline(config)
    pipeline.run(source=args.input, target_lang=args.target_lang,
                 source_lang=args.source_lang, subtitle_only=args.subtitle_only,
                 keep_original_audio=args.keep_original_audio,
                 skip_download=args.skip_download, skip_asr=args.skip_asr,
                 skip_translate=args.skip_translate)


def cmd_douyin(args):
    from pipeline import TranslationPipeline
    config = load_config()
    config = apply_overrides(config, args)
    pipeline = TranslationPipeline(config)
    pipeline.run(source=args.share_text, target_lang=args.target_lang,
                 source_lang=args.source_lang, subtitle_only=args.subtitle_only,
                 keep_original_audio=args.keep_original_audio,
                 skip_download=args.skip_download, skip_asr=args.skip_asr,
                 skip_translate=args.skip_translate)


def cmd_youtube(args):
    """命令行：处理 YouTube / 流媒体视频"""
    from pipeline import TranslationPipeline
    config = load_config()
    config = apply_overrides(config, args)
    pipeline = TranslationPipeline(config)
    pipeline.run(source=args.url, target_lang=args.target_lang,
                 source_lang=args.source_lang, subtitle_only=args.subtitle_only,
                 keep_original_audio=args.keep_original_audio,
                 skip_download=args.skip_download, skip_asr=args.skip_asr,
                 skip_translate=args.skip_translate)


def cmd_config(args):
    if args.init:
        path = Path(".env")
        if path.exists(): path.unlink()
        create_config_template(".")
        return
    config = load_config()
    print(f"\n配置文件: {config.config_file_path}")
    print(f"ffmpeg: {config.ffmpeg_path}")
    print(f"ASR: {config.asr_engine}")
    print(f"翻译: {config.translate_engine} / {config.translate_model}")
    print(f"TTS: {config.tts_engine}")
    print(f"字幕: {config.subtitle_style} / {config.subtitle_fontsize}px / 换行 {config.subtitle_max_width_percent:.0%}")
    print(f"OpenAI: {'已设置' if config.has_openai() else '未设置'}")


def main():
    # 无参数 → 进入交互模式
    if len(sys.argv) <= 1:
        interactive_main()
        return

    parser = argparse.ArgumentParser(description="TransVideo 视频翻译配音工具")
    subparsers = parser.add_subparsers(dest="command")

    p_video = subparsers.add_parser("video", help="翻译本地视频")
    p_video.add_argument("input", help="视频文件路径")
    add_common_args(p_video)
    p_video.set_defaults(func=cmd_video)

    p_douyin = subparsers.add_parser("douyin", help="翻译抖音 / TikTok 视频")
    p_douyin.add_argument("share_text", help="抖音 / TikTok 分享文本")
    add_common_args(p_douyin)
    p_douyin.set_defaults(func=cmd_douyin)

    # YouTube / 流媒体 子命令
    p_youtube = subparsers.add_parser("youtube", help="翻译 YouTube / 流媒体视频")
    p_youtube.add_argument("url", help="视频 URL (YouTube/B站/Vimeo 等)")
    add_common_args(p_youtube)
    p_youtube.set_defaults(func=cmd_youtube)

    p_config = subparsers.add_parser("config", help="查看配置")
    p_config.add_argument("--init", action="store_true")
    p_config.set_defaults(func=cmd_config)

    # 交互模式入口
    p_interactive = subparsers.add_parser("interactive", help="进入交互模式")
    p_interactive.set_defaults(func=lambda args: interactive_main())

    args = parser.parse_args()

    if not args.command:
        interactive_main()
        return

    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n[!] 用户中断")
        sys.exit(130)
    except Exception as e:
        print(f"\n[!] 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
