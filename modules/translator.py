# -*- coding: utf-8 -*-
"""翻译模块 —— 支持 OpenAI（自定义模型/base_url）和 Google 免费翻译

翻译策略：
  - 分批翻译（每批 20 个片段），提高效率
  - 要求模型按顺序返回译文，保持与原片段对齐
  - 翻译模型、base_url 均可在 transvideo_config.json 中自定义
"""

import json
import time
import requests
from typing import List

from config import Config
from modules.transcriber import Segment


# ANSI 颜色（避免循环依赖 cli.py）
class Color:
    RED = '\033[91m'
    YELLOW = '\033[93m'
    GREEN = '\033[92m'
    CYAN = '\033[96m'
    DIM = '\033[2m'
    RESET = '\033[0m'


# 语言代码 → 名称映射（用于 prompt）
LANG_NAMES = {
    "zh": "中文", "en": "English", "ja": "日本語",
    "ko": "한국어", "fr": "Français", "de": "Deutsch",
    "es": "Español", "ru": "Русский", "ar": "العربية",
}


class Translator:
    """翻译基类"""

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        raise NotImplementedError


class OpenAITranslator(Translator):
    """OpenAI GPT 翻译 —— 模型可自定义，支持模型级备选"""

    # 主模型不可用时的默认备选模型列表（按优先级）
    # 可被 config.translate_model_fallbacks 覆盖
    DEFAULT_MODEL_FALLBACKS = [
        "gpt-4o-mini",     # OpenAI 官方便宜稳定模型
        "gpt-3.5-turbo",   # 老牌稳定模型
        "gpt-4o",          # 高质量模型
    ]

    def __init__(self, config: Config):
        self.config = config
        from openai import OpenAI
        # 支持自定义 base_url
        self.client = OpenAI(
            api_key=config.openai_api_key,
            base_url=config.openai_base_url if config.openai_base_url else None,
        )
        self.model = config.translate_model
        self.batch_size = 20
        # 构建模型备选链：优先用用户配置的备选链，否则用内置默认
        # 用户配置格式：translate_model_fallbacks: ["gpt-3.5-turbo", "gpt-4o"]
        # 主模型不在备选链中（自动去重）
        user_fallbacks = getattr(config, "translate_model_fallbacks", None) or []
        if user_fallbacks:
            # 用户配置的备选链（去重，去掉主模型）
            self.model_fallbacks = [m for m in user_fallbacks if m != self.model]
        else:
            # 内置默认备选链（去重，去掉主模型）
            self.model_fallbacks = [m for m in self.DEFAULT_MODEL_FALLBACKS if m != self.model]
        # 当前使用的模型（可能在运行中切换）
        self._active_model = self.model
        # 已知不可用的模型（避免重复尝试）
        self._failed_models = set()

        # 打印模型备选链（帮助用户确认配置生效）
        if self.model_fallbacks:
            print(f"[translate] OpenAI 模型链: {self.model} → {' → '.join(self.model_fallbacks)}")

    def _build_prompt(self, source_lang: str, target_lang: str, batch_texts: List[str]) -> str:
        src_name = LANG_NAMES.get(source_lang, source_lang)
        tgt_name = LANG_NAMES.get(target_lang, target_lang)

        # 构造带编号的文本，要求模型按编号返回
        numbered = "\n".join(f"[{i+1}] {t}" for i, t in enumerate(batch_texts))

        return (
            f"你是专业视频字幕翻译。将以下{src_name}字幕翻译为{tgt_name}。\n"
            f"要求：\n"
            f"1. 保持口语化，适合配音和阅读\n"
            f"2. 保持原意，不增删信息\n"
            f"3. 科普/教学类内容，专业术语翻译准确\n"
            f"4. 严格按编号格式返回译文，每行一条：[编号] 译文\n\n"
            f"原文：\n{numbered}\n\n"
            f"译文（严格按 [编号] 格式）："
        )

    def _parse_response(self, response: str, expected_count: int) -> List[str]:
        """解析模型返回的带编号译文"""
        translations = []
        lines = response.strip().split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # 匹配 [编号] 译文 格式
            import re
            m = re.match(r'^\[?\d+\]?\s*(.*)', line)
            if m:
                translations.append(m.group(1).strip())
            elif translations:
                # 多行译文续行
                translations[-1] += " " + line

        # 如果解析数量不匹配，回退到按行分割
        if len(translations) != expected_count:
            print(f"[translate] 警告: 解析数量 {len(translations)} != 预期 {expected_count}，回退处理")
            clean_lines = [l.strip() for l in response.strip().split("\n") if l.strip()]
            if len(clean_lines) >= expected_count:
                translations = clean_lines[:expected_count]
            else:
                # 不足的用原文填充
                while len(translations) < expected_count:
                    translations.append("")

        return translations[:expected_count]

    def _call_api_with_retry(self, prompt: str, max_retries: int = 3) -> str:
        """调用 OpenAI API，带指数退避重试 + 模型级备选

        重试策略：
          1. 可重试错误（503/502/429/超时/连接）→ 指数退避重试
          2. 模型错误（404/model not found）→ 切换到备选模型
          3. 认证错误（401）→ 立即抛出（备选模型也会失败）
        """
        # 构建本次调用的模型备选链：当前活跃模型 + 未失败的备选模型
        model_chain = [self._active_model]
        for m in self.model_fallbacks:
            if m not in self._failed_models and m != self._active_model:
                model_chain.append(m)

        for model_idx, model in enumerate(model_chain):
            last_error = None
            for attempt in range(max_retries):
                try:
                    resp = self.client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": "你是专业的视频字幕翻译助手。"},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.3,
                    )
                    # 成功：更新活跃模型
                    if model != self._active_model:
                        print(f"[translate]     ✓ 切换到备选模型: {model}")
                        self._active_model = model
                    return resp.choices[0].message.content.strip()
                except Exception as e:
                    last_error = e
                    err_str = str(e)
                    err_type = type(e).__name__

                    # 判断错误类型
                    is_model_error = "404" in err_str or ("model" in err_str.lower() and "not found" in err_str.lower())
                    is_auth_error = "401" in err_str or "Authentication" in err_str or "api_key" in err_str.lower()
                    is_retryable = (
                        "503" in err_str or "502" in err_str or "429" in err_str
                        or "timeout" in err_str.lower() or "timed out" in err_str.lower()
                        or "connection" in err_str.lower()
                        or "RateLimit" in err_type or "APITimeoutError" in err_type
                        or "APIConnectionError" in err_type or "InternalServerError" in err_type
                    )

                    # 模型错误：标记模型失败，尝试备选模型
                    if is_model_error:
                        self._failed_models.add(model)
                        if model_idx < len(model_chain) - 1:
                            print(f"[translate]     模型 {model} 不可用，切换备选模型: {model_chain[model_idx+1]}")
                            break  # 跳出重试循环，尝试下一个模型
                        else:
                            raise  # 所有模型都试过了
                    # 认证错误：立即抛出（备选模型也会失败）
                    if is_auth_error:
                        raise
                    # 可重试错误：指数退避
                    if is_retryable and attempt < max_retries - 1:
                        wait = 2 ** attempt  # 1s, 2s, 4s
                        print(f"[translate]     重试 {attempt+1}/{max_retries} (等待 {wait}s): {err_str[:80]}")
                        time.sleep(wait)
                    else:
                        # 不可重试错误 或 重试耗尽
                        # 如果还有备选模型，尝试切换
                        if not is_retryable and model_idx < len(model_chain) - 1:
                            self._failed_models.add(model)
                            print(f"[translate]     模型 {model} 错误，切换备选: {model_chain[model_idx+1]}")
                            break
                        if is_retryable:
                            print(f"[translate]     重试已耗尽: {err_str[:80]}")

            # 如果是 break 出来的（模型切换），继续下一个模型
            # 如果是正常退出（重试耗尽），也尝试下一个模型
            if model_idx < len(model_chain) - 1:
                continue
            # 最后一个模型也失败了
            if last_error:
                raise last_error
            raise RuntimeError(f"所有模型均失败: {model_chain}")
        raise RuntimeError("无可用模型")

    def _translate_single(self, text: str, source_lang: str, target_lang: str) -> str:
        """单条翻译（用于批次失败时降级）"""
        prompt = self._build_prompt(source_lang, target_lang, [text])
        content = self._call_api_with_retry(prompt, max_retries=2)
        translations = self._parse_response(content, 1)
        return translations[0] if translations and translations[0] else text

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        print(f"[translate] OpenAI 翻译: {source_lang} → {target_lang} (模型: {self.model})")
        print(f"[translate] 共 {len(segments)} 个片段，分批 {self.batch_size}/批")

        total_batches = (len(segments) + self.batch_size - 1) // self.batch_size
        success_count = 0
        fallback_count = 0
        failed_count = 0

        for batch_idx in range(total_batches):
            start = batch_idx * self.batch_size
            end = min(start + self.batch_size, len(segments))
            batch = segments[start:end]

            print(f"[translate]   批次 {batch_idx+1}/{total_batches} ({len(batch)} 条)...")

            batch_texts = [s.text for s in batch]
            prompt = self._build_prompt(source_lang, target_lang, batch_texts)

            try:
                # 带重试的 API 调用
                content = self._call_api_with_retry(prompt, max_retries=3)
                translations = self._parse_response(content, len(batch))

                for i, seg in enumerate(batch):
                    translated = translations[i] if i < len(translations) and translations[i] else seg.text
                    if translated == seg.text and translations[i] != seg.text:
                        # 解析失败，标记需要单条重试
                        translated = None
                    if translated:
                        seg.translated_text = translated
                        success_count += 1
                    else:
                        # 标记为需要单条降级
                        seg.translated_text = None

                # 对解析失败的片段做单条降级
                for seg in batch:
                    if seg.translated_text is None:
                        try:
                            seg.translated_text = self._translate_single(seg.text, source_lang, target_lang)
                            fallback_count += 1
                            success_count += 1
                        except Exception:
                            seg.translated_text = seg.text
                            failed_count += 1

            except Exception as e:
                err_str = str(e)
                print(f"[translate]   批次 {batch_idx+1} 失败: {err_str[:100]}")

                # 诊断错误类型
                is_auth_error = "401" in err_str or "Authentication" in err_str or "api_key" in err_str.lower()
                is_model_error = "404" in err_str or ("model" in err_str.lower() and "not found" in err_str.lower())

                if is_auth_error:
                    print(f"[translate]   {Color.RED}✗ API Key 无效，请检查配置{Color.RESET}")
                elif is_model_error:
                    print(f"[translate]   {Color.RED}✗ 模型不存在: {self.model}{Color.RESET}")
                    print(f"[translate]   {Color.DIM}  请在 .env 中修改 TRANSLATE_MODEL{Color.RESET}")
                    print(f"[translate]   {Color.DIM}  可选: gpt-4o-mini / gpt-4o / gpt-3.5-turbo{Color.RESET}")
                elif "503" in err_str or "502" in err_str:
                    print(f"[translate]   {Color.YELLOW}⚠ API 服务端临时不可用（503），尝试单条降级...{Color.RESET}")
                elif "429" in err_str:
                    print(f"[translate]   {Color.YELLOW}⚠ 触发速率限制（429），单条降级并等待...{Color.RESET}")

                # 认证/模型错误不降级（单条也会失败），其他错误尝试单条降级
                if is_auth_error or is_model_error:
                    for seg in batch:
                        seg.translated_text = seg.text
                        failed_count += 1
                else:
                    # 批次失败 → 单条逐个翻译降级
                    for seg in batch:
                        try:
                            seg.translated_text = self._translate_single(seg.text, source_lang, target_lang)
                            fallback_count += 1
                            success_count += 1
                        except Exception:
                            seg.translated_text = seg.text
                            failed_count += 1

            time.sleep(0.3)  # 避免 rate limit

        # 汇总
        print(f"[translate] 翻译完成: 成功 {success_count}, 降级 {fallback_count}, 失败 {failed_count}")
        if failed_count > len(segments) // 2:
            print(f"[translate] {Color.RED}✗ 失败率过高 ({failed_count}/{len(segments)}){Color.RESET}")
            print(f"[translate] {Color.YELLOW}建议：1)检查网络 2)更换模型 3)稍后重试{Color.RESET}")
        elif failed_count > 0:
            print(f"[translate] {Color.YELLOW}⚠ 部分片段翻译失败，已用原文填充{Color.RESET}")
        return segments


