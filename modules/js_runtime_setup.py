"""JS 运行时（node/deno）自动检测与下载。

背景：yt-dlp 2026.x 起，YouTube 的 nsig 签名必须借助外部 JS 运行时
（node / deno / bun / quickjs）解算，否则视频流地址一律返回 403。

本模块在首次下载 YouTube 视频时自动检测本机运行时：
  1. 系统 PATH 中的 node / deno —— 直接使用
  2. 项目 .tools/ 目录中已下载的 deno —— 直接使用
  3. 都没有 → 自动下载 deno 单文件版（约 30MB，带进度显示），
     下载源回退链：代理+GitHub → 直连 GitHub → 国内镜像
"""

import os
import shutil
import zipfile

_RUNTIME_CACHE = None  # 检测结果缓存（None=未检测）


def _tools_dir() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".tools")
    os.makedirs(path, exist_ok=True)
    return path


def _local_deno() -> str:
    name = "deno.exe" if os.name == "nt" else "deno"
    path = os.path.join(_tools_dir(), name)
    return path if os.path.isfile(path) else ""


def _detect() -> dict:
    """检测可用运行时，返回 yt-dlp js_runtimes 参数字典（无可用则空 dict）"""
    runtimes = {}
    if shutil.which("deno"):
        runtimes["deno"] = {}
    if shutil.which("node"):
        runtimes["node"] = {}
    local = _local_deno()
    if "deno" not in runtimes and local:
        runtimes["deno"] = {"path": local}
    return runtimes


def _download_deno(proxy: str = "") -> bool:
    """下载 deno 到项目 .tools/ 目录，带回退链和进度显示"""
    if os.name != "nt":
        print("[js-runtime] 非 Windows 系统，请自行安装 node 或 deno: https://nodejs.org")
        return False

    zip_name = "deno-x86_64-pc-windows-msvc.zip"
    url = f"https://github.com/denoland/deno/releases/latest/download/{zip_name}"
    mirrors = [f"https://mirror.ghproxy.com/{url}",
               f"https://gh-proxy.com/{url}"]

    attempts = []
    if proxy:
        attempts.append(("GitHub 官方（走代理）", url, proxy))
    attempts.append(("GitHub 官方（直连）", url, None))
    for m in mirrors:
        attempts.append((f"国内镜像 {m.split('/')[2]}（直连）", m, None))

    zip_path = os.path.join(_tools_dir(), zip_name)
    for label, u, px in attempts:
        print(f"[js-runtime] --- 尝试: {label} ---")
        try:
            _download_with_progress(u, zip_path, px)
            with zipfile.ZipFile(zip_path) as z:
                z.extractall(_tools_dir())
            os.remove(zip_path)
            print(f"[js-runtime] ✓ deno 已安装到 {_tools_dir()}")
            return True
        except Exception as e:
            print(f"[js-runtime] ✗ 「{label}」失败: {str(e)[:150]}")
            if os.path.isfile(zip_path):
                os.remove(zip_path)

    print("[js-runtime] ✗ deno 所有下载源均失败")
    print("[js-runtime]   建议在 .env 中配置 NETWORK_PROXY 后重试，")
    print("[js-runtime]   或手动安装 Node.js: https://nodejs.org")
    return False


def _download_with_progress(url: str, dest: str, proxy: str = None):
    """流式下载并打印进度"""
    import requests
    proxies = {"http": proxy, "https": proxy} if proxy else None
    with requests.get(url, stream=True, timeout=(15, 60), proxies=proxies,
                      allow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        last_pct = -10
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= last_pct + 10:
                        print(f"[js-runtime] 下载中 {pct}% "
                              f"({done / 1048576:.1f}/{total / 1048576:.1f} MB)")
                        last_pct = pct
                else:
                    print(f"[js-runtime] 已下载 {done / 1048576:.1f} MB", end="\r")


def get_js_runtimes(proxy: str = "", auto_download: bool = True) -> dict:
    """获取 yt-dlp 的 js_runtimes 参数。

    首次调用时检测系统 node/deno；都没有且 auto_download=True 时
    自动下载 deno（进度可见）。结果缓存，后续调用零开销。
    返回 {} 表示无可用运行时（yt-dlp 将回退到内置行为）。
    """
    global _RUNTIME_CACHE
    if _RUNTIME_CACHE is not None:
        return _RUNTIME_CACHE

    runtimes = _detect()
    if not runtimes and auto_download:
        print("[js-runtime] 未检测到 node/deno，YouTube 下载需要 JS 运行时解签名")
        print("[js-runtime] 正在自动下载 deno（约 30MB，仅需一次）...")
        if _download_deno(proxy):
            runtimes = _detect()
    if runtimes:
        names = ", ".join(runtimes)
        print(f"[js-runtime] 使用 JS 运行时: {names}")
    _RUNTIME_CACHE = runtimes
    return runtimes
