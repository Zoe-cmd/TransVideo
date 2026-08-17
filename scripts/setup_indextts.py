# -*- coding: utf-8 -*-
"""IndexTTS 2 环境自动安装脚本

用法：
  python scripts/setup_indextts.py              # 完整安装（已装的部分自动跳过）
  python scripts/setup_indextts.py --check      # 只检查环境状态，不安装
  python scripts/setup_indextts.py --proxy http://127.0.0.1:7897

安装步骤（幂等，可重复运行）：
  1. 检查 git / uv（uv 缺失时自动 pip install）
  2. 克隆 index-tts 仓库到 index-tts/
  3. uv sync 安装依赖（Python 3.11 + CUDA torch）
  4. 下载 IndexTTS-2 模型权重到 index-tts/checkpoints（约 5GB）
  5. 验证 GPU / torch 可用性

下载源回退策略：
  - 配置了代理（.env 的 NETWORK_PROXY 或 --proxy）：先用「代理 + 官方源」
  - 失败或没配代理：直连官方源
  - 再失败：切换国内镜像（阿里云 PyPI / pytorch-wheels、ModelScope、hf-mirror）
  - 全部失败：提示配置代理后重试

中文路径说明：
  项目路径含非 ASCII 字符时，虚拟环境会建到外部纯 ASCII 目录
  （见 modules/indextts_setup.venv_dir），规避 kaldifst 的中文路径崩溃。
"""

import argparse
import os
import shutil
import subprocess
import sys

# 项目根目录（本文件在 scripts/ 下）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from modules.indextts_setup import (  # noqa: E402
    REPO_DIR, checkpoints_dir, index_python, status, venv_dir,
)

REPO_URL = "https://github.com/index-tts/index-tts.git"
MODEL_ID = "IndexTeam/IndexTTS-2"

# 国内镜像
ALIYUN_PYPI = "https://mirrors.aliyun.com/pypi/simple"
ALIYUN_PYTORCH_CU128 = "https://mirrors.aliyun.com/pytorch-wheels/cu128"
OFFICIAL_PYTORCH_CU128 = "https://download.pytorch.org/whl/cu128"
HF_MIRROR = "https://hf-mirror.com"


def log(msg: str):
    print(f"[setup] {msg}", flush=True)


def run(cmd: list, cwd: str = None, env: dict = None) -> bool:
    """运行命令（输出实时可见），返回是否成功"""
    log(f"执行: {' '.join(str(c) for c in cmd)}")
    proc = subprocess.run(cmd, cwd=cwd, env=env)
    return proc.returncode == 0


def load_proxy(cli_proxy: str = None) -> str:
    """代理优先级: --proxy 参数 > .env 的 NETWORK_PROXY"""
    if cli_proxy:
        return cli_proxy
    try:
        from config import load_config
        return load_config(PROJECT_ROOT).network_proxy or ""
    except Exception:
        return ""


def build_env(proxy: str = "") -> dict:
    """构造子进程环境；给了代理就注入 HTTP(S)_PROXY

    同时注入 UV_PROJECT_ENVIRONMENT：项目路径含中文等非 ASCII 字符时，
    把虚拟环境建到外部纯 ASCII 目录（kaldifst 打开 .fst 文件用 fopen，
    中文路径在 Windows GBK 系统上必然失败）。
    """
    env = os.environ.copy()
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
    env["UV_PROJECT_ENVIRONMENT"] = venv_dir()
    return env


def ensure_uv(env: dict) -> str:
    """确保 uv 可用，返回 uv 可执行文件路径"""
    uv = shutil.which("uv")
    if uv:
        return uv
    log("未找到 uv，正在安装（pip install -U uv）...")
    if not run([sys.executable, "-m", "pip", "install", "-U", "uv"], env=env):
        # pip 也失败时试国内镜像
        run([sys.executable, "-m", "pip", "install", "-U", "uv", "-i", ALIYUN_PYPI], env=env)
    uv = shutil.which("uv")
    if not uv:
        # Windows 下 Scripts 目录可能不在 PATH
        candidate = os.path.join(os.path.dirname(sys.executable), "uv.exe")
        if os.path.isfile(candidate):
            uv = candidate
    if not uv:
        raise RuntimeError("uv 安装失败，请手动执行: pip install -U uv")
    return uv


def step_clone(proxy: str):
    if os.path.isdir(REPO_DIR):
        log("index-tts 仓库已存在，跳过克隆")
        return
    if not shutil.which("git"):
        raise RuntimeError("未找到 git，请先安装 Git: https://git-scm.com")
    log("克隆 index-tts 仓库...")
    # 链：代理(如已配置) → 直连
    attempts = []
    if proxy:
        attempts.append(("代理", build_env(proxy)))
    attempts.append(("直连", build_env()))
    for label, e in attempts:
        log(f"尝试{label}克隆...")
        if run(["git", "clone", REPO_URL, REPO_DIR], env=e):
            return
    raise RuntimeError("git clone 失败，请检查网络或在 .env 中配置 NETWORK_PROXY 后重试")


