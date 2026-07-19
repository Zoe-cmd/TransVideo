# -*- coding: utf-8 -*-
"""依赖检查模块 —— 在 CLI 启动时验证所有必需的库已安装

作用：避免运行到一半才因 ImportError 失败，提前给出明确指引。
"""

import sys
import importlib
from typing import List, Tuple


# 必需依赖（核心功能）
REQUIRED_DEPS = [
    ("requests", "requests", "网络请求"),
    ("yaml", "pyyaml", "YAML 配置文件"),
]

# 可选依赖（按引擎分组）
OPTIONAL_DEPS = {
    "asr": [
        ("openai", "openai", "Whisper API（云端 ASR）"),
        ("faster_whisper", "faster-whisper", "本地 ASR（免费方案）"),
    ],
    "translate": [
        ("openai", "openai", "GPT 翻译"),
    ],
    "tts": [
        ("edge_tts", "edge-tts", "edge-tts 配音（免费）"),
    ],
    "youtube": [
        ("yt_dlp", "yt-dlp", "YouTube 视频解析"),
    ],
}


def check_import(module_name: str) -> bool:
    """检查模块是否能导入"""
    try:
        importlib.import_module(module_name)
        return True
    except ImportError:
        return False


def get_install_command(packages: List[str]) -> str:
    """生成安装命令"""
    return f'"{sys.executable}" -m pip install {" ".join(packages)}'


def check_all(config=None) -> Tuple[bool, list, list]:
    """检查所有依赖

    Returns:
        (all_ok, missing_required, missing_optional)
        - all_ok: 必需依赖是否齐全
        - missing_required: 缺失的必需依赖列表 [(module, pip_name, desc)]
        - missing_optional: 缺失的可选依赖列表 [(category, module, pip_name, desc)]
    """
    missing_required = []
    for module, pip_name, desc in REQUIRED_DEPS:
        if not check_import(module):
            missing_required.append((module, pip_name, desc))

    missing_optional = []
    for category, deps in OPTIONAL_DEPS.items():
        for module, pip_name, desc in deps:
            if not check_import(module):
                missing_optional.append((category, module, pip_name, desc))

    # Ollama 特殊：检查服务是否运行（不是库依赖）
    if config and config.translate_engine == "ollama":
        try:
            from modules.ollama_translator import is_ollama_running
            if not is_ollama_running(config.ollama_url):
                missing_optional.append(("ollama", "_service_", "ollama", "Ollama 服务未运行"))
        except Exception:
            pass

    all_ok = len(missing_required) == 0
    return all_ok, missing_required, missing_optional


def format_report(missing_required: list, missing_optional: list) -> str:
    """格式化缺失依赖报告"""
    lines = []

    if missing_required:
        lines.append("❌ 缺失必需依赖：")
        for module, pip_name, desc in missing_required:
            lines.append(f"   - {pip_name} ({desc})")
        packages = [p for _, p, _ in missing_required]
        lines.append(f"\n   请运行：{get_install_command(packages)}")

    if missing_optional:
        lines.append("\n⚠️  缺失可选依赖（影响部分功能）：")
        by_cat = {}
        for cat, module, pip_name, desc in missing_optional:
            by_cat.setdefault(cat, []).append((module, pip_name, desc))
        for cat, items in by_cat.items():
            cat_name = {"asr": "ASR 语音识别", "translate": "翻译",
                        "tts": "TTS 配音", "youtube": "YouTube 解析",
                        "ollama": "Ollama"}.get(cat, cat)
            lines.append(f"   [{cat_name}]")
            for module, pip_name, desc in items:
                lines.append(f"     - {pip_name} ({desc})")
            packages = [p for _, p, _ in items]
            lines.append(f"     安装：{get_install_command(packages)}")

    return "\n".join(lines)


def ensure_required_or_exit():
    """启动时检查，必需依赖缺失则退出"""
    all_ok, missing_required, missing_optional = check_all()
    if not all_ok:
        print("\n" + "=" * 60)
        print("  依赖检查失败")
        print("=" * 60)
        print(format_report(missing_required, missing_optional))
        print("\n" + "=" * 60 + "\n")
        sys.exit(1)
    return missing_optional
