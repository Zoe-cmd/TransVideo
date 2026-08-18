# -*- coding: utf-8 -*-
"""人声分离 —— 把音频拆成人声轨 + 伴奏轨

用途：翻译配音时保留原视频背景音（BGM、环境声），只替换人声。
典型场景：外语 ASMR、带背景音乐的教学视频。

技术方案：audio-separator（UVR MDX-NET / RoFormer ONNX 模型）
  - 纯 ONNX 推理，CPU 可跑（有 onnxruntime-gpu 时自动用 GPU）
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
_ALIYUN_PYPI = "https://mirrors.aliyun.com/pypi/simple"


def _log(msg: str):
    print(f"[separate] {msg}", flush=True)


def _pip_install(proxy: str = "") -> bool:
    """安装 audio-separator 及依赖，返回是否成功

    回退链：代理+官方源 → 直连官方源 → 阿里云镜像。pip 输出实时可见。
    """
    attempts = []
    if proxy:
        attempts.append(("代理 + 官方源", proxy))
    attempts.append(("直连官方源", ""))
    attempts.append(("阿里云镜像", "mirror"))

    for label, mode in attempts:
        _log(f"安装人声分离依赖（{label}）...")
        env = os.environ.copy()
        cmd = [sys.executable, "-m", "pip", "install"] + _REQUIRED_PACKAGES
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


def ensure_available(proxy: str = "") -> bool:
    """确保 audio-separator 可用，必要时自动安装。返回是否可用"""
    try:
        import audio_separator  # noqa: F401
        return True
    except ImportError:
        pass
    _log("首次使用人声分离功能，自动安装依赖（约 200MB）...")
    if not _pip_install(proxy):
        _log("✗ 依赖安装失败。请在 .env 配置 NETWORK_PROXY 后重试，或手动执行:")
        _log(f"  pip install {' '.join(_REQUIRED_PACKAGES)}")
        return False
    try:
        import audio_separator  # noqa: F401
        return True
    except ImportError:
        _log("✗ 安装后仍无法导入 audio-separator")
        return False


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
