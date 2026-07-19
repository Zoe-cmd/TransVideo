# -*- coding: utf-8 -*-
"""Ollama 本地模型翻译模块 —— 无需 API Key，完全本地运行

Ollama 是本地大模型运行工具，2026 年月下载量超 5000 万。
本模块使用 Ollama 原生 /api/chat 端点（不依赖 openai 库），
并复用 OpenAITranslator 的批处理/重试/降级逻辑。

优势：
  - 完全免费，无配额限制
  - 数据不出本地，隐私安全
  - 支持多种开源模型（Qwen / Llama / GLM / Mistral 等）

使用前提：
  1. 安装 Ollama: https://ollama.com
  2. 拉取模型: ollama pull qwen2.5:7b
  3. 启动服务: ollama serve（默认 localhost:11434）
"""

import os
import json
import time
import requests
from typing import List

from config import Config
from modules.transcriber import Segment
from modules.translator import OpenAITranslator, Color, LANG_NAMES


# 默认配置
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"

# 推荐用于翻译的模型列表（按质量/速度排序）
RECOMMENDED_MODELS = [
    ("qwen2.5:7b", "通义千问 7B（推荐，中英翻译质量好，速度快）"),
    ("qwen2.5:14b", "通义千问 14B（质量更好，需 16GB+ 内存）"),
    ("qwen3:8b", "通义千问 3 8B（2026 新版，支持思考模式）"),
    ("glm4:9b", "智谱 GLM-4 9B（中文优秀）"),
    ("llama3.1:8b", "Llama 3.1 8B（Meta 开源，多语言）"),
    ("mistral:7b", "Mistral 7B（欧洲多语言）"),
    ("gemma2:9b", "Gemma 2 9B（Google 开源）"),
    ("phi3:14b", "Phi-3 14B（微软，轻量）"),
]