class GoogleTranslator(Translator):
    """Google 翻译免费版（无需 API key，多端点容错）"""

    # 多个端点，依次尝试
    ENDPOINTS = [
        "https://translate.google.cn/translate_a/single",   # 国内可用
        "https://translate.googleapis.com/translate_a/single",  # 国际
    ]

    def __init__(self, config: Config):
        self.config = config
        self.timeout = 8  # 缩短超时，快速失败降级
        self.working_endpoint = None  # 缓存可用端点

    def _translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        """单条翻译，多端点容错"""
        params = {
            "client": "gtx",
            "sl": source_lang if source_lang != "auto" else "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }

        # 如果已找到可用端点，优先使用
        endpoints = [self.working_endpoint] + self.ENDPOINTS if self.working_endpoint else self.ENDPOINTS
        seen = set()
        for url in endpoints:
            if url in seen:
                continue
            seen.add(url)
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                data = resp.json()
                translated = "".join(item[0] for item in data[0] if item[0])
                if translated:
                    self.working_endpoint = url  # 缓存可用端点
                    return translated
            except Exception:
                continue
        raise RuntimeError("所有 Google 翻译端点均不可用")

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        print(f"[translate] Google 翻译: {source_lang} → {target_lang}")
        print(f"[translate] 共 {len(segments)} 个片段（多端点容错）")

        failed = 0
        for i, seg in enumerate(segments):
            try:
                seg.translated_text = self._translate_text(seg.text, source_lang, target_lang)
                if (i + 1) % 5 == 0:
                    print(f"[translate]   进度: {i+1}/{len(segments)}")
                time.sleep(0.1)
            except Exception as e:
                failed += 1
                seg.translated_text = seg.text
                if failed <= 3:
                    print(f"[translate]   片段 {i+1} 失败: {e}")

        if failed > len(segments) // 2:
            print(f"[translate] 失败率过高 ({failed}/{len(segments)})，建议使用其他引擎")
            raise RuntimeError(f"Google 翻译失败率 {failed}/{len(segments)} 过高")

        print(f"[translate] 翻译完成 (失败 {failed})")
        return segments


class MyMemoryTranslator(Translator):
    """MyMemory 免费翻译（无需 API key，国内可访问）

    注意：MyMemory 免费版有每日字数配额（约 5000 字/天），用尽后
    返回的译文会变成警告文本。本类会检测这种情况并提前终止。
    """

    # MyMemory 配额耗尽时的返回文本模式
    QUOTA_WARNING_PATTERNS = [
        "MYMEMORY WARNING",
        "YOU USED ALL",
        "FREE WORDS FOR TODAY",
        "PLEASE RETRY",
        "PLEASE USE A VALID EMAIL",
        "PLEASE SELECT TWO DISTINCT LANGUAGES",   # 新增：配额耗尽后的固定错误返回
        "SELECT TWO DISTINCT",                    # 简短匹配
        "INVALID LANGUAGE PAIR",
        "QUERY IS EMPTY",
    ]

    def __init__(self, config: Config):
        self.config = config
        self.url = "https://api.mymemory.translated.net/get"
        self.timeout = 10
        self.quota_exhausted = False  # 配额耗尽标记，避免重复请求

    def _is_quota_warning(self, text: str) -> bool:
        """检测是否是配额耗尽的警告文本"""
        if not text:
            return False
        text_upper = text.upper()
        return any(p in text_upper for p in self.QUOTA_WARNING_PATTERNS)

    def _translate_text(self, text: str, source_lang: str, target_lang: str) -> str:
        if self.quota_exhausted:
            raise RuntimeError("MyMemory 免费配额已耗尽（请配置 OpenAI API Key 或等待明日重置）")

        if source_lang == "auto":
            source_lang = "zh"  # MyMemory 需要明确源语言
        langpair = f"{source_lang}|{target_lang}"
        params = {"q": text, "langpair": langpair, "de": "a@b.c"}
        resp = requests.get(self.url, params=params, timeout=self.timeout)
        data = resp.json()

        if data.get("responseStatus") == 200 or "responseData" in data:
            translated = data["responseData"]["translatedText"]
            # 检测配额耗尽警告
            if self._is_quota_warning(translated):
                self.quota_exhausted = True
                raise RuntimeError(
                    "MyMemory 免费配额已耗尽（每日约 5000 字）\n"
                    "解决方案：\n"
                    "  1. 配置 OpenAI API Key（推荐，质量更好且无配额限制）\n"
                    "  2. 等待明日重置（24 小时后）\n"
                    "  3. 使用 --translate-engine google（国内可能不可用）"
                )
            return translated
        raise RuntimeError(f"MyMemory 错误: {data}")

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        print(f"[translate] MyMemory 翻译: {source_lang} → {target_lang}")
        print(f"[translate] 共 {len(segments)} 个片段")
        print(f"[translate] {Color.YELLOW}⚠ MyMemory 免费版每日约 5000 字配额{Color.RESET}")

        failed = 0
        success_count = 0
        for i, seg in enumerate(segments):
            # 配额耗尽后立即停止
            if self.quota_exhausted:
                # 剩余片段保留原文（至少字幕还能烧录）
                seg.translated_text = seg.text
                continue

            try:
                seg.translated_text = self._translate_text(seg.text, source_lang, target_lang)
                success_count += 1
                if (i + 1) % 5 == 0:
                    print(f"[translate]   进度: {i+1}/{len(segments)} (成功 {success_count})")
                time.sleep(0.5)  # MyMemory 有速率限制
            except Exception as e:
                failed += 1
                seg.translated_text = seg.text  # 失败时保留原文
                if failed <= 3:
                    print(f"[translate]   片段 {i+1} 失败: {e}")

        # 配额耗尽特殊处理
        if self.quota_exhausted:
            print(f"[translate] {Color.RED}✗ MyMemory 免费配额已耗尽{Color.RESET}")
            print(f"[translate]   成功翻译 {success_count}/{len(segments)} 个片段")
            print(f"[translate]   剩余 {len(segments) - success_count} 个保留原文")
            print(f"[translate] {Color.YELLOW}建议配置 OpenAI API Key 以获得更好的翻译质量{Color.RESET}")
        else:
            print(f"[translate] 翻译完成 (成功 {success_count}, 失败 {failed})")
        return segments


def create_translator(config: Config) -> Translator:
    """工厂函数：根据配置创建翻译器

    支持引擎级联备选（自动降级）:
      - openai 模式: OpenAI(主) → Ollama(备1) → Google(备2) → MyMemory(兜底)
      - ollama 模式: Ollama(主) → OpenAI(备1,如有 key) → Google(备2) → MyMemory(兜底)
      - google 模式: Google(主) → MyMemory(兜底)
      - mymemory 模式: MyMemory
      - auto 模式: 按可用性自动选择

    OpenAI 引擎内部还支持模型级备选（见 OpenAITranslator.model_fallbacks）。
    """
    # 构建备选引擎链
    primary = config.translate_engine or "auto"
    chain = _build_engine_chain(config, primary)
    return FallbackTranslator(config, chain)


def _build_engine_chain(config: Config, primary: str) -> List[Translator]:
    """构建翻译引擎备选链

    返回按优先级排序的翻译器列表，FallbackTranslator 会依次尝试。
    """
    chain: List[Translator] = []
    added_types = set()

    def add(engine_type: str):
        if engine_type in added_types:
            return
        try:
            t = _create_single_engine(config, engine_type)
            if t is not None:
                chain.append(t)
                added_types.add(engine_type)
        except Exception as e:
            print(f"[translate] 引擎 {engine_type} 不可用: {str(e)[:80]}")

    # 主引擎
    if primary == "auto":
        # 自动模式：按可用性优先级排序
        if config.has_openai():
            add("openai")
        add("ollama")
        add("google")
        add("mymemory")
    else:
        add(primary)
        # 主引擎失败时的备选
        if primary == "openai":
            # OpenAI 失败 → Ollama → Google → MyMemory
            add("ollama")
            add("google")
            add("mymemory")
        elif primary == "ollama":
            # Ollama 失败 → OpenAI(如有 key) → Google → MyMemory
            if config.has_openai():
                add("openai")
            add("google")
            add("mymemory")
        elif primary == "google":
            # Google 失败 → MyMemory
            add("mymemory")
        # mymemory 无备选

    if not chain:
        # 最后兜底：MyMemory（无需 key，无需本地服务）
        add("mymemory")

    return chain


def _create_single_engine(config: Config, engine_type: str) -> Translator:
    """创建单个翻译引擎实例

    对需要预检的引擎（Ollama/Google）做可用性检查，
    不可用时返回 None（由调用方决定是否加入备选链）。
    """
    if engine_type == "openai":
        if not config.has_openai():
            return None
        return OpenAITranslator(config)

    if engine_type == "ollama":
        try:
            from modules.ollama_translator import OllamaTranslator, is_ollama_running
            url = getattr(config, "ollama_url", "http://localhost:11434") or "http://localhost:11434"
            if not is_ollama_running(url):
                return None
            return OllamaTranslator(config)
        except ImportError:
            return None

    if engine_type == "google":
        t = GoogleTranslator(config)
        try:
            t._translate_text("test", "zh", "en")
            return t
        except Exception:
            return None

    if engine_type == "mymemory":
        return MyMemoryTranslator(config)

    return None


class FallbackTranslator(Translator):
    """带级联备选的翻译器

    按备选链依次尝试翻译：
      - 优先用链首引擎翻译所有片段
      - 如果某引擎对某片段翻译失败（返回原文或空），切换到下一个引擎重试该片段
      - 所有引擎都失败时，用原文填充（确保流程能继续）
    """

    def __init__(self, config: Config, chain: List[Translator]):
        self.config = config
        self.chain = chain
        if chain:
            primary = chain[0]
            engine_name = type(primary).__name__
            chain_desc = " → ".join(type(t).__name__ for t in chain)
            print(f"[translate] 翻译引擎链: {chain_desc}")

    def translate(self, segments: List[Segment], source_lang: str, target_lang: str) -> List[Segment]:
        if not self.chain:
            print("[translate] ✗ 无可用翻译引擎，所有片段用原文填充")
            for seg in segments:
                seg.translated_text = seg.text
            return segments

        # 第一轮：用主引擎（链首）翻译所有片段
        primary = self.chain[0]
        print(f"[translate] 主引擎: {type(primary).__name__}")
        try:
            primary.translate(segments, source_lang, target_lang)
        except Exception as e:
            print(f"[translate] 主引擎失败: {str(e)[:100]}")
            # 主引擎整体失败，所有片段标记为待翻译
            for seg in segments:
                if not getattr(seg, "translated_text", None):
                    seg.translated_text = None

        # 第二轮：对失败片段用备选引擎逐个重试
        failed = [seg for seg in segments if not getattr(seg, "translated_text", None)
                  or seg.translated_text == seg.text]
        if failed and len(self.chain) > 1:
            print(f"[translate] {len(failed)} 个片段需要备选引擎重试")
            for seg in failed:
                seg.translated_text = None  # 清空，让备选引擎重新翻译

            for engine in self.chain[1:]:
                still_failed = [seg for seg in failed if not seg.translated_text]
                if not still_failed:
                    break
                print(f"[translate] 备选引擎 {type(engine).__name__}: 重试 {len(still_failed)} 个片段")
                try:
                    engine.translate(still_failed, source_lang, target_lang)
                except Exception as e:
                    print(f"[translate] 备选引擎 {type(engine).__name__} 失败: {str(e)[:80]}")

        # 最终兜底：仍然失败的片段用原文填充
        final_failed = 0
        for seg in segments:
            if not getattr(seg, "translated_text", None):
                seg.translated_text = seg.text
                final_failed += 1

        success = len(segments) - final_failed
        print(f"[translate] 翻译完成: 成功 {success}/{len(segments)}")
        if final_failed > 0:
            print(f"[translate] ⚠ {final_failed} 个片段所有引擎均失败，用原文填充")
        return segments
