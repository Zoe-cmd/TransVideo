# -*- coding: utf-8 -*-
"""IndexTTS 2.5 环境状态检查、路径规划与自动安装（供 CLI 配置界面和安装脚本共用）

为什么需要 venv_dir()：
  index-tts 依赖的 kaldifst（C++ 扩展）用 fopen 打开 wetext 的 .fst 文件，
  路径含中文等非 ASCII 字符时在 Windows GBK 系统上会直接失败。
  因此当仓库路径包含非 ASCII 字符时，把 index-tts 的虚拟环境放到
  纯 ASCII 的外部目录（%LOCALAPPDATA%\\TransVideo\\indextts-venv），
  模型权重仍在仓库内 checkpoints_25（torch 加载走 Python 宽字符 API，不受影响）。

共享目录：
  在 .env 设置 INDEX_TTS_REPO_DIR 可把 index-tts 仓库（代码 + 权重 + 环境）
  放到项目外的公共目录，供多个项目共享，例如：
    INDEX_TTS_REPO_DIR=C:\\Users\\12439\\agent project\\public\\index-tts
"""

import os
import subprocess
import sys

# 项目根目录（本文件在 modules/ 下）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP_SCRIPT = os.path.join(PROJECT_ROOT, "scripts", "setup_indextts.py")


def _read_env_value(key: str) -> str:
    """从环境变量或项目 .env 读取配置值（不依赖 config 模块，避免循环引用）"""
    if os.environ.get(key):
        return os.environ[key]
    env_file = os.path.join(PROJECT_ROOT, ".env")
    if os.path.isfile(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(key + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError:
            pass
    return ""


def repo_dir() -> str:
    """index-tts 仓库目录

    默认在项目内 index-tts/；可在 .env 设置 INDEX_TTS_REPO_DIR 指向外部目录
    （如 C:\\Users\\xxx\\public\\index-tts），供多个项目共享同一份代码与模型权重。
    相对路径按项目根目录解析。
    """
    custom = _read_env_value("INDEX_TTS_REPO_DIR")
    if custom:
        return custom if os.path.isabs(custom) else os.path.join(PROJECT_ROOT, custom)
    return os.path.join(PROJECT_ROOT, "index-tts")


# 兼容旧引用（模块级常量，值为默认路径；动态逻辑请用 repo_dir()）
REPO_DIR = os.path.join(PROJECT_ROOT, "index-tts")

# 判定权重已下载完整所需的关键文件
REQUIRED_CKPT_FILES = ["config.yaml", "gpt.pth", "s2mel.pth"]

# IndexTTS 2.5 权重目录与关键文件（与 webui.py 的 REQUIRED_FILES["2.5"] 保持一致，
# 2.5 的 config.yaml 与 2.0 内容不同，因此独立目录存放，互不覆盖）
CKPT25_DIR_NAME = "checkpoints_25"
REQUIRED_CKPT25_FILES = ["config.yaml", "gpt.pth", "s2mel.pth", "codec.pth",
                         "multilingual_zh_ja_yue_char_del.tiktoken", "wav2vec2bert_stats.pt"]


def venv_dir() -> str:
    """index-tts 虚拟环境目录

    仓库路径为纯 ASCII 时用仓库内的 .venv（如共享目录 public/index-tts/.venv，
    多项目天然共享）；否则用外部纯 ASCII 目录（规避 kaldifst 等 C++ 扩展的中文路径问题）。
    """
    repo = repo_dir()
    if repo.encode("ascii", "ignore").decode("ascii") == repo:
        return os.path.join(repo, ".venv")
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
    return os.path.join(repo_dir(), "checkpoints")


def checkpoints_25_dir() -> str:
    return os.path.join(repo_dir(), CKPT25_DIR_NAME)


def _missing_files(directory: str, required: list) -> list:
    return [f for f in required if not os.path.isfile(os.path.join(directory, f))]


def detect_version() -> str:
    """检测本地可用的模型版本：'2.5' 优先，其次 '2'，都没有返回 ''"""
    if not _missing_files(checkpoints_25_dir(), REQUIRED_CKPT25_FILES):
        return "2.5"
    if not _missing_files(checkpoints_dir(), REQUIRED_CKPT_FILES):
        return "2"
    return ""


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
    # 仓库需包含 2.5 推理代码（infer_v2_5.py），否则提示升级
    repo = repo_dir()
    repo_ok = os.path.isdir(repo)
    if repo_ok and not os.path.isfile(os.path.join(repo, "indextts", "infer_v2_5.py")):
        result["repo"] = (False, f"{repo}（代码过旧，需升级 v2.5.0）")
    else:
        result["repo"] = (repo_ok, repo)
    py = index_python()
    venv_ok = False
    venv_detail = py
    if os.path.isfile(py):
        try:
            probe = subprocess.run(
                [py, "-c", "import torch, indextts; from indextts import infer_v2_5"],
                capture_output=True, timeout=60)
            venv_ok = probe.returncode == 0
        except Exception:
            venv_ok = False
        venv_detail = "依赖完整" if venv_ok else "Python 存在但依赖未装完（或缺少 2.5 代码，需重新 sync）"
    result["venv"] = (venv_ok, venv_detail)
    # 权重：2.5 优先；只有 2.0 时提示可升级
    missing25 = _missing_files(checkpoints_25_dir(), REQUIRED_CKPT25_FILES)
    if not missing25:
        result["checkpoints"] = (True, "完整（IndexTTS-2.5）")
    elif not _missing_files(checkpoints_dir(), REQUIRED_CKPT_FILES):
        result["checkpoints"] = (False, "仅有 2.0 权重，运行安装脚本升级 2.5")
    else:
        result["checkpoints"] = (False, f"缺少: {', '.join(missing25)}")
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