def _patch_pytorch_index(mirror_url: str):
    """把 index-tts/pyproject.toml 中的 pytorch 索引替换为镜像，返回原始内容"""
    pyproject = os.path.join(REPO_DIR, "pyproject.toml")
    with open(pyproject, "r", encoding="utf-8") as f:
        content = f.read()
    patched = content.replace(OFFICIAL_PYTORCH_CU128, mirror_url)
    if patched == content:
        return None  # 没找到可替换的 URL
    with open(pyproject, "w", encoding="utf-8") as f:
        f.write(patched)
    return content


def step_sync(uv: str, proxy: str):
    py = index_python()
    # 已装好则跳过
    if os.path.isfile(py):
        probe = subprocess.run(
            [py, "-c", "import torch, indextts; print(torch.__version__)"],
            capture_output=True, text=True)
        if probe.returncode == 0:
            log(f"依赖已安装（torch {probe.stdout.strip()}），跳过 uv sync")
            return

    # 清理旧的 editable 安装指针：.pth 里写的是项目绝对路径，
    # 路径含中文时 Python(GBK locale) 读取 .pth 会直接崩溃，
    # 所以统一用 --no-editable 安装（包实体进 site-packages，不引用项目路径）
    for stale in (os.path.join(venv_dir(), "Lib", "site-packages", "_editable_impl_indextts.pth"),
                  os.path.join(REPO_DIR, ".venv", "Lib", "site-packages", "_editable_impl_indextts.pth")):
        if os.path.isfile(stale):
            try:
                os.remove(stale)
            except OSError:
                pass

    log(f"安装 index-tts 依赖到 {venv_dir()}（torch 约 3.2GB，进度实时显示）...")
    attempts = []
    if proxy:
        attempts.append(("代理 + 官方源", build_env(proxy)))
    attempts.append(("直连官方源", build_env()))

    for label, e in attempts:
        log(f"--- 尝试: {label} ---")
        if run([uv, "sync", "--no-editable"], cwd=REPO_DIR, env=e):
            return
        log(f"「{label}」失败，切换下一个下载源...")

    # 国内镜像：替换 pytorch 索引为阿里云，PyPI 也走阿里云
    log("--- 尝试: 国内镜像（阿里云 PyPI + pytorch-wheels）---")
    original = _patch_pytorch_index(ALIYUN_PYTORCH_CU128)
    try:
        if run([uv, "sync", "--no-editable", "--default-index", ALIYUN_PYPI],
               cwd=REPO_DIR, env=build_env()):
            return
    finally:
        if original is not None:
            with open(os.path.join(REPO_DIR, "pyproject.toml"), "w", encoding="utf-8") as f:
                f.write(original)

    raise RuntimeError(
        "依赖安装失败（官方源直连/国内镜像均不可用）。\n"
        "建议在 .env 中配置 NETWORK_PROXY（如 http://127.0.0.1:7897）后重试:\n"
        "  python scripts/setup_indextts.py")


def step_checkpoints(uv: str, proxy: str):
    ok, detail = status()["checkpoints"]
    if ok:
        log("模型权重已完整，跳过下载")
        return
    ckpt = checkpoints_dir()
    os.makedirs(ckpt, exist_ok=True)
    log(f"下载 IndexTTS-2 模型权重（约 5.5GB）: {detail}")

    attempts = []
    # 配置了代理：优先 HuggingFace 官方 + 代理
    if proxy:
        attempts.append(("HuggingFace (代理)",
                         [uv, "tool", "run", "--from", "huggingface-hub[cli,hf_xet]",
                          "hf", "download", MODEL_ID, "--local-dir", "checkpoints"],
                         build_env(proxy)))
    # 国内镜像：ModelScope（indextts 依赖自带 modelscope）
    attempts.append(("ModelScope (国内直连)",
                     [uv, "run", "modelscope", "download", "--model", MODEL_ID,
                      "--local_dir", "checkpoints"],
                     build_env()))
    # hf-mirror 兜底
    env_mirror = build_env()
    env_mirror["HF_ENDPOINT"] = HF_MIRROR
    attempts.append(("hf-mirror 镜像",
                     [uv, "tool", "run", "--from", "huggingface-hub[cli,hf_xet]",
                      "hf", "download", MODEL_ID, "--local-dir", "checkpoints"],
                     env_mirror))

    for label, cmd, e in attempts:
        log(f"--- 尝试: {label} ---")
        if run(cmd, cwd=REPO_DIR, env=e) and status()["checkpoints"][0]:
            return
        log(f"「{label}」失败或不完整，切换下一个下载源...")

    raise RuntimeError(
        "模型权重下载失败（所有源均不可用）。\n"
        "建议在 .env 中配置 NETWORK_PROXY（如 http://127.0.0.1:7897）后重试:\n"
        "  python scripts/setup_indextts.py\n"
        "或手动执行:\n"
        "  cd index-tts && uv run modelscope download --model IndexTeam/IndexTTS-2 --local_dir checkpoints")