class OllamaTranslator(OpenAITranslator):
    """Ollama 本地翻译器

    继承自 OpenAITranslator，复用批处理/重试/降级/解析逻辑，
    只重写底层 API 调用方法使用 requests 调用 Ollama 原生 /api/chat 端点。
    """

    def __init__(self, config: Config):
        # 不调用父类 __init__（避免创建 openai client），手动初始化必要字段
        self.config = config
        self.model = config.ollama_model or DEFAULT_OLLAMA_MODEL
        self.base_url = (config.ollama_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.batch_size = config.ollama_batch_size or 10
        self.timeout = config.ollama_timeout or 120  # 本地模型推理较慢，给足超时

        # 连接检查状态（延迟检查，避免每次翻译都测试）
        self._connection_checked = False
        self._connection_ok = False

    def _check_connection(self) -> bool:
        """检查 Ollama 服务是否可用 + 模型是否存在"""
        if self._connection_checked:
            return self._connection_ok

        self._connection_checked = True

        # 1. 检查服务是否运行
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                print(f"[translate] {Color.RED}✗ Ollama 服务返回异常: {resp.status_code}{Color.RESET}")
                return False
        except requests.exceptions.ConnectionError:
            print(f"[translate] {Color.RED}✗ 无法连接到 Ollama 服务: {self.base_url}{Color.RESET}")
            print(f"[translate] {Color.DIM}  请确保 Ollama 已启动:{Color.RESET}")
            print(f"[translate] {Color.DIM}  1. 安装: https://ollama.com{Color.RESET}")
            print(f"[translate] {Color.DIM}  2. 启动服务: ollama serve{Color.RESET}")
            print(f"[translate] {Color.DIM}  3. 拉取模型: ollama pull {self.model}{Color.RESET}")
            return False
        except Exception as e:
            print(f"[translate] {Color.RED}✗ Ollama 连接失败: {e}{Color.RESET}")
            return False

        # 2. 检查模型是否已拉取
        try:
            data = resp.json()
            installed_models = [m.get("name", "") for m in data.get("models", [])]
            if self.model not in installed_models:
                # 模糊匹配（ollama 的 name 可能带 :latest 后缀）
                # 例如配置 qwen2.5:7b，已安装 qwen2.5:latest → 匹配
                base_name = self.model.split(":")[0]
                matched = [m for m in installed_models if m.startswith(base_name)]
                if not matched:
                    print(f"[translate] {Color.YELLOW}⚠ 模型 {self.model} 未安装{Color.RESET}")
                    print(f"[translate] {Color.DIM}  已安装的模型: {', '.join(installed_models) if installed_models else '(无)'}{Color.RESET}")
                    print(f"[translate] {Color.DIM}  请运行: ollama pull {self.model}{Color.RESET}")
                    # 不返回 False，尝试用已安装的模型继续
                    if installed_models:
                        # 优先选择同系列的，否则选第一个
                        self.model = matched[0] if matched else installed_models[0]
                        print(f"[translate] {Color.DIM}  自动切换到: {self.model}{Color.RESET}")
                else:
                    # 找到匹配，更新为实际安装的模型名
                    self.model = matched[0]
                    print(f"[translate] {Color.DIM}  模型匹配: {self.model}{Color.RESET}")
        except Exception:
            pass  # 解析失败不阻塞，继续尝试

        self._connection_ok = True
        print(f"[translate] {Color.GREEN}✓ Ollama 服务可用: {self.base_url} (模型: {self.model}){Color.RESET}")
        return True

    def _build_prompt(self, source_lang: str, target_lang: str, batch_texts: List[str]) -> str:
        """为本地模型优化的 prompt

        本地模型对 JSON 数组格式遵循度更高（边界明确，不易合并/漏译），
        因此要求模型返回 JSON 数组而非带编号的纯文本。
        """
        src_name = LANG_NAMES.get(source_lang, source_lang)
        tgt_name = LANG_NAMES.get(target_lang, target_lang)

        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(batch_texts))
        n = len(batch_texts)

        return (
            f"将以下{src_name}字幕翻译为{tgt_name}。\n"
            f"要求：保持口语化、原意准确、术语统一、每条独立翻译。\n"
            f"只返回 JSON 数组（共 {n} 个字符串，顺序与原文对应），不要其他内容。\n"
            f"格式：[\"译文1\", \"译文2\", ...]\n\n"
            f"原文：\n{numbered}\n\n"
            f"JSON："
        )

    def _parse_response(self, response: str, expected_count: int) -> List[str]:
        """解析本地模型返回，支持 JSON / 编号 / 按行 三种策略

        策略1：JSON 数组（优先，最可靠）
        策略2：带编号格式 [N] / N. / N、 / N) 等
        策略3：按行分割（最后回退）
        """
        import re

        # 去除思考标签（qwen3 等模型的思考模式，_call_api_with_retry 已处理，这里做二次保险）
        if "<think>" in response and "</think>" in response:
            response = re.sub(r"<think>.*?</think>\s*", "", response, flags=re.DOTALL).strip()

        # ===== 策略1：JSON 数组 =====
        try:
            cleaned = response.strip()
            # 去除 markdown 代码块标记 ```json ... ```
            if cleaned.startswith("```"):
                cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
                cleaned = re.sub(r'\s*```$', '', cleaned).strip()
            # 找到第一个 [ 到最后一个 ] 的范围
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start != -1 and end != -1 and end > start:
                json_str = cleaned[start:end + 1]
                arr = json.loads(json_str)
                if isinstance(arr, list) and len(arr) >= expected_count:
                    return [str(x).strip() for x in arr[:expected_count]]
                elif isinstance(arr, list) and arr:
                    # JSON 解析成功但数量不足，先用已解析的，剩余留空
                    result = [str(x).strip() for x in arr]
                    while len(result) < expected_count:
                        result.append("")
                    return result[:expected_count]
        except (json.JSONDecodeError, ValueError):
            pass

        # ===== 策略2：编号格式 =====
        translations = []
        lines = response.strip().split("\n")
        # 多种编号格式
        patterns = [
            r'^\[(\d+)\]\s*(.*)',      # [1] 译文
            r'^<<(\d+)>>\s*(.*)',      # <<1>> 译文
            r'^(\d+)\.\s*(.*)',         # 1. 译文
            r'^(\d+)、\s*(.*)',         # 1、译文
            r'^(\d+):\s*(.*)',          # 1: 译文
            r'^(\d+)\)\s*(.*)',         # 1) 译文
        ]
        for line in lines:
            line = line.strip()
            if not line:
                continue
            matched = False
            for pat in patterns:
                m = re.match(pat, line)
                if m:
                    num = int(m.group(1))
                    text = m.group(2).strip()
                    # 按编号填到正确位置
                    while len(translations) < num - 1:
                        translations.append("")
                    if num - 1 < len(translations):
                        translations[num - 1] = text
                    else:
                        translations.append(text)
                    matched = True
                    break
            if not matched and translations:
                # 续行：追加到上一条
                translations[-1] += " " + line

        if len(translations) == expected_count:
            return translations

        # ===== 策略3：按行分割 =====
        clean_lines = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 跳过明显的非译文行
            if line.startswith("```") or line.startswith("JSON") or line.startswith("译文"):
                continue
            clean_lines.append(line)

        if len(clean_lines) >= expected_count:
            return clean_lines[:expected_count]

        # 最后回退：用已有结果 + 空字符串填充
        while len(translations) < expected_count:
            translations.append("")
        return translations[:expected_count]

    def _call_api_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        """调用 Ollama /api/chat，带指数退避重试

        复用父类的重试框架，只替换底层 API 调用。
        """
        # 首次调用时检查连接
        if not self._connection_checked:
            if not self._check_connection():
                raise RuntimeError(f"Ollama 服务不可用: {self.base_url}")

        last_error = None
        for attempt in range(max_retries):
            try:
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "你是专业的视频字幕翻译助手。"},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_ctx": 8192,  # 上下文长度
                    },
                }
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )

                if resp.status_code != 200:
                    err_text = resp.text[:200]
                    # 模型不存在的错误
                    if resp.status_code == 404 and "model" in err_text.lower():
                        raise RuntimeError(
                            f"模型不存在: {self.model}。请运行: ollama pull {self.model}"
                        )
                    raise RuntimeError(f"Ollama API 返回 {resp.status_code}: {err_text}")

                data = resp.json()
                content = data.get("message", {}).get("content", "").strip()
                if not content:
                    raise RuntimeError("Ollama 返回空内容")

                # 检查是否有思考标签（qwen3 等模型的思考模式）
                #  标签内容
                if "<think>" in content and "</think>" in content:
                    import re
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()

                return content

            except Exception as e:
                last_error = e
                err_str = str(e)
                err_type = type(e).__name__

                # 判断是否可重试
                retryable = (
                    "timeout" in err_str.lower() or "timed out" in err_str.lower()
                    or "connection" in err_str.lower()
                    or "ConnectionError" in err_type
                    or "503" in err_str or "502" in err_str
                )

                # 模型不存在/服务不可用错误不重试
                if "模型不存在" in err_str or "Ollama 服务不可用" in err_str:
                    raise

                if not retryable:
                    raise

                # 可重试：指数退避
                wait = 2 ** attempt
                if attempt < max_retries - 1:
                    print(f"[translate]     重试 {attempt+1}/{max_retries} (等待 {wait}s): {err_str[:80]}")
                    time.sleep(wait)
                else:
                    print(f"[translate]     重试已耗尽: {err_str[:80]}")

        raise last_error

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        """重写 translate：在调用父类前先检查 Ollama 连接"""
        # 先检查连接（给出明确提示）
        if not self._check_connection():
            print(f"[translate] {Color.RED}✗ Ollama 不可用，所有片段用原文填充{Color.RESET}")
            for seg in segments:
                seg.translated_text = seg.text
            return segments

        # 调用父类的 translate（复用批处理/重试/降级逻辑）
        return super().translate(segments, source_lang, target_lang)


def list_installed_models(base_url: str = DEFAULT_OLLAMA_URL) -> list:
    """获取已安装的 Ollama 模型列表"""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        pass
    return []


def is_ollama_running(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    """检查 Ollama 服务是否在运行"""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False
