#!/usr/bin/env python3
"""文章生成服务。Tavily 搜索真实学术素材 + LLM 改写嵌入生词，生成托福级长文。"""
import json
import logging
import os
import random
import re
from datetime import datetime
from pathlib import Path

import concurrent.futures

import requests

logger = logging.getLogger(__name__)


class ArticleGenerator:
    LOCAL_GENERATION_MODEL = "MiniMax-M2.7-highspeed"
    SOURCE_CACHE_MIN_ITEMS = 5
    SOURCE_CACHE_PREFETCH_BATCH = 8
    SOURCE_CACHE_MAX_ITEMS = 30
    SOURCE_CACHE_MAX_USES = 2
    TARGET_MIN_WORDS = 700
    TARGET_MAX_WORDS = 1000
    TARGET_MIN_EMBEDDED_WORDS = 20
    PARAGRAPH_REWRITE_WORKERS = 8
    PARAGRAPH_REWRITE_MAX_TOKENS = 600
    PARAGRAPH_REWRITE_TIMEOUT = 55
    PARAGRAPH_RETRY_TIMEOUT = 45
    PARAGRAPH_REWRITE_TARGET_CHUNKS = 6

    # 托福阅读常见学科主题，用于 Tavily 搜索
    ACADEMIC_TOPICS = [
        "climate change impact on biodiversity",
        "neuroscience cognitive development",
        "archaeological discoveries ancient civilizations",
        "renewable energy technology advances",
        "marine biology ocean ecosystems",
        "evolutionary biology natural selection",
        "urban planning sustainable cities",
        "astronomy exoplanet discoveries",
        "psychology behavioral economics",
        "genetics gene editing CRISPR",
        "geology plate tectonics volcanic activity",
        "anthropology cultural evolution",
        "environmental science deforestation",
        "materials science nanotechnology",
        "paleontology fossil record extinction",
    ]

    # 2026 托福阅读常见大类 → ACADEMIC_TOPICS 子集。
    TOPIC_TO_ACADEMIC = {
        "biology": [
            "marine biology ocean ecosystems",
            "evolutionary biology natural selection",
            "genetics gene editing CRISPR",
            "paleontology fossil record extinction",
        ],
        "technology": [
            "renewable energy technology advances",
            "materials science nanotechnology",
            "genetics gene editing CRISPR",
        ],
        "astronomy": [
            "astronomy exoplanet discoveries",
        ],
        "geology": [
            "geology plate tectonics volcanic activity",
        ],
        "environment": [
            "climate change impact on biodiversity",
            "environmental science deforestation",
        ],
        "psychology": [
            "neuroscience cognitive development",
            "psychology behavioral economics",
        ],
        "archaeology": [
            "archaeological discoveries ancient civilizations",
            "paleontology fossil record extinction",
        ],
        "humanities": [
            "anthropology cultural evolution",
            "archaeological discoveries ancient civilizations",
        ],
        "urban": [
            "urban planning sustainable cities",
        ],
    }

    def __init__(self):
        self.project_root = Path(__file__).resolve().parents[1]
        self.data_dir = self.project_root / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.source_cache_file = self.data_dir / "article_source_cache.json"
        self.api_key = self._load_tavily_api_key()
        self.llm_base_url = ""
        self.llm_api_key = ""
        self.llm_model = self.LOCAL_GENERATION_MODEL
        self.config_article_model = ""
        self.config_article_base_url = ""
        self.config_article_api_key = ""
        self.llm_timeout_seconds = 35
        self.available_models = {}
        self._active_topic_filter = None
        self._load_project_llm_config()

        article_env_base_url = os.getenv("ARTICLE_LLM_BASE_URL", "").strip()
        article_env_api_key = os.getenv("ARTICLE_LLM_API_KEY", "").strip()
        generic_env_base_url = os.getenv("LLM_BASE_URL", "").strip()
        generic_env_api_key = os.getenv("LLM_API_KEY", "").strip()

        if article_env_base_url:
            self.llm_base_url = article_env_base_url
        elif self.config_article_base_url:
            self.llm_base_url = self.config_article_base_url
        elif generic_env_base_url:
            self.llm_base_url = generic_env_base_url or self.llm_base_url

        if article_env_api_key:
            self.llm_api_key = article_env_api_key
        elif self.config_article_api_key:
            self.llm_api_key = self.config_article_api_key
        elif generic_env_api_key:
            self.llm_api_key = generic_env_api_key or self.llm_api_key

        article_env_model = os.getenv("ARTICLE_LLM_MODEL", "").strip()
        generic_env_model = os.getenv("LLM_MODEL", "").strip()
        if article_env_model:
            self.llm_model = article_env_model
        elif self.config_article_model:
            self.llm_model = self.config_article_model
        elif generic_env_model:
            self.llm_model = generic_env_model
        self._load_llm_from_openclaw_if_needed()
        # 按项目约定统一本地生成模型，避免与其它模块模型不一致。
        self.llm_model = self.LOCAL_GENERATION_MODEL

    def _load_project_llm_config(self):
        config_file = self.project_root / "llm_config.json"
        if not config_file.exists():
            return
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.llm_base_url = str(data.get("base_url", "")).strip()
            self.llm_api_key = str(data.get("api_key", "")).strip()
            self.config_article_base_url = str(data.get("article_base_url", "")).strip()
            self.config_article_api_key = str(data.get("article_api_key", "")).strip()
            self.config_article_model = str(data.get("article_model", "")).strip()
            self.llm_model = str(data.get("article_model", data.get("model", self.llm_model))).strip() or self.llm_model
            self.available_models = data.get("available_models", {})
        except Exception:
            return

    def _candidate_config_paths(self):
        cwd = Path.cwd()
        return [
            Path.home() / ".openclaw" / "gateway" / "config.json",
            Path.home() / ".openclaw" / "config.json",
            Path.home() / ".openclaw" / "openclaw.json",
            cwd / ".openclaw" / "gateway" / "config.json",
            cwd / ".openclaw" / "config.json",
            cwd / ".openclaw" / "openclaw.json",
            Path(__file__).resolve().parents[1] / ".openclaw" / "gateway" / "config.json",
        ]

    def _load_json_file(self, config_path: Path):
        for encoding in ("utf-8", "utf-8-sig"):
            try:
                with open(config_path, "r", encoding=encoding) as f:
                    return json.load(f)
            except Exception:
                continue
        raise ValueError(f"Unable to parse JSON config: {config_path}")

    def _load_tavily_api_key(self):
        for config_path in self._candidate_config_paths():
            if not config_path.exists():
                continue

            try:
                config = self._load_json_file(config_path)

                plugins = config.get("plugins", {}).get("entries", {})
                for plugin_name, plugin in plugins.items():
                    is_tavily = plugin_name == "tavily" or plugin.get("kind") == "tavily"
                    if not is_tavily:
                        continue
                    cfg = plugin.get("config", {})
                    if "webSearch" in cfg:
                        return cfg["webSearch"].get("apiKey")
                    return cfg.get("apiKey")
            except Exception:
                continue

        return None

    def _load_llm_from_openclaw_if_needed(self):
        """未设置环境变量时，从 openclaw.json 中读取默认 provider。"""
        if self.llm_base_url and self.llm_api_key:
            return

        for config_path in self._candidate_config_paths():
            if not config_path.exists():
                continue
            try:
                config = self._load_json_file(config_path)

                providers = config.get("models", {}).get("providers", {})
                if not providers:
                    continue

                # 优先选包含 sjtu 的 provider，否则取第一个。
                provider = None
                for provider_name, provider_cfg in providers.items():
                    if "sjtu" in provider_name.lower():
                        provider = provider_cfg
                        break
                if provider is None:
                    provider = next(iter(providers.values()))

                if not self.llm_base_url:
                    self.llm_base_url = str(provider.get("baseUrl", "")).strip()
                if not self.llm_api_key:
                    self.llm_api_key = str(provider.get("apiKey", "")).strip()

                if not self.llm_model:
                    model_items = provider.get("models", [])
                    if model_items and isinstance(model_items, list):
                        first_model = model_items[0].get("id")
                        if first_model:
                            self.llm_model = str(first_model).strip()
                return
            except Exception:
                continue

    def _build_chat_completions_url(self):
        base = self.llm_base_url.strip()
        if not base:
            return ""
        base = base.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def _build_llm_payload(self, messages, temperature, max_tokens):
        payload = {
            "model": self.llm_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.llm_model.lower().startswith("qwen") or "dashscope.aliyuncs.com" in self.llm_base_url:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _count_words(self, text):
        return len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or ""))

    def _resolve_embedded_words(self, content, words):
        content = (content or "").lower()
        embedded = []
        for word in words:
            # 允许常见英语词形变化后缀（ed/ing/s/ly/tion 等）
            if re.search(rf"\b{re.escape(word.lower())}\w{{0,5}}\b", content):
                embedded.append(word)
        return embedded

    def _normalize_questions(self, questions):
        normalized = []
        for item in questions or []:
            if isinstance(item, str):
                normalized.append({"question": item, "type": "detail"})
            elif isinstance(item, dict):
                question_text = item.get("question_text") or item.get("question")
                if question_text:
                    normalized.append({
                        "question": question_text,
                        "type": item.get("type", "detail"),
                    })
        while len(normalized) < 5:
            default_types = ["main_idea", "vocabulary_in_context", "inference", "detail", "rhetorical_purpose"]
            fallback_type = default_types[len(normalized)] if len(normalized) < len(default_types) else "detail"
            normalized.append({"question": "What is the main point of the passage?", "type": fallback_type})
        return normalized[:5]

    def _finalize_article(self, parsed, words):
        parsed["questions"] = self._normalize_questions(parsed.get("questions", []))
        parsed["embedded_words"] = self._resolve_embedded_words(parsed.get("content", ""), words)
        return parsed

    def _meets_article_targets(self, parsed, words, min_words=None, min_embedded_words=None):
        min_words = min_words or self.TARGET_MIN_WORDS
        min_embedded_words = min(min_embedded_words or self.TARGET_MIN_EMBEDDED_WORDS, len(words))
        return (
            self._count_words(parsed.get("content", "")) >= min_words
            and len(parsed.get("embedded_words", [])) >= min_embedded_words
        )

    # ---- full-article LLM rewrite ----

    def _single_shot_rewrite(self, fallback_content, words, source_material=None, topic=None):
        """一次性将整篇模板文章发给 LLM 改写，嵌入生词。比逐段更快。"""
        url = self._build_chat_completions_url()
        if not url or not self.llm_api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        topic_str = (topic or "scientific research").replace("-", " ")
        source_hint = ""
        if source_material:
            source_hint = (
                "\nUse this real-world context to enrich the rewrite:\n"
                f"{source_material[:1200]}\n"
            )

        prompt = (
            "Rewrite the following academic article to naturally embed ALL of these vocabulary words.\n\n"
            f"VOCABULARY WORDS (you MUST use every one): {', '.join(words)}\n\n"
            f"Topic: {topic_str}\n"
            f"{source_hint}"
            f"\nORIGINAL ARTICLE:\n{fallback_content}\n\n"
            "Rules:\n"
            "- Keep the article AT LEAST as long as the original (every paragraph must be preserved)\n"
            "- Maintain academic TOEFL-style tone\n"
            "- Replace generic phrases with the vocabulary words where they fit naturally\n"
            "- Do NOT shorten or remove any paragraphs\n"
            "- Do NOT add definitions or translations\n"
            "- Return ONLY the rewritten article text"
        )

        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": "Rewrite the article. Return ONLY the rewritten text, nothing else."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.5,
            "max_tokens": 4000,
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=90)
            if resp.status_code != 200:
                logger.warning("Single-shot rewrite LLM returned %d", resp.status_code)
                return None
            text = resp.json()["choices"][0]["message"]["content"].strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            text = re.sub(r"^```.*?\n", "", text).rstrip("`").strip()
            wc = self._count_words(text)
            original_wc = self._count_words(fallback_content)
            logger.info("Single-shot rewrite: %d words (original %d)", wc, original_wc)
            if wc >= original_wc * 0.6:
                return text
            logger.warning("Single-shot rewrite too short: %d < %d*0.6", wc, original_wc)
            return None
        except Exception as exc:
            logger.warning("Single-shot rewrite failed: %s", exc)
            return None

    # ---- paragraph-by-paragraph LLM rewrite ----

    def _parallel_rewrite_paragraphs(self, paragraphs, words, source_material=None, topic=None):
        """逐段并行调用 LLM 改写，将生词自然嵌入每段文字。"""
        url = self._build_chat_completions_url()
        if not url or not self.llm_api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        # 给每段分配重点词（必须用）+ 全量词表（尽量用）
        n = len(paragraphs)
        focus_per_para = [[] for _ in range(n)]
        for i, w in enumerate(words):
            focus_per_para[i % n].append(w)

        all_words_str = ", ".join(words)
        topic_str = (topic or "scientific research and academic analysis").replace("-", " ")
        source_hint = ""
        if source_material:
            source_hint = (
                "\nUse the following real-world context to make the paragraph more specific and factual:\n"
                f"{source_material[:800]}\n"
            )

        def _rewrite_one(paragraph, focus_words, timeout=None):
            if not focus_words:
                return paragraph
            _timeout = timeout or self.PARAGRAPH_REWRITE_TIMEOUT
            prompt = (
                "Rewrite this academic paragraph.\n"
                f"REQUIRED words (you MUST use ALL of them): {', '.join(focus_words)}\n"
                f"BONUS words (use as many as you naturally can): {all_words_str}\n\n"
                f"Topic context: {topic_str}\n"
                f"{source_hint}"
                f"ORIGINAL PARAGRAPH:\n{paragraph}\n\n"
                "Rules:\n"
                "- Keep roughly the same length or slightly longer\n"
                "- Academic TOEFL-style tone\n"
                "- CRITICAL: Every REQUIRED word MUST appear at LEAST ONCE\n"
                "- Also include as many BONUS words as naturally fit\n"
                "- Do NOT add definitions or translations\n"
                "- Return ONLY the rewritten paragraph text, nothing else"
            )
            payload = {
                "model": self.llm_model,
                "messages": [
                    {"role": "system", "content": "Rewrite the paragraph. Return only the rewritten text."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.5,
                "max_tokens": self.PARAGRAPH_REWRITE_MAX_TOKENS,
            }
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=_timeout)
                if resp.status_code != 200:
                    logger.warning("Paragraph rewrite LLM returned %d", resp.status_code)
                    return paragraph
                raw = resp.json().get("choices", [{}])[0].get("message", {}).get("content")
                if not raw:
                    return paragraph
                text = raw.strip()
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
                text = re.sub(r"^```.*?\n", "", text).rstrip("`").strip()
                # 基本检查：改写后长度不应缩水太多
                if len(text.split()) >= len(paragraph.split()) * 0.5:
                    return text
                return paragraph
            except Exception as exc:
                logger.warning("Paragraph rewrite failed: %s", exc)
                return paragraph

        results = [None] * n
        workers = min(self.PARAGRAPH_REWRITE_WORKERS, n)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for i, (para, focus) in enumerate(zip(paragraphs, focus_per_para)):
                futures[pool.submit(_rewrite_one, para, focus, self.PARAGRAPH_REWRITE_TIMEOUT)] = i
            for future in concurrent.futures.as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception:
                    results[idx] = paragraphs[idx]

        # 对失败的段逐个重试一次（并行批次结束后服务器压力小）
        failed = [i for i in range(n) if results[i] == paragraphs[i] and focus_per_para[i]]
        if failed:
            logger.info("Retrying %d failed paragraphs sequentially", len(failed))
            for idx in failed:
                results[idx] = _rewrite_one(paragraphs[idx], focus_per_para[idx], self.PARAGRAPH_RETRY_TIMEOUT)

        # 检查有多少段被成功改写
        rewritten_count = sum(1 for i in range(n) if results[i] != paragraphs[i])
        logger.info("Paragraph rewrite: %d/%d paragraphs rewritten by LLM", rewritten_count, n)
        if rewritten_count == 0:
            return None
        return results

    def _generate_with_paragraph_rewrite(self, words):
        """核心管线：本地模板 → 逐段并行 LLM 改写嵌入生词。"""
        # Step 1: 尝试获取 Tavily 素材作为改写上下文
        source_material = None
        topic = None
        references = []
        cache_id = None

        if self.api_key:
            search_result = self._search_tavily_sources(words)
            if search_result and len(search_result.get("material", "")) >= 100:
                source_material = search_result["material"]
                topic = search_result.get("topic")
                references = search_result.get("references", [])
                cache_id = search_result.get("cache_id")

        # Step 2: 生成本地模板长文（保证字数）
        fallback = self._generate_fallback(words, topic=topic, references=references)

        # Step 3: 逐段并行 LLM 改写（每段 ~130 词，LLM 容易处理）
        paragraphs = [p.strip() for p in fallback["content"].split("\n\n") if p.strip()]
        rewritten = self._parallel_rewrite_paragraphs(paragraphs, words, source_material, topic)

        if rewritten:
            content = "\n\n".join(rewritten)
            result = {
                "title": fallback["title"],
                "content": content,
                "questions": fallback["questions"],
                "references": references,
                "cache_id": cache_id,
                "source_topic": topic,
            }
            result = self._finalize_article(result, words)
            wc = self._count_words(result.get("content", ""))
            emb_count = len(result.get("embedded_words", []))
            logger.info("Rewritten article: %d words, %d/%d embedded (need %d words, %d embedded)",
                        wc, emb_count, len(words), self.TARGET_MIN_WORDS, self.TARGET_MIN_EMBEDDED_WORDS)
            # LLM 改写内容质量高，嵌入词门槛可以适当放宽
            if self._meets_article_targets(result, words, min_embedded_words=12):
                src = ("tavily+" if source_material else "") + "llm-rewrite"
                result["source"] = src
                return result

        # LLM 改写失败，返回原始模板
        fallback["references"] = references
        fallback["cache_id"] = cache_id
        fallback["source_topic"] = topic
        fallback["source"] = "tavily+fallback" if source_material else "fallback"
        return fallback

    def _extract_json(self, raw_text):
        text = raw_text.strip()

        # 兼容部分模型输出的思考块和 markdown 包裹
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except Exception:
            pass

        # 兜底：提取首个 JSON 对象
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])

        raise ValueError("No JSON found in model response")

    def _load_source_cache(self):
        if not self.source_cache_file.exists():
            return []
        try:
            payload = json.loads(self.source_cache_file.read_text(encoding="utf-8"))
            entries = payload.get("entries", [])
            return [entry for entry in entries if entry.get("material")]
        except Exception:
            return []

    def _save_source_cache(self, entries):
        trimmed = sorted(entries, key=lambda item: (item.get("used_count", 0), item.get("created_at", "")))
        payload = {
            "updated_at": datetime.now().isoformat(),
            "entries": trimmed[: self.SOURCE_CACHE_MAX_ITEMS],
        }
        self.source_cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _fetch_tavily_bundle(self, topic, words):
        """请求 Tavily 并构造一条可缓存的素材包。"""
        if not self.api_key:
            return None

        query_tail = " ".join(words[:2]) if words else ""
        query = f"{topic} {query_tail} academic research overview".strip()

        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 5,
                    "include_domains": [
                        "scientificamerican.com",
                        "nationalgeographic.com",
                        "nature.com",
                        "sciencedaily.com",
                        "newscientist.com",
                        "smithsonianmag.com",
                        "bbc.com/news/science",
                        "phys.org",
                    ],
                },
                timeout=20,
            )
            if response.status_code != 200:
                logger.warning("Tavily search returned %d", response.status_code)
                return None

            payload = response.json()
            results = payload.get("results", [])
            snippets = []
            references = []
            for item in results:
                content = item.get("content", "").strip()
                if content and len(content) > 80:
                    snippets.append(content)
                    references.append(
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "domain": item.get("url", "").split("/")[2] if item.get("url") else "",
                        }
                    )

            combined = "\n\n".join(snippets[:4])
            logger.info("Tavily returned %d snippets, %d chars total", len(snippets), len(combined))
            if not combined:
                return None
            return {
                "cache_id": f"{topic}-{abs(hash(combined[:120]))}",
                "topic": topic,
                "query": query,
                "material": combined[:3000],
                "references": references[:4],
                "seed_words": words[:4],
                "used_count": 0,
                "created_at": datetime.now().isoformat(),
                "last_used_at": None,
            }
        except Exception as e:
            logger.warning("Tavily search failed: %s", e)
            return None

    def _prefetch_source_cache(self, words):
        entries = self._load_source_cache()

        topics = self.ACADEMIC_TOPICS[:]
        random.shuffle(topics)
        # 冷启动时批量预抓取，常态下每次少量补货，保证缓存持续更新。
        if len(entries) < self.SOURCE_CACHE_MIN_ITEMS:
            need = max(self.SOURCE_CACHE_MIN_ITEMS - len(entries), self.SOURCE_CACHE_PREFETCH_BATCH)
        else:
            remaining = max(self.SOURCE_CACHE_MAX_ITEMS - len(entries), 0)
            need = min(2, remaining)

        if need <= 0:
            return entries

        seen_topics = {entry.get("topic") for entry in entries}
        seen_cache_ids = {entry.get("cache_id") for entry in entries if entry.get("cache_id")}

        for topic in topics:
            if need <= 0:
                break
            # 冷启动优先补齐不同 topic，常态补货可重复 topic 但不能重复同一 cache_id。
            if len(entries) < self.SOURCE_CACHE_MIN_ITEMS and topic in seen_topics:
                continue
            bundle = self._fetch_tavily_bundle(topic, words)
            if bundle:
                cache_id = bundle.get("cache_id")
                if cache_id and cache_id in seen_cache_ids:
                    continue
                entries.append(bundle)
                seen_topics.add(topic)
                if cache_id:
                    seen_cache_ids.add(cache_id)
                need -= 1

        if entries:
            self._save_source_cache(entries)
        return entries

    def _search_tavily_sources(self, words):
        """优先从本地缓存取学术素材；缓存不足时批量预抓取。"""
        entries = self._prefetch_source_cache(words)
        if self._active_topic_filter:
            filtered = [entry for entry in entries if entry.get("topic") in self._active_topic_filter]
            if filtered:
                entries = filtered
        if not entries:
            return None

        def _score(entry):
            overlap = len(set(words) & set(entry.get("seed_words", [])))
            last_used = entry.get("last_used_at") or ""
            return (entry.get("used_count", 0), -overlap, last_used)

        selected = min(entries, key=_score)
        selected["used_count"] = int(selected.get("used_count", 0)) + 1
        selected["last_used_at"] = datetime.now().isoformat()
        # 淘汰用过多次的缓存条目，强制刷新
        entries = [e for e in entries if e.get("used_count", 0) < self.SOURCE_CACHE_MAX_USES
                   or e.get("cache_id") == selected.get("cache_id")]
        self._save_source_cache(entries)
        return {
            "cache_id": selected.get("cache_id"),
            "topic": selected.get("topic"),
            "material": selected.get("material", ""),
            "references": selected.get("references", []),
        }

    async def generate_article_with_words(self, words, topic_hint: str | None = None):
        # 根据主题提示限制 ACADEMIC_TOPICS 子集（影响 Tavily 预抓取与本地模板主题）。
        original_topics = self.ACADEMIC_TOPICS
        original_filter = self._active_topic_filter
        fallback_topic = None
        if topic_hint:
            subset = self.TOPIC_TO_ACADEMIC.get(topic_hint.strip().lower())
            if subset:
                self.ACADEMIC_TOPICS = subset
                self._active_topic_filter = set(subset)
                fallback_topic = subset[0]
        try:
            # 主策略：本地模板 + 逐段并行 LLM 改写（保证字数 + 定制化生词嵌入）
            if self.llm_base_url and self.llm_api_key:
                result = self._generate_with_paragraph_rewrite(words)
                if result:
                    if topic_hint:
                        result["topic_hint"] = topic_hint
                    if fallback_topic and not result.get("source_topic"):
                        result["source_topic"] = fallback_topic
                    return result

            # 兜底：纯本地模板
            fallback = self._generate_fallback(words, topic=fallback_topic)
            fallback["source"] = "fallback"
            if topic_hint:
                fallback["topic_hint"] = topic_hint
            return fallback
        finally:
            self.ACADEMIC_TOPICS = original_topics
            self._active_topic_filter = original_filter

    def _generate_tavily_enhanced(self, words):
        """核心：Tavily 搜索真实学术素材 → LLM 基于素材改写并嵌入生词。"""
        search_result = self._search_tavily_sources(words)
        if not search_result or len(search_result["material"]) < 100:
            return self._generate_via_llm(words)
        source_material = search_result["material"]

        url = self._build_chat_completions_url()
        if not url:
            return None

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        prompt = (
            "You are an expert TOEFL reading passage writer. Your task:\n\n"
            "1. Read the SOURCE MATERIAL below (real academic article excerpts).\n"
            "2. Write a TOEFL iBT-style reading passage based on it. "
            "The passage should read like it comes from Scientific American, Nature, or National Geographic.\n"
            f"3. The passage MUST be {self.TARGET_MIN_WORDS}-{self.TARGET_MAX_WORDS} words long.\\n"
            "4. Naturally embed ALL of these TARGET VOCABULARY WORDS into the passage. "
            "Replace synonyms in the source material with the target words where they fit naturally. "
            "Do NOT force words into unnatural positions.\n"
            "5. Structure: Use 4-5 paragraphs. Include an introduction, body with evidence/examples, and a conclusion.\n"
            "6. Write exactly 5 TOEFL-style comprehension questions:\n"
            "   - 1 main idea question\n"
            "   - 1 vocabulary-in-context question\n"
            "   - 1 inference question\n"
            "   - 1 detail question\n"
            "   - 1 rhetorical purpose question\n\n"
            f"TARGET VOCABULARY WORDS: {', '.join(words)}\n\n"
            f"SOURCE MATERIAL:\n{source_material}\n\n"
            "Return strict JSON with keys: title, content, questions, embedded_words.\n"
            "- title: a clear academic title\n"
            "- content: the full passage text (780-950 words)\n"
            "- questions: array of 5 objects, each with 'question' and 'type' keys\n"
            "- embedded_words: array of target words that were successfully embedded"
        )

        data = self._build_llm_payload(
            messages=[
                {"role": "system", "content": "You are a TOEFL reading passage generator. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=2600,
        )

        parsed = self._request_llm_json(url, headers, data)
        if parsed:
            parsed["references"] = search_result["references"]
            parsed["cache_id"] = search_result.get("cache_id")
            parsed["source_topic"] = search_result.get("topic")
            parsed = self._finalize_article(parsed, words)
            parsed = self._expand_short_article(parsed, words, min_words=self.TARGET_MIN_WORDS)
            parsed = self._finalize_article(parsed, words)
            if self._meets_article_targets(parsed, words):
                parsed["source"] = "tavily+llm"
                return parsed

        logger.warning("Tavily-enhanced generation returned short content, falling back to local long article")
        fallback = self._generate_fallback(words, topic=search_result.get("topic"), references=search_result.get("references", []))
        fallback["references"] = search_result.get("references", [])
        fallback["cache_id"] = search_result.get("cache_id")
        fallback["source_topic"] = search_result.get("topic")
        fallback["source"] = "tavily+fallback"
        return fallback

    def _generate_via_llm(self, words):
        """纯 LLM 生成（无搜索素材时使用）。"""
        if not (self.llm_base_url and self.llm_api_key and self.llm_model):
            return None

        url = self._build_chat_completions_url()
        if not url:
            return None

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }

        prompt = (
            "You are an expert TOEFL reading passage writer.\n\n"
            "Write a TOEFL iBT-style reading passage with the following requirements:\n"
            f"1. Length: {self.TARGET_MIN_WORDS}-{self.TARGET_MAX_WORDS} words.\\n"
            "2. Style: Academic, similar to Scientific American or National Geographic.\n"
            "3. Structure: 4-5 paragraphs with clear topic sentences.\n"
            "4. Naturally embed ALL target words if possible, but at minimum embed 20 of them naturally: "
            f"{', '.join(words)}.\n"
            "5. Write exactly 5 comprehension questions (main idea, vocabulary, inference, detail, rhetorical purpose).\n\n"
            "Return strict JSON with keys: title, content, questions, embedded_words.\n"
            "- questions: array of 5 objects with 'question' and 'type' keys\n"
            "- embedded_words: array of successfully embedded target words"
        )

        data = self._build_llm_payload(
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=2600,
        )

        parsed = self._request_llm_json(url, headers, data)
        if parsed:
            parsed = self._finalize_article(parsed, words)
            parsed = self._expand_short_article(parsed, words, min_words=self.TARGET_MIN_WORDS)
            parsed = self._finalize_article(parsed, words)
            if self._meets_article_targets(parsed, words):
                parsed["source"] = "llm"
                return parsed

        # 重试：保留完整词表，但明确要求补足长度与词汇覆盖。
        retry_data = self._build_llm_payload(
            messages=[
                {"role": "system", "content": "You must return only valid JSON."},
                {"role": "user", "content": (
                    f"Rewrite a {self.TARGET_MIN_WORDS}-{self.TARGET_MAX_WORDS} word academic reading passage. "
                    f"Naturally include at least 20 of these target words: {', '.join(words)}. "
                    "Return JSON with keys: title, content, questions, embedded_words."
                )},
            ],
            temperature=0.3,
            max_tokens=2400,
        )
        parsed = self._request_llm_json(url, headers, retry_data)
        if parsed:
            parsed = self._finalize_article(parsed, words)
            parsed = self._expand_short_article(parsed, words, min_words=self.TARGET_MIN_WORDS)
            parsed = self._finalize_article(parsed, words)
            if self._meets_article_targets(parsed, words):
                parsed["source"] = "llm"
                return parsed
        return None

    def _expand_short_article(self, parsed, words, min_words=760):
        content = parsed.get("content", "")
        if self._count_words(content) >= min_words and len(parsed.get("embedded_words", [])) >= min(self.TARGET_MIN_EMBEDDED_WORDS, len(words)):
            return parsed
        if not (self.llm_base_url and self.llm_api_key and self.llm_model):
            return parsed

        url = self._build_chat_completions_url()
        if not url:
            return parsed

        headers = {
            "Authorization": f"Bearer {self.llm_api_key}",
            "Content-Type": "application/json",
        }
        prompt = (
            f"Expand the following TOEFL passage to {self.TARGET_MIN_WORDS}-{self.TARGET_MAX_WORDS} words while preserving its topic and improving academic depth. "
            "Keep the existing vocabulary words where possible, naturally insert any missing target words, and make the structure 4-5 paragraphs. "
            "Also ensure there are exactly 5 comprehension questions with question and type keys.\n\n"
            f"Target words: {', '.join(words)}\n\n"
            f"Current title: {parsed.get('title', '')}\n\n"
            f"Current passage:\n{content}\n\n"
            "Return strict JSON with keys: title, content, questions, embedded_words."
        )
        data = self._build_llm_payload(
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=2600,
        )
        expanded = self._request_llm_json(url, headers, data)
        if expanded and self._count_words(expanded.get("content", "")) > self._count_words(content):
            expanded = self._finalize_article(expanded, words)
            if parsed.get("references"):
                expanded["references"] = parsed["references"]
            if parsed.get("cache_id"):
                expanded["cache_id"] = parsed["cache_id"]
            if parsed.get("source_topic"):
                expanded["source_topic"] = parsed["source_topic"]
            return expanded
        return parsed

    def _request_llm_json(self, url, headers, data):
        try:
            response = requests.post(url, headers=headers, json=data, timeout=self.llm_timeout_seconds)
            if response.status_code != 200:
                return None
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            parsed = self._extract_json(content)
            if not parsed.get("title") or not parsed.get("content"):
                return None
            return parsed
        except Exception:
            return None

    def _generate_fallback(self, words, topic=None, references=None):
        references = references or []
        normalized_topic = (topic or "scientific change and evidence").replace("-", " ").strip()
        clean_words = words[:] if words else [
            "cognitive", "paradigm", "sustainable", "ambiguous", "substantiate", "ubiquitous"
        ]

        # 增加轻量随机扰动，避免同一 topic+词表反复生成完全相同的兜底文章。
        variant_seed = sum(ord(ch) for ch in normalized_topic) + len(clean_words) + random.randint(0, 997)

        def pick(options, offset=0):
            return options[(variant_seed + offset) % len(options)]

        clusters = [clean_words[index:index + 4] for index in range(0, len(clean_words), 4)]
        while len(clusters) < 6:
            clusters.append([])

        rotation = variant_seed % len(clusters) if clusters else 0
        clusters = clusters[rotation:] + clusters[:rotation]

        def cluster_text(index):
            cluster = clusters[index] if index < len(clusters) else []
            return ", ".join(cluster) if cluster else "empirical, cumulative, analytical, adaptive"

        source_names = ", ".join(
            ref.get("domain") or ref.get("title", "") for ref in references[:3] if ref.get("domain") or ref.get("title")
        ) or "recent academic and science publications"

        intro_openers = [
            "Scholars who study",
            "Researchers investigating",
            "Academic discussions of",
        ]
        transition_openers = [
            "One reason",
            "A central reason",
            "An important reason",
        ]
        methodology_openers = [
            "Methodology is equally important.",
            "Research design is just as important.",
            "The methodological issue is equally important.",
        ]
        institution_openers = [
            "Institutional context also shapes the interpretation of",
            "The institutional setting also influences how scholars interpret",
            "Academic institutions also affect the interpretation of",
        ]
        closing_openers = [
            "Ultimately, the study of",
            "In the end, the study of",
            "Taken together, the study of",
        ]

        paragraphs = [
            (
                f"{pick(intro_openers)} {normalized_topic} often begins from a simple observation: large-scale change rarely appears all at once. "
                f"Instead, it becomes visible only when researchers compare many small signals across long stretches of time. "
                f"In current discussions of this field, the evidence base is {cluster_text(0)}, because specialists must connect environmental patterns, historical records, and modern measurement techniques before they can draw careful conclusions. "
                f"A TOEFL reader approaching such a passage must therefore do more than memorize definitions. That reader must follow how an author frames a problem, qualifies a claim, and then uses concrete observations to substantiate a broader interpretation. "
                f"This kind of structured reading is especially useful when the subject involves systems that look stable on the surface but are, in fact, constantly adjusting beneath that appearance."
            ),
            (
                f"{pick(transition_openers, 1)} {normalized_topic} continues to attract public attention is that it forces scientists to work across multiple scales. "
                f"Some questions are local and immediate, such as whether a small shift in temperature, pressure, or resource use changes what researchers can observe in a given season. "
                f"Other questions are historical and comparative. In those cases, the analysis becomes {cluster_text(1)}, because scholars must ask whether a pattern is temporary, cyclical, or part of a much deeper transformation. "
                f"The most persuasive articles do not jump directly to dramatic conclusions. They first establish a baseline, then explain what counts as reliable evidence, and finally show why alternative explanations may be incomplete. "
                f"That sequence matters because academic readers are trained to value careful reasoning more than rhetorical excitement."
            ),
            (
                f"{pick(methodology_openers, 2)} In many disciplines, a single instrument rarely settles a debate. Researchers compare field observations with laboratory data, historical archives, satellite records, or statistical reconstructions. "
                f"As a result, the final interpretation often appears {cluster_text(2)} rather than linear, because each new layer of evidence clarifies one issue while complicating another. "
                f"This is precisely why strong academic prose tends to acknowledge uncertainty without collapsing into indecision. A competent author will identify what is known, what remains disputed, and what further research would need to measure before a stronger claim becomes defensible. "
                f"For students preparing for standardized reading tasks, this kind of paragraph is valuable practice because it trains them to distinguish between primary evidence, tentative inference, and final conclusion."
            ),
            (
                f"{pick(institution_openers, 3)} {normalized_topic}. Universities, museums, laboratories, and policy organizations rarely ask identical questions, even when they examine the same body of evidence. "
                f"One group may focus on mechanism, another on long-term consequence, and a third on how findings should be communicated to the public. "
                f"Under those circumstances, vocabulary such as {cluster_text(3)} becomes useful not as decoration, but as a concise way to distinguish subtle intellectual positions. "
                f"A reader who recognizes these distinctions can see that disagreement in academic writing is often productive rather than destructive. It signals that researchers are testing the limits of an explanation and refining the terms under which it remains persuasive."
            ),
            (
                f"Another challenge lies in translating specialist knowledge for broader audiences. Articles from {source_names} often succeed because they preserve analytical precision while still offering a readable narrative. "
                f"They may begin with an example, return to a major principle, and then use one carefully chosen comparison to show why the issue matters beyond the laboratory or archive. "
                f"In that communicative setting, a vocabulary set such as {cluster_text(4)} helps readers trace cause and effect, contrast old assumptions with newer findings, and infer how apparently minor observations can accumulate into a major revision of accepted knowledge. "
                f"This is one reason advanced vocabulary should be learned in context. When a word is tied to a real argument, its meaning becomes more precise and easier to recall later."
            ),
            (
                f"{pick(closing_openers, 4)} {normalized_topic} demonstrates why patient reading remains indispensable for academic growth. "
                f"A strong passage does not merely present facts; it organizes them into a hierarchy of claims, examples, and implications. "
                f"By the end of such a discussion, readers should understand not only the topic itself but also the method by which scholars move from observation to explanation. "
                f"That is where the final vocabulary cluster, {cluster_text(5)}, becomes most useful: it gives students a language for describing evidence, complexity, interpretation, and consequence with greater accuracy. "
                f"When learners repeatedly encounter these terms inside coherent topic-based articles, they become faster at inference, more alert to rhetorical structure, and more capable of writing their own academic summaries with confidence."
            ),
            (
                f"There is also a practical payoff. Once a learner has read several passages on related subjects, recurring ideas stop feeling isolated. "
                f"The same analytic habits that help explain {normalized_topic} can later be applied to biology, economics, archaeology, or environmental policy. "
                f"In that sense, reading practice is cumulative: each article enlarges both topic knowledge and the student’s ability to track how sophisticated arguments are built. "
                f"Over time, this makes difficult texts less intimidating and turns advanced vocabulary from a memorization burden into a usable intellectual tool. "
                f"When a student can explain why a claim is persuasive, identify the evidence that supports it, and restate its implications in precise academic language, that student has moved beyond surface comprehension into genuine analytical reading."
            ),
            (
                f"For that reason, efficient preparation should combine repeated exposure with strategic selection. Not every unfamiliar term deserves equal attention in every session. "
                f"Some words should be revisited because they were noticed but not yet mastered; others should be introduced because they expand the learner’s range in describing mechanism, sequence, evaluation, or consequence. "
                f"A long passage on {normalized_topic} is therefore useful for more than simple memorization. It provides a setting in which concepts, evidence, and vocabulary reinforce one another. "
                f"This is precisely the kind of integrated practice that improves both retention and test performance over time."
            ),
            (
                f"Seen from that perspective, advanced reading is not a matter of passively receiving information. It is an active process of classification, comparison, and revision. "
                f"Readers must decide which evidence is central, which details are illustrative, and which phrases signal the author’s true stance. "
                f"As they repeat that process across longer passages, the intellectual habits of academic English become easier to internalize. "
                f"That is why a well-designed article routine, especially one anchored in topics like {normalized_topic}, remains one of the quickest and most durable ways to build high-level TOEFL vocabulary in context."
            ),
        ]
        content = "\n\n".join(paragraphs)
        questions = [
            {"question": f"What is the main idea of the passage about {normalized_topic}?", "type": "main_idea"},
            {"question": "According to the passage, why do researchers compare multiple kinds of evidence?", "type": "detail"},
            {"question": "What can be inferred about academic disagreement in this field?", "type": "inference"},
            {"question": "Why does the author discuss institutions and public communication?", "type": "rhetorical_purpose"},
            {"question": "In the passage, what does the use of advanced vocabulary suggest about academic reading?", "type": "vocabulary_in_context"},
        ]

        return {
            "title": f"Academic Reading on {normalized_topic.title()}",
            "content": content,
            "questions": questions,
            "embedded_words": clean_words,
            "source": "fallback",
        }
