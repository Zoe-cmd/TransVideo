# -*- coding: utf-8 -*-
"""人声分离 —— 把音频拆成人声轨 + 伴奏轨

用途：翻译配音时保留原视频背景音（BGM、环境声），只替换人声。
典型场景：外语 ASMR、带背景音乐的教学视频。

技术方案：audio-separator（UVR MDX-NET / RoFormer ONNX 模型）
  - 检测到 NVIDIA GPU 时自动安装 onnxruntime-gpu 启用 CUDA 加速（CPU 约慢 5-10 倍）
  - 无 GPU 时回退纯 CPU 推理
  - 首次使用自动 pip 安装（代理 → 直连 → 阿里云镜像回退）
  - 首次运行自动下载模型（约 50-200MB，下载进度实时可见）

注意：audio-separator 0.44.x 与 librosa>=1.0 不兼容（get_duration 参数改名），
所以自动安装时固定 librosa==0.11.0。
"""

import logging
import os
import subprocess
import sys

# 自动安装时要装的包（librosa 版本钉死，见模块 docstring）
_REQUIRED_PACKAGES = ["audio-separator==0.44.5", "audioread", "librosa==0.11.0"]
# GPU 加速：onnxruntime-gpu + CUDA 运行库（pip 版，免装 CUDA Toolkit）
# 版本说明（实测）：onnxruntime-gpu 钉 1.28（1.29 起依赖 CUDA 13 且生态包名混乱）；
# 1.28 是混合构建——cudart/cudnn 用 CUDA 12 系（cudart64_12 / cudnn64_9），
# 而 cublas/cufft 用 CUDA 13 系（cublasLt64_13 / cufft64_12，来自无后缀新包，
# 注意 *-cu13 后缀包是官方废弃占位，勿用）
_GPU_PACKAGES = [
    "onnxruntime-gpu==1.28.0",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cublas",
    "nvidia-cufft",
    "nvidia-cuda-runtime",
]
_ALIYUN_PYPI = "https://mirrors.aliyun.com/pypi/simple"


def _log(msg: str):
    print(f"[separate] {msg}", flush=True)


def _pip_install(packages, proxy: str = "", extra_args=None) -> bool:
    """安装指定 pip 包，返回是否成功

    回退链：代理+官方源 → 直连官方源 → 阿里云镜像。pip 输出实时可见。
    """
    attempts = []
    if proxy:
        attempts.append(("代理 + 官方源", proxy))
    attempts.append(("直连官方源", ""))
    attempts.append(("阿里云镜像", "mirror"))

    for label, mode in attempts:
        _log(f"安装 {', '.join(packages)}（{label}）...")
        env = os.environ.copy()
        cmd = [sys.executable, "-m", "pip", "install"] + (extra_args or []) + list(packages)
        if mode == "mirror":
            cmd += ["-i", _ALIYUN_PYPI]
        elif mode:
            env["HTTP_PROXY"] = mode
            env["HTTPS_PROXY"] = mode
        proc = subprocess.run(cmd, env=env)
        if proc.returncode == 0:
            return True
        _log(f"「{label}」失败，切换下一个源...")
    return False