def step_verify():
    py = index_python()
    log("验证环境...")
    probe = subprocess.run(
        [py, "-c",
         "import torch; print(f'torch={torch.__version__} cuda={torch.cuda.is_available()}')"],
        capture_output=True, text=True)
    log(probe.stdout.strip() or probe.stderr.strip()[-200:])
    if probe.returncode != 0:
        raise RuntimeError("index-tts 环境验证失败，请查看上方日志")


# flash-attn Windows 预编译 wheel 来源（kingbri1/flash-attention，社区维护）
FLASH_ATTN_REPO_RELEASE = "https://github.com/kingbri1/flash-attention/releases/download/v2.8.3"


def _probe(py: str, code: str) -> str:
    """在 index-tts 环境里执行代码，失败返回空串"""
    p = subprocess.run([py, "-c", code], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def step_accel(uv: str, proxy: str):
    """按 .env 的加速开关安装可选加速依赖（triton-windows / flash-attn）

    全部失败也不报错——worker 初始化加速失败会自动降级到基础模式。
    """
    try:
        from config import load_config
        cfg = load_config(PROJECT_ROOT)
    except Exception:
        return
    want_tc = getattr(cfg, "index_tts_use_torch_compile", False)
    want_accel = getattr(cfg, "index_tts_use_accel", False)
    if not (want_tc or want_accel):
        return

    py = index_python()
    env = build_env(proxy)

    if want_tc:
        if _probe(py, "import triton; print(triton.__version__)"):
            log("triton 已安装，跳过")
        else:
            log("安装 triton-windows（torch.compile 加速所需）...")
            ok = run([uv, "pip", "install", "--python", py,
                      "triton-windows==3.1.0.post17"], env=env)
            if not ok:
                run([uv, "pip", "install", "--python", py,
                     "triton-windows==3.1.0.post17", "--default-index", ALIYUN_PYPI],
                    env=build_env())

    if want_accel:
        if _probe(py, "import flash_attn; print(flash_attn.__version__)"):
            log("flash-attn 已安装，跳过")
        else:
            # 探测 torch / CUDA / Python 版本，拼出匹配的预编译 wheel 文件名
            info = _probe(py, "import torch,sys;print(torch.__version__,torch.version.cuda,"
                              "f'cp{sys.version_info.major}{sys.version_info.minor}')")
            if not info:
                log("✗ 无法探测 torch 版本，跳过 flash-attn 安装")
                return
            torch_ver, cuda_ver, py_tag = info.split()
            cu = "cu" + cuda_ver.replace(".", "")
            url = (f"{FLASH_ATTN_REPO_RELEASE}/flash_attn-2.8.3+{cu}torch{torch_ver}"
                   f"cxx11abiFALSE-{py_tag}-{py_tag}-win_amd64.whl")
            log(f"安装 flash-attn 2.8.3 预编译 wheel（匹配 torch {torch_ver} / {cu} / {py_tag}）...")
            log(f"来源: {url}")
            if not run([uv, "pip", "install", "--python", py, url], env=env):
                log("✗ 没有匹配的预编译 wheel 或下载失败。")
                log("  可到 https://github.com/kingbri1/flash-attention/releases 手动找匹配版本；")
                log("  装不上也不影响使用——配音时会自动降级到基础模式。")


def main():
    parser = argparse.ArgumentParser(description="IndexTTS 2 环境自动安装")
    parser.add_argument("--check", action="store_true", help="只检查状态，不安装")
    parser.add_argument("--proxy", default=None, help="下载代理，如 http://127.0.0.1:7897")
    args = parser.parse_args()

    if args.check:
        st = status()
        all_ok = all(ok for ok, _ in st.values())
        for name, (ok, detail) in st.items():
            print(f"  [{'OK' if ok else 'X'}] {name}: {detail}")
        sys.exit(0 if all_ok else 1)

    proxy = load_proxy(args.proxy)
    log(f"项目目录: {PROJECT_ROOT}")
    log(f"虚拟环境: {venv_dir()}")
    log(f"代理: {proxy or '(未配置)'}")

    uv = ensure_uv(build_env(proxy))
    log(f"uv: {uv}")

    step_clone(proxy)
    step_sync(uv, proxy)
    step_checkpoints(uv, proxy)
    step_verify()
    step_accel(uv, proxy)

    log("✓ IndexTTS 2 环境安装完成！")
    log("在 .env 中设置 TTS_ENGINE=index 即可使用（或在 CLI 配置界面切换）")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        log(f"✗ {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        log("用户中断")
        sys.exit(130)
