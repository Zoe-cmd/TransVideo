# -*- coding: utf-8 -*-
"""IndexTTS 2 环境状态检查、路径规划与自动安装（供 CLI 配置界面和安装脚本共用）

为什么需要 venv_dir()：
  index-tts 依赖的 kaldifst（C++ 扩展）用 fopen 打开 wetext 的 .fst 文件，
  路径含中文等非 ASCII 字符时在 Windows GBK 系统上会直接失败。
  因此当项目路径包含非 ASCII 字符时，把 index-tts 的虚拟环境放到
  纯 ASCII 的外部目录（%LOCALAPPDATA%\\TransVideo\\indextts-venv），
  模型权重仍在项目内 index-tts/checkpoints（torch 加载走 Python 宽字符 API，不受影响）。
"""

import os
import subprocess
import sys

# 项目根目录（本文件在 modules/ 下）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.join(PROJECT_ROOT, "index-tts")
SETUP_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "setup_indextts.py")

# 判定权重已下载完整所需的关键文件
REQUIRED_CKPT_FILES = ["config.yaml", "gpt.pth", "s2mel.pth"]


def venv_dir() -> str:
    """index-tts 虚拟环境目录

    项目路径为纯 ASCII 时用项目内的 index-tts/.venv；
    否则用外部纯 ASCII 目录（规避 kaldifst 等 C++ 扩展的中文路径问题）。
    """
    if PROJECT_ROOT.encode("ascii", "ignore").decode("ascii") == PROJECT_ROOT:
        return os.path.join(REPO_DIR, ".venv")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "TransVideo", "indextts-venv")
    return os.path.join(os.path.expanduser("~"), ".cache", "transvideo", "indextts-venv")


def index_python() -> str:
    """index-tts 虚拟环境中的 Python 解释器路径"""
    venv = venv_dir()
    if os.name == "nt":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python")


def checkpoints_dir() -> str:
    return os.path.join(REPO_DIR, "checkpoints")


def ascii_alias(path: str) -> str:
    """返回 path 的纯 ASCII 访问路径

    sentencepiece / kaldifst 等 C++ 扩展用 fopen 打开文件，
    中文路径在 Windows GBK 系统上必然失败。对非 ASCII 路径，
    在外部 ASCII 目录下创建 junction（目录联接）并返回联接路径；
    文件本体不动， junction 创建不需要管理员权限。
    """
    if path.encode("ascii", "ignore").decode("ascii") == path:
        return path
    if os.name != "nt":
        return path  # Linux/macOS 的 fopen 支持 UTF-8，无此问题

    import hashlib
    base = os.path.join(os.path.dirname(venv_dir()), "links")
    os.makedirs(base, exist_ok=True)
    name = hashlib.md5(os.path.abspath(path).encode("utf-8")).hexdigest()[:10]
    link = os.path.join(base, name)
    if os.path.isdir(link):
        return link
    if os.path.lexists(link):
        os.remove(link)
    result = subprocess.run(["cmd", "/c", "mklink", "/J", link, os.path.abspath(path)],
                            capture_output=True)
    if result.returncode != 0 or not os.path.isdir(link):
        raise RuntimeError(
            f"无法为中文路径创建 ASCII junction: {path}\n"
            f"mklink 输出: {result.stderr.decode('gbk', 'replace')[:200]}")
    return link


def status() -> dict:
    """检查各环节状态，返回 {step: (ok, detail)}"""
    result = {}
    result["repo"] = (os.path.isdir(REPO_DIR), REPO_DIR)
    py = index_python()
    venv_ok = False
    venv_detail = py
    if os.path.isfile(py):
        try:
            probe = subprocess.run(
                [py, "-c", "import torch, indextts"],
                capture_output=True, timeout=60)
            venv_ok = probe.returncode == 0
        except Exception:
            venv_ok = False
        venv_detail = "依赖完整" if venv_ok else "Python 存在但依赖未装完"
    result["venv"] = (venv_ok, venv_detail)
    ckpt = checkpoints_dir()
    missing = [f for f in REQUIRED_CKPT_FILES
               if not os.path.isfile(os.path.join(ckpt, f))]
    result["checkpoints"] = (not missing,
                             "完整" if not missing else f"缺少: {', '.join(missing)}")
    return result


def indextts_status() -> tuple:
    """检查 IndexTTS 环境是否就绪

    返回 (ready, detail)：ready 为 True 表示可直接使用。
    """
    try:
        st = status()
        if all(ok for ok, _ in st.values()):
            return True, "环境已就绪"
        missing = [name for name, (ok, _) in st.items() if not ok]
        label = {"repo": "index-tts 仓库", "venv": "依赖环境 (.venv)",
                 "checkpoints": "模型权重"}
        return False, "未就绪: " + ", ".join(label.get(m, m) for m in missing)
    except Exception as e:
        return False, f"状态检查失败: {e}"


def install_indextts() -> bool:
    """运行自动安装脚本（输出实时可见），返回是否成功"""
    cmd = [sys.executable, SETUP_SCRIPT]
    print(f"\n[indextts] 开始自动安装: {' '.join(cmd)}")
    print("[indextts] 需要下载约 8GB 数据（依赖 + 模型权重），请保持网络畅通...")
    proc = subprocess.run(cmd, cwd=PROJECT_ROOT)
    return proc.returncode == 0