def _has_nvidia_gpu() -> bool:
    """是否有 NVIDIA GPU（nvidia-smi 可用即认为有）"""
    try:
        proc = subprocess.run(["nvidia-smi"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=10)
        return proc.returncode == 0
    except Exception:
        return False


def _dist_installed(name: str) -> bool:
    """pip 发行版是否已安装（不导入模块，避免锁定已加载的同名模块）"""
    try:
        from importlib.metadata import version
        version(name)
        return True
    except Exception:
        return False


def _register_nvidia_dll_dirs():
    """把 pip 安装的 nvidia 运行库目录加入 DLL 搜索路径（Windows 必需）

    各包布局不一：多为 nvidia/<lib>/bin，CUDA 13 系则是 nvidia/cu13/bin/x86_64，
    所以直接递归找所有含 DLL 的目录。
    """
    if os.name != "nt":
        return
    try:
        import site
        candidates = list(site.getsitepackages()) + [site.getusersitepackages()]
    except Exception:
        return
    for sp in candidates:
        nvidia_dir = os.path.join(sp, "nvidia")
        if not os.path.isdir(nvidia_dir):
            continue
        for root, _dirs, files in os.walk(nvidia_dir):
            if any(f.lower().endswith(".dll") for f in files):
                try:
                    os.add_dll_directory(root)
                except OSError:
                    pass
                # onnxruntime 用 LOAD_WITH_ALTERED_SEARCH_PATH 加载 provider，
                # 该模式不认 add_dll_directory，但认 PATH —— 必须同时加进 PATH
                path = os.environ.get("PATH", "")
                if root.lower() not in path.lower():
                    os.environ["PATH"] = root + os.pathsep + path


def _cuda_provider_usable() -> bool:
    """实际验证 CUDAExecutionProvider 可用（注册 DLL 路径后再查）"""
    _register_nvidia_dll_dirs()
    try:
        import onnxruntime as ort
        return "CUDAExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def _ort_gpu_pinned_installed() -> bool:
    """钉死版本的 onnxruntime-gpu 是否已安装（版本不符也需重装）"""
    try:
        from importlib.metadata import version
        return version("onnxruntime-gpu") == _GPU_PACKAGES[0].split("==")[1]
    except Exception:
        return False


def _ensure_gpu_acceleration(proxy: str = ""):
    """有 NVIDIA GPU 但 GPU 推理栈不齐时，自动安装/换装

    注意 onnxruntime-gpu 与 CPU 版 onnxruntime 模块名相同会冲突，换装前要先卸载；
    且换装要在 audio-separator/onnxruntime 被导入前完成，本次运行即可用 GPU。
    """
    if not _has_nvidia_gpu():
        return
    if (_ort_gpu_pinned_installed() and _dist_installed("nvidia-cudnn-cu12")
            and _dist_installed("nvidia-cublas")):
        return
    _log("检测到 NVIDIA GPU，自动启用 GPU 加速（安装 onnxruntime-gpu + CUDA 运行库）...")
    # 换装失败不影响功能（CPU 兜底），所以任何一步失败都只是告警
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "onnxruntime"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _pip_install(_GPU_PACKAGES, proxy):
        _log("✓ GPU 加速安装完成")
    else:
        _log("⚠ onnxruntime-gpu 安装失败，本次将使用 CPU 推理（速度较慢）")
        _log("  可稍后手动执行: pip uninstall -y onnxruntime && "
             f"pip install {' '.join(_GPU_PACKAGES)}")
        # 装回 CPU 版，保证基础功能可用
        _pip_install(["onnxruntime"], proxy)


def ensure_available(proxy: str = "") -> bool:
    """确保 audio-separator 可用，必要时自动安装；有 NVIDIA GPU 时自动启用 CUDA 加速"""
    # GPU 换装必须在 audio_separator/onnxruntime 被导入前完成
    _ensure_gpu_acceleration(proxy)
    try:
        import audio_separator  # noqa: F401
    except ImportError:
        _log("首次使用人声分离功能，自动安装依赖（约 200MB）...")
        if not _pip_install(_REQUIRED_PACKAGES, proxy):
            _log("✗ 依赖安装失败。请在 .env 配置 NETWORK_PROXY 后重试，或手动执行:")
            _log(f"  pip install {' '.join(_REQUIRED_PACKAGES)}")
            return False
        try:
            import audio_separator  # noqa: F401
        except ImportError:
            _log("✗ 安装后仍无法导入 audio-separator")
            return False
    if _has_nvidia_gpu():
        if _cuda_provider_usable():
            _log("✓ 人声分离使用 GPU（CUDA）加速")
        else:
            _log("⚠ 检测到 GPU 但 CUDA 加速不可用，本次使用 CPU 推理")
    return True


def separate_vocals(audio_path: str, work_dir: str, base_name: str,
                    model_name: str = "UVR-MDX-NET-Voc_FT.onnx",
                    proxy: str = "") -> tuple:
    """分离人声和伴奏

    参数：
      audio_path: 输入音频（任意 ffmpeg 可读格式）
      work_dir: 输出目录
      base_name: 输出文件基名 → {base}_vocals.wav / {base}_accompaniment.wav
      model_name: 分离模型（默认 UVR-MDX-NET-Voc_FT， vocals/instrumental 双 stem）
      proxy: 下载模型用的代理（模型从 GitHub/HF 下载，国内建议配置）

    返回 (vocals_path, accompaniment_path)；失败返回 ("", "")。
    已分离过（目标文件存在）则直接复用，支持断点续跑。
    """
    vocals_out = os.path.join(work_dir, f"{base_name}_vocals.wav")
    accomp_out = os.path.join(work_dir, f"{base_name}_accompaniment.wav")
    if os.path.isfile(vocals_out) and os.path.isfile(accomp_out):
        _log(f"复用已有分离结果: {os.path.basename(vocals_out)}")
        return vocals_out, accomp_out

    if not ensure_available(proxy):
        return "", ""

    from audio_separator.separator import Separator

    _log(f"分离人声/伴奏: {os.path.basename(audio_path)}（模型: {model_name}）")
    _log("首次运行需下载模型（约 50-200MB），进度见上方下载条")
    sep = Separator(output_dir=work_dir, output_format="WAV", log_level=logging.INFO)
    # audio-separator 的 CUDA 开关依赖 torch.cuda.is_available()，但推理实际走 ONNX，
    # CPU 版 torch 会误判导致全程 CPU。这里直接覆盖 ONNX 执行提供者为 CUDA。
    if _cuda_provider_usable():
        sep.onnx_execution_provider = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    sep.load_model(model_name)
    outputs = sep.separate(audio_path)

    vocals_src = accomp_src = ""
    for p in outputs:
        name = os.path.basename(p)
        if "(Vocals)" in name:
            vocals_src = p if os.path.isabs(p) else os.path.join(work_dir, name)
        elif "(Instrumental)" in name or "(No Vocals)" in name:
            accomp_src = p if os.path.isabs(p) else os.path.join(work_dir, name)

    if not vocals_src or not os.path.isfile(vocals_src):
        _log("✗ 分离失败：未找到人声轨输出")
        return "", ""

    # 统一命名为固定文件名（后续步骤按名引用）
    os.replace(vocals_src, vocals_out)
    if accomp_src and os.path.isfile(accomp_src):
        os.replace(accomp_src, accomp_out)
    else:
        # 极少数模型只输出人声：用原音频减人声不靠谱，直接退化为静音伴奏不可用，
        # 这里用原音频作为伴奏占位（相当于保留原声模式）
        _log("⚠ 模型未输出伴奏轨，伴奏将使用原始音频（可能会听到原人声）")
        import shutil
        shutil.copy2(audio_path, accomp_out)

    _log(f"✓ 人声: {os.path.basename(vocals_out)} / 伴奏: {os.path.basename(accomp_out)}")
    return vocals_out, accomp_out
