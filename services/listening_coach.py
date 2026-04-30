#!/usr/bin/env python3
"""TOEFL 听力教练（2026 任务类型模板）：
- Listen and Choose a Response
- Listen to a Conversation
- Listen to an Announcement
- Listen to an Academic Talk

能力点：Tavily + 本地缓存池 + 不重复抽样 + TTS 自动播放指令 + 评分与错题解析。
"""
from __future__ import annotations

import concurrent.futures
import json
import random
import re
from datetime import datetime
from pathlib import Path
from threading import Lock

import requests

from database import SessionLocal
from models import StudyEvent
from services.llm_client import chat_json, is_configured


project_root = Path(__file__).resolve().parent.parent
listening_state_file = project_root / "data" / "listening_state.json"
listening_source_cache_file = project_root / "data" / "listening_source_cache.json"
listening_passage_cache_file = project_root / "data" / "listening_passage_cache.json"


SOURCE_CACHE_MIN_ITEMS = 6
SOURCE_CACHE_PREFETCH_BATCH = 2
SOURCE_CACHE_MAX_ITEMS = 40
SOURCE_CACHE_MAX_USES = 2
PASSAGE_CACHE_MIN_ITEMS = 10
PASSAGE_CACHE_PREFETCH_BATCH = 1
PASSAGE_CACHE_MAX_ITEMS = 120
PASSAGE_CACHE_MAX_USES = 3


_source_cache_lock = Lock()
_passage_cache_lock = Lock()


LISTENING_TASK_SPECS = {
    "choose_response": {
        "label": "Listen and Choose a Response",
        "label_cn": "听句子选回应",
        "target_words": "12-28 words",
        "word_min": 12,
        "word_max": 30,
        "question_count": 1,
        "focus": ["meaning", "intent", "spoken-response pattern"],
        "q_types": ["response_selection"],
        "description": "听短促口语提示，选最合适回应。",
    },
    "conversation": {
        "label": "Listen to a Conversation",
        "label_cn": "听校园对话",
        "target_words": "150-230 words",
        "word_min": 150,
        "word_max": 240,
        "question_count": 5,
        "focus": ["main idea", "detail", "speaker intent", "inference"],
        "q_types": ["main_idea", "detail", "detail", "function", "inference"],
        "description": "校园语境对话，考主旨、细节、话语功能、推断。",
    },
    "announcement": {
        "label": "Listen to an Announcement",
        "label_cn": "听校园公告",
        "target_words": "110-180 words",
        "word_min": 110,
        "word_max": 190,
        "question_count": 4,
        "focus": ["purpose", "key info", "implied meaning", "next action"],
        "q_types": ["purpose", "detail", "inference", "next_step"],
        "description": "公告类输入，考目的、关键信息、隐含意义与后续动作。",
    },
    "academic_talk": {
        "label": "Listen to an Academic Talk",
        "label_cn": "听学术讲解",
        "target_words": "230-340 words",
        "word_min": 220,
        "word_max": 360,
        "question_count": 6,
        "focus": ["main idea", "supporting detail", "organization", "inference", "vocab/function"],
        "q_types": ["main_idea", "detail", "detail", "organization", "function", "inference"],
        "description": "学术讲解类输入，考结构追踪与信息整合。",
    },
}

LISTENING_TASK_ALIASES = {
    "choose": "choose_response",
    "choose_response": "choose_response",
    "chooseresponse": "choose_response",
    "response": "choose_response",
    "听句子选回应": "choose_response",
    "选回应": "choose_response",
    "conversation": "conversation",
    "conv": "conversation",
    "对话": "conversation",
    "校园对话": "conversation",
    "announcement": "announcement",
    "announce": "announcement",
    "公告": "announcement",
    "campus_announcement": "announcement",
    "academic_talk": "academic_talk",
    "academictalk": "academic_talk",
    "talk": "academic_talk",
    "lecture": "academic_talk",
    "学术讲解": "academic_talk",
    "讲座": "academic_talk",
    "section": "section",
    "full": "section",
    "套题": "section",
    "整套": "section",
}

TOPIC_ALIASES = {
    "生物": "biology",
    "biology": "biology",
    "bio": "biology",
    "科技": "technology",
    "技术": "technology",
    "technology": "technology",
    "tech": "technology",
    "校园": "campus",
    "campus": "campus",
    "政策": "policy",
    "policy": "policy",
    "人文": "humanities",
    "humanities": "humanities",
    "历史": "humanities",
    "history": "humanities",
    "心理": "psychology",
    "psychology": "psychology",
    "环境": "environment",
    "environment": "environment",
    "地质": "geology",
    "geology": "geology",
    "交通": "transportation",
    "transport": "transportation",
    "transportation": "transportation",
}

TOPIC_LABELS = {
    "biology": "生物",
    "technology": "科技",
    "campus": "校园",
    "policy": "政策",
    "humanities": "人文",
    "psychology": "心理",
    "environment": "环境",
    "geology": "地质",
    "transportation": "交通",
}

QUESTION_TYPE_LABELS = {
    "response_selection": "回应匹配",
    "main_idea": "主旨",
    "detail": "细节",
    "function": "话语功能",
    "inference": "推断",
    "purpose": "目的",
    "next_step": "后续动作",
    "organization": "结构",
}

TTS_BROWSER_NAME = "Microsoft Edge"
TTS_SITE_URL = "http://new.text-to-speech.cn/tts/?ref=tts"

SECTION_BLUEPRINT_2026 = [
    "choose_response",
    "conversation",
    "announcement",
    "academic_talk",
]


FALLBACK_PROMPT_BANK = {
    "choose_response": [
        {
            "topic_tags": ["campus", "policy"],
            "topic_label_cn": "校园",
            "transcript": "Professor Lee, could we move my office-hour appointment to Thursday because my lab session was rescheduled?",
        },
        {
            "topic_tags": ["technology", "campus"],
            "topic_label_cn": "科技",
            "transcript": "Hi, this is IT support. Did restarting the campus app fix the login issue you reported this morning?",
        },
    ],
    "conversation": [
        {
            "topic_tags": ["campus", "policy"],
            "topic_label_cn": "校园政策",
            "transcript": (
                "Student: Hi, I got an email saying my scholarship documents are incomplete, but I submitted everything last week. "
                "Advisor: I can see your file. The financial form is here, but your internship verification letter is missing a supervisor signature. "
                "Student: Oh, I uploaded the draft letter first because my supervisor was traveling. "
                "Advisor: That explains it. If you upload the signed version by Friday, your status will be restored before the payment cycle closes. "
                "Student: If I miss Friday, will I lose funding for the whole semester? "
                "Advisor: Not the whole semester, but your first payment may be delayed by two weeks. "
                "Student: Got it. I will get the signed letter today and update the portal tonight."
            ),
        },
        {
            "topic_tags": ["technology", "campus"],
            "topic_label_cn": "科技",
            "transcript": (
                "Student: Excuse me, I reserved a 3D printer slot for tomorrow, but the booking page now shows it as unavailable. "
                "Lab Assistant: We had to close two printers for maintenance after a calibration error. "
                "Student: My design review is on Monday, so I really need a prototype this weekend. "
                "Lab Assistant: In that case, switch to the polymer printer in Room B. It prints slower, but the detail quality is better for complex edges. "
                "Student: Does it use the same file format? "
                "Lab Assistant: Yes, but export at high resolution and add support structures manually. "
                "Student: Thanks. I will revise the file and book a longer slot tonight."
            ),
        },
    ],
    "announcement": [
        {
            "topic_tags": ["campus", "policy"],
            "topic_label_cn": "校园",
            "transcript": (
                "Good afternoon, this is a notice from the campus library. Starting next Monday, the second floor silent zone will open one hour earlier, "
                "at seven a.m., during midterm week. Group study rooms will remain available by reservation only, and each booking is limited to ninety minutes. "
                "Please remember to confirm your reservation at the self-service kiosk within ten minutes of your start time, or the room will be released automatically. "
                "If you need accessibility seating, email librarysupport before Friday so staff can assign priority spaces."
            ),
        },
        {
            "topic_tags": ["technology", "campus"],
            "topic_label_cn": "科技",
            "transcript": (
                "Attention engineering students. The micro-robotics workshop scheduled for Wednesday evening has moved from Hall C to Innovation Lab 2. "
                "Because demand exceeded capacity, attendance is now split into two sessions, one at five p.m. and another at seven p.m. "
                "You must check your assigned session in the student portal and complete the safety quiz before arrival. "
                "Students who finish the quiz late may still attend, but they will need to wait for on-site briefing and may miss the live demonstration."
            ),
        },
    ],
    "academic_talk": [
        {
            "topic_tags": ["biology", "environment"],
            "topic_label_cn": "生物",
            "transcript": (
                "Professor: Today we are looking at how coastal wetlands respond to gradual sea-level rise. Early models assumed wetlands either survived or collapsed, "
                "but newer field data show a more complex sequence. First, plant communities shift toward salt-tolerant species. Second, sediment capture can temporarily "
                "increase surface elevation, which slows flooding. Third, once storm frequency crosses a threshold, soil structure weakens and carbon storage declines. "
                "Why does this matter? Wetlands are not only habitats; they also buffer wave energy and reduce infrastructure damage during storms. "
                "Researchers now combine satellite imagery, soil cores, and drone-based elevation mapping to estimate transition points. "
                "A key limitation is timescale mismatch: satellite records may show rapid change over ten years, while soil chemistry reflects processes unfolding over decades. "
                "So when you read policy recommendations, ask whether they distinguish short-term adaptation from long-term ecosystem transformation. "
                "Without that distinction, restoration plans may overestimate resilience and underfund protective migration corridors."
            ),
        },
        {
            "topic_tags": ["technology", "humanities"],
            "topic_label_cn": "科技",
            "transcript": (
                "Professor: In digital archaeology, scholars use multispectral imaging to recover writing that is invisible to the naked eye. "
                "At first glance, that sounds purely technical, but interpretation remains the harder step. Different pigments reflect light differently, "
                "so an enhanced image may reveal multiple layers of annotation from different historical periods. "
                "To separate those layers, teams align image channels and compare handwriting patterns with known manuscript databases. "
                "Even then, ambiguity persists. A faded mark might be a punctuation symbol, a correction, or damage caused by humidity. "
                "That is why collaborative review matters: imaging specialists, language historians, and conservators evaluate the same evidence from different angles. "
                "When these perspectives converge, confidence in transcription rises. When they diverge, responsible reports label the reading as provisional. "
                "So the broader lesson is methodological: better tools increase access to evidence, but they do not remove the need for careful argumentation."
            ),
        },
    ],
}

FALLBACK_EXTENSION_SENTENCES = [
    "Listeners should pay attention to constraints, timing, and stated priorities.",
    "The speaker contrasts practical steps with potential risks in implementation.",
    "A key point is that early planning can prevent avoidable delays.",
    "The message also implies that support resources are available if problems continue.",
    "Another detail highlights how context affects decision quality and outcomes.",
]


TASK_TYPE_QUERY_HINT = {
    "choose_response": "short spoken prompt appropriate response in campus academic context",
    "conversation": "campus conversation between student and advisor about scheduling policy resources",
    "announcement": "university campus announcement schedule policy student services",
    "academic_talk": "short academic lecture transcript for university students",
}

TOPIC_QUERY_HINT = {
    "biology": "biology ecology genetics research",
    "technology": "technology engineering computing research",
    "campus": "university campus student services",
    "policy": "education policy campus administration",
    "humanities": "history literature linguistics anthropology",
    "psychology": "psychology cognition behavior",
    "environment": "environment climate sustainability",
    "geology": "geology earth science",
    "transportation": "transportation urban mobility",
}


def _clip(text: str, limit: int = 400) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]+(?:[-'][A-Za-z]+)?", text or ""))


def _normalize_task_type(raw: str | None) -> str | None:
    if not raw:
        return None
    token = str(raw).strip()
    if not token:
        return None

    lowered = token.lower().replace("-", "_")
    lowered_compact = lowered.replace("_", "").replace(" ", "")
    if lowered in LISTENING_TASK_SPECS:
        return lowered
    if lowered in LISTENING_TASK_ALIASES:
        return LISTENING_TASK_ALIASES[lowered]
    if lowered_compact in LISTENING_TASK_ALIASES:
        return LISTENING_TASK_ALIASES[lowered_compact]
    if token in LISTENING_TASK_ALIASES:
        return LISTENING_TASK_ALIASES[token]
    return None


def _normalize_topic_hint(raw: str | None) -> str | None:
    if not raw:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    return TOPIC_ALIASES.get(text)


def _topic_hint_display(topic_hint: str | None) -> str:
    if not topic_hint:
        return ""
    return TOPIC_LABELS.get(topic_hint, topic_hint)


def _safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _ensure_state_dir() -> None:
    listening_state_file.parent.mkdir(parents=True, exist_ok=True)


def _load_state() -> dict:
    if not listening_state_file.exists():
        return {}
    try:
        with open(listening_state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    _ensure_state_dir()
    with open(listening_state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _record_event(event_type: str, payload: dict) -> None:
    db = SessionLocal()
    try:
        db.add(StudyEvent(event_type=event_type, payload_json=json.dumps(payload, ensure_ascii=False)))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _candidate_config_paths():
    cwd = Path.cwd()
    return [
        Path.home() / ".openclaw" / "gateway" / "config.json",
        Path.home() / ".openclaw" / "config.json",
        Path.home() / ".openclaw" / "openclaw.json",
        cwd / ".openclaw" / "gateway" / "config.json",
        cwd / ".openclaw" / "config.json",
        cwd / ".openclaw" / "openclaw.json",
        project_root / ".openclaw" / "gateway" / "config.json",
    ]


def _load_json_file(config_path: Path):
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            with open(config_path, "r", encoding=encoding) as f:
                return json.load(f)
        except Exception:
            continue
    raise ValueError(f"Unable to parse JSON config: {config_path}")


def _load_tavily_api_key() -> str | None:
    for config_path in _candidate_config_paths():
        if not config_path.exists():
            continue
        try:
            config = _load_json_file(config_path)
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


def _load_source_cache() -> list[dict]:
    with _source_cache_lock:
        if not listening_source_cache_file.exists():
            return []
        try:
            payload = _safe_json_loads(listening_source_cache_file.read_text(encoding="utf-8"), {})
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            return [entry for entry in entries if isinstance(entry, dict) and entry.get("material")]
        except Exception:
            return []


def _save_source_cache(entries: list[dict]) -> None:
    with _source_cache_lock:
        listening_source_cache_file.parent.mkdir(parents=True, exist_ok=True)
        trimmed = sorted(entries, key=lambda item: (item.get("used_count", 0), item.get("created_at", "")))
        payload = {
            "updated_at": datetime.now().isoformat(),
            "entries": trimmed[:SOURCE_CACHE_MAX_ITEMS],
        }
        listening_source_cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fetch_tavily_bundle(api_key: str, task_type: str, topic_hint: str | None = None) -> dict | None:
    topic_query = TOPIC_QUERY_HINT.get(topic_hint or "", "academic English university")
    task_query = TASK_TYPE_QUERY_HINT.get(task_type, "academic listening transcript")
    query = f"{topic_query} {task_query}".strip()

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_domains": [
                    "nature.com",
                    "sciencedaily.com",
                    "nationalgeographic.com",
                    "scientificamerican.com",
                    "newscientist.com",
                    "bbc.com",
                    "phys.org",
                ],
            },
            timeout=22,
        )
        if response.status_code != 200:
            return None

        payload = response.json()
        results = payload.get("results", [])
        snippets = []
        references = []
        for item in results:
            content = str(item.get("content") or "").strip()
            if content and len(content) > 80:
                snippets.append(content)
                url = str(item.get("url") or "")
                references.append({
                    "title": str(item.get("title") or ""),
                    "url": url,
                    "domain": url.split("/")[2] if url else "",
                })

        material = "\n\n".join(snippets[:4])
        if len(material) < 120:
            return None

        cache_id = f"{task_type}-{topic_hint or 'general'}-{abs(hash(material[:180]))}"
        return {
            "cache_id": cache_id,
            "task_type": task_type,
            "topic_hint": topic_hint,
            "query": query,
            "material": material[:3200],
            "references": references[:4],
            "used_count": 0,
            "created_at": datetime.now().isoformat(),
            "last_used_at": None,
        }
    except Exception:
        return None


def _prefetch_source_cache(task_type: str, topic_hint: str | None, fast_mode: bool = False) -> list[dict]:
    entries = _load_source_cache()
    api_key = _load_tavily_api_key()
    if not api_key:
        return entries

    matching = [
        entry
        for entry in entries
        if entry.get("task_type") == task_type and (not topic_hint or entry.get("topic_hint") == topic_hint)
    ]
    if fast_mode and matching:
        return entries

    if fast_mode:
        need = 1 if len(entries) < SOURCE_CACHE_MAX_ITEMS else 0
    else:
        if len(entries) < SOURCE_CACHE_MIN_ITEMS:
            need = max(1, min(SOURCE_CACHE_PREFETCH_BATCH, SOURCE_CACHE_MIN_ITEMS - len(entries)))
        else:
            need = 1 if len(entries) < SOURCE_CACHE_MAX_ITEMS else 0

    if need <= 0:
        return entries

    seen_cache_ids = {entry.get("cache_id") for entry in entries if entry.get("cache_id")}
    topics = list(TOPIC_LABELS.keys())
    random.shuffle(topics)
    if topic_hint and topic_hint in topics:
        topics = [topic_hint] + [item for item in topics if item != topic_hint]

    topic_candidates = []
    candidate_count = max(need * 3, need)
    for idx in range(candidate_count):
        candidate_topic = topics[idx % len(topics)] if topics else topic_hint
        topic_candidates.append(candidate_topic)

    if need > 1 and len(topic_candidates) > 1:
        max_workers = min(4, len(topic_candidates))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch_tavily_bundle, api_key, task_type, candidate_topic) for candidate_topic in topic_candidates]
            for future in concurrent.futures.as_completed(futures):
                bundle = future.result()
                if not bundle:
                    continue
                cache_id = bundle.get("cache_id")
                if cache_id and cache_id in seen_cache_ids:
                    continue
                entries.append(bundle)
                if cache_id:
                    seen_cache_ids.add(cache_id)
                need -= 1
                if need <= 0:
                    break
    else:
        for candidate_topic in topic_candidates:
            if need <= 0:
                break
            bundle = _fetch_tavily_bundle(api_key, task_type, candidate_topic)
            if not bundle:
                continue
            cache_id = bundle.get("cache_id")
            if cache_id and cache_id in seen_cache_ids:
                continue
            entries.append(bundle)
            if cache_id:
                seen_cache_ids.add(cache_id)
            need -= 1

    if entries:
        _save_source_cache(entries)
    return entries


def _select_source_bundle(task_type: str, topic_hint: str | None, fast_mode: bool = False) -> dict | None:
    entries = _prefetch_source_cache(task_type, topic_hint, fast_mode=fast_mode)
    if not entries:
        return None

    filtered = [entry for entry in entries if entry.get("task_type") == task_type]
    if topic_hint:
        topic_filtered = [entry for entry in filtered if entry.get("topic_hint") == topic_hint]
        if topic_filtered:
            filtered = topic_filtered
    if not filtered:
        filtered = entries

    def _score(entry):
        topic_bonus = 0 if not topic_hint else (0 if entry.get("topic_hint") == topic_hint else 1)
        return (topic_bonus, int(entry.get("used_count", 0)), entry.get("last_used_at") or "")

    selected = min(filtered, key=_score)

    # 回写 source cache 使用信息
    for entry in entries:
        if entry.get("cache_id") == selected.get("cache_id"):
            entry["used_count"] = int(entry.get("used_count", 0)) + 1
            entry["last_used_at"] = datetime.now().isoformat()
            break

    entries = [
        entry
        for entry in entries
        if int(entry.get("used_count", 0)) < SOURCE_CACHE_MAX_USES or entry.get("cache_id") == selected.get("cache_id")
    ]
    _save_source_cache(entries)

    return {
        "topic_hint": selected.get("topic_hint"),
        "material": selected.get("material", ""),
        "references": selected.get("references", []),
    }


def _load_passage_cache() -> list[dict]:
    with _passage_cache_lock:
        if not listening_passage_cache_file.exists():
            return []
        try:
            payload = _safe_json_loads(listening_passage_cache_file.read_text(encoding="utf-8"), {})
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            return [entry for entry in entries if isinstance(entry, dict) and entry.get("transcript") and entry.get("questions")]
        except Exception:
            return []


def _save_passage_cache(entries: list[dict]) -> None:
    with _passage_cache_lock:
        listening_passage_cache_file.parent.mkdir(parents=True, exist_ok=True)
        trimmed = sorted(entries, key=lambda item: (item.get("used_count", 0), item.get("created_at", "")))
        payload = {
            "updated_at": datetime.now().isoformat(),
            "entries": trimmed[:PASSAGE_CACHE_MAX_ITEMS],
        }
        listening_passage_cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_question(item: dict, index: int, default_type: str) -> dict | None:
    if not isinstance(item, dict):
        return None
    question = str(item.get("question") or item.get("stem") or "").strip()
    if not question:
        return None

    q_type = str(item.get("type") or default_type).strip().lower()
    if q_type not in QUESTION_TYPE_LABELS:
        q_type = default_type if default_type in QUESTION_TYPE_LABELS else "detail"

    options = item.get("options") if isinstance(item.get("options"), dict) else {}
    normalized_options = {}
    for key in ("A", "B", "C", "D"):
        value = str(options.get(key) or "").strip()
        normalized_options[key] = value or f"Option {key}"

    answer = str(item.get("answer") or "").strip().upper()
    if answer not in {"A", "B", "C", "D"}:
        answer = "A"

    explanation = _clip(str(item.get("explanation") or "").strip(), 320)
    evidence = _clip(str(item.get("evidence") or "").strip(), 180)

    return {
        "id": index,
        "type": q_type,
        "question": question,
        "options": normalized_options,
        "answer": answer,
        "explanation": explanation,
        "evidence": evidence,
    }


def _fallback_questions(task_type: str, transcript: str) -> list[dict]:
    spec = LISTENING_TASK_SPECS[task_type]
    q_types = spec["q_types"]
    first_sentence = _clip((transcript or "").split(".")[0].strip(), 100)
    key_detail = _clip((transcript or "").split(".")[1].strip(), 100) if "." in (transcript or "") else first_sentence
    key_detail = key_detail or first_sentence
    questions: list[dict] = []
    for idx, q_type in enumerate(q_types, 1):
        stem = f"[{QUESTION_TYPE_LABELS.get(q_type, q_type)}] Which option best matches the audio content?"
        if q_type == "response_selection":
            stem = "What is the most appropriate response to the speaker?"
        elif q_type == "main_idea":
            stem = "What is the main idea of the audio clip?"
        elif q_type == "detail":
            stem = "According to the audio, which detail is correct?"
        elif q_type == "function":
            stem = "Why does the speaker say this part?"
        elif q_type == "inference":
            stem = "What can be inferred from the audio?"
        elif q_type == "purpose":
            stem = "What is the primary purpose of this announcement?"
        elif q_type == "organization":
            stem = "How is the talk mainly organized?"
        elif q_type == "next_step":
            stem = "What will listeners most likely do next?"

        correct_option = "A"
        option_a = f"It aligns with the speaker's key point: {first_sentence}"
        option_b = "It reverses the speaker's stated relationship between cause and effect."
        option_c = "It introduces a claim that is not mentioned in the audio."
        option_d = "It focuses on an unrelated procedural detail while ignoring the main point."

        if q_type == "detail":
            option_a = f"It matches a stated detail: {key_detail}"
        elif q_type == "function":
            option_a = "The speaker uses that part to clarify a problem and offer a practical step."
        elif q_type == "inference":
            option_a = "The audio implies the speaker expects listeners to act on the recommendation soon."
        elif q_type == "purpose":
            option_a = "The announcement aims to communicate logistics, constraints, and required actions."
        elif q_type == "organization":
            option_a = "The talk is organized as claim -> evidence -> limitation -> implication."
        elif q_type == "next_step":
            option_a = "Listeners are expected to complete the specified follow-up action promptly."

        questions.append({
            "id": idx,
            "type": q_type,
            "question": stem,
            "options": {
                "A": option_a,
                "B": option_b,
                "C": option_c,
                "D": option_d,
            },
            "answer": correct_option,
            "explanation": "Option A best aligns with the speaker's purpose and supporting information.",
            "evidence": _clip(first_sentence or transcript, 160),
        })
    return questions


def _llm_generate_item(task_type: str, topic_hint: str | None, source_bundle: dict | None, recent_samples: list[str], timeout_seconds: int = 95) -> dict | None:
    if not is_configured():
        return None

    spec = LISTENING_TASK_SPECS[task_type]
    q_types = spec["q_types"]
    topic_text = topic_hint or "general"
    source_material = _clip((source_bundle or {}).get("material", ""), 1600)

    system_prompt = """You are a TOEFL iBT Listening item writer for the 2026 format.
Return JSON only, no markdown.
"""

    user_prompt = f"""
Create ONE listening item for task type: {task_type} ({spec['label']}).

Requirements:
- Topic hint: {topic_text}
- Transcript length: {spec['word_min']} to {spec['word_max']} words (strict).
- Number of questions: {spec['question_count']} (strict).
- Question type order: {json.dumps(q_types, ensure_ascii=False)}
- Every question must have 4 options (A-D) and exactly one correct answer.
- Include concise explanation and direct evidence quote from transcript for each question.
- Keep language natural, academic TOEFL-like.
- Avoid repeating these recent transcript openings:
{json.dumps([_clip(item, 100) for item in recent_samples[:4]], ensure_ascii=False)}

Source context (optional, adapt but do not copy verbatim):
{source_material}

Output JSON schema:
{{
  "topic_label_cn": "string",
  "topic_tags": ["string", "string"],
  "transcript": "string",
  "questions": [
    {{
      "type": "string",
      "question": "string",
      "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
      "answer": "A|B|C|D",
      "explanation": "string",
      "evidence": "short quote from transcript"
    }}
  ]
}}
"""

    payload = chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.45,
        max_tokens=2600,
        timeout=max(25, int(timeout_seconds)),
        retries=1,
    )
    if not payload:
        return None

    transcript = str(payload.get("transcript") or "").strip()
    if not transcript:
        return None

    wc = _word_count(transcript)
    if wc < spec["word_min"]:
        extension_idx = 0
        while _word_count(transcript) < spec["word_min"]:
            addon = FALLBACK_EXTENSION_SENTENCES[extension_idx % len(FALLBACK_EXTENSION_SENTENCES)]
            transcript += f" {addon}"
            extension_idx += 1
    elif wc > spec["word_max"]:
        transcript = " ".join(transcript.split()[: spec["word_max"]])

    raw_questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
    fallback_questions = _fallback_questions(task_type, transcript)
    normalized_questions = []
    for idx, default_q_type in enumerate(q_types, 1):
        source_item = raw_questions[idx - 1] if idx - 1 < len(raw_questions) else {}
        normalized = _normalize_question(source_item, idx, default_q_type)
        if not normalized:
            if idx - 1 < len(fallback_questions):
                normalized = dict(fallback_questions[idx - 1])
                normalized["id"] = idx
                normalized["type"] = default_q_type
            else:
                continue
        normalized_questions.append(normalized)

    if len(normalized_questions) < spec["question_count"]:
        for idx in range(len(normalized_questions), spec["question_count"]):
            if idx < len(fallback_questions):
                candidate = dict(fallback_questions[idx])
                candidate["id"] = idx + 1
                if idx < len(q_types):
                    candidate["type"] = q_types[idx]
                normalized_questions.append(candidate)

    normalized_questions = normalized_questions[: spec["question_count"]]

    topic_tags = payload.get("topic_tags") if isinstance(payload.get("topic_tags"), list) else []
    topic_tags = [str(tag).strip().lower() for tag in topic_tags if str(tag).strip()][:3]
    if topic_hint and topic_hint not in topic_tags:
        topic_tags.insert(0, topic_hint)

    item = {
        "prompt_id": f"listen_{task_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}",
        "task_type": task_type,
        "topic_label_cn": str(payload.get("topic_label_cn") or _topic_hint_display(topic_hint) or "综合").strip(),
        "topic_tags": topic_tags,
        "transcript": transcript,
        "questions": normalized_questions,
        "references": (source_bundle or {}).get("references", []),
        "source": "tavily+llm" if source_bundle else "llm",
        "used_count": 0,
        "created_at": datetime.now().isoformat(),
        "last_used_at": None,
    }
    return item


def _fallback_item(task_type: str, topic_hint: str | None = None) -> dict:
    bank = FALLBACK_PROMPT_BANK.get(task_type) or []
    candidate = random.choice(bank) if bank else {"transcript": "The speaker explains a campus process and asks students to follow specific steps.", "topic_tags": ["campus"], "topic_label_cn": "校园"}
    transcript = str(candidate.get("transcript") or "").strip()

    # 长度补齐：fallback 也尽量贴近官方短促剪辑长度要求。
    spec = LISTENING_TASK_SPECS[task_type]
    extension_idx = 0
    while _word_count(transcript) < spec["word_min"]:
        addon = FALLBACK_EXTENSION_SENTENCES[extension_idx % len(FALLBACK_EXTENSION_SENTENCES)]
        transcript += f" {addon}"
        extension_idx += 1
    if _word_count(transcript) > spec["word_max"]:
        words = transcript.split()
        transcript = " ".join(words[: spec["word_max"]])

    topic_tags = [str(tag).strip().lower() for tag in candidate.get("topic_tags", []) if str(tag).strip()]
    if topic_hint and topic_hint not in topic_tags:
        topic_tags.insert(0, topic_hint)

    item = {
        "prompt_id": f"fallback_{task_type}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}",
        "task_type": task_type,
        "topic_label_cn": str(candidate.get("topic_label_cn") or _topic_hint_display(topic_hint) or "综合").strip(),
        "topic_tags": topic_tags,
        "transcript": transcript,
        "questions": _fallback_questions(task_type, transcript),
        "references": [],
        "source": "fallback",
        "used_count": 0,
        "created_at": datetime.now().isoformat(),
        "last_used_at": None,
    }
    return item


def _transcript_signature(text: str) -> str:
    normalized = " ".join((text or "").strip().lower().split())
    return str(abs(hash(normalized[:600])))


def _used_prompt_ids(state: dict, task_type: str) -> set[str]:
    prompt_history = state.get("prompt_history") if isinstance(state.get("prompt_history"), dict) else {}
    history = prompt_history.get(task_type) if isinstance(prompt_history, dict) else []
    if not isinstance(history, list):
        history = []
    return {str(item.get("prompt_id")) for item in history if isinstance(item, dict) and item.get("prompt_id")}


def _used_signatures(state: dict, task_type: str) -> set[str]:
    prompt_history = state.get("prompt_history") if isinstance(state.get("prompt_history"), dict) else {}
    history = prompt_history.get(task_type) if isinstance(prompt_history, dict) else []
    if not isinstance(history, list):
        history = []
    signatures = set()
    for item in history:
        if isinstance(item, dict) and item.get("signature"):
            signatures.add(str(item.get("signature")))
    return signatures


def _remember_prompt_history(state: dict, task_type: str, item: dict) -> None:
    prompt_history = state.setdefault("prompt_history", {})
    history = prompt_history.setdefault(task_type, [])
    history.append({
        "prompt_id": item.get("prompt_id"),
        "signature": _transcript_signature(item.get("transcript") or ""),
        "generated_at": datetime.now().isoformat(),
    })
    if len(history) > 80:
        prompt_history[task_type] = history[-80:]


def _prefetch_passage_cache(task_type: str, topic_hint: str | None, state: dict, fast_mode: bool = False) -> list[dict]:
    entries = _load_passage_cache()
    recent_ids = _used_prompt_ids(state, task_type)

    def _match(entry: dict) -> bool:
        if entry.get("task_type") != task_type:
            return False
        if topic_hint:
            tags = [str(tag).strip().lower() for tag in entry.get("topic_tags", []) if str(tag).strip()]
            if topic_hint not in tags:
                return False
        if entry.get("prompt_id") in recent_ids:
            return False
        return True

    matching = [entry for entry in entries if _match(entry)]

    need = 0
    if fast_mode:
        if len(matching) < 1 and len(entries) < PASSAGE_CACHE_MAX_ITEMS:
            need = 1
    else:
        if len(matching) < 1 and len(entries) < PASSAGE_CACHE_MAX_ITEMS:
            need = 1
        elif len(entries) < PASSAGE_CACHE_MIN_ITEMS and len(entries) < PASSAGE_CACHE_MAX_ITEMS:
            need = 1

    if need <= 0:
        return entries

    existing_signatures = {_transcript_signature(entry.get("transcript") or "") for entry in entries}
    recent_samples = [entry.get("transcript") or "" for entry in entries[-8:]]
    attempts = 0
    while need > 0 and attempts < PASSAGE_CACHE_PREFETCH_BATCH * 4:
        attempts += 1
        item = None
        if not fast_mode:
            source_bundle = _select_source_bundle(task_type, topic_hint, fast_mode=False)
            item = _llm_generate_item(task_type, topic_hint, source_bundle, recent_samples, timeout_seconds=55)
        else:
            # section 快速模式：先做一次短超时 LLM 尝试，失败再 fallback，兼顾质量和速度。
            source_bundle = _select_source_bundle(task_type, topic_hint, fast_mode=True)
            item = _llm_generate_item(task_type, topic_hint, source_bundle, recent_samples, timeout_seconds=35)
            if not item:
                item = _fallback_item(task_type, topic_hint)

        if not item:
            item = _fallback_item(task_type, topic_hint)
        sig = _transcript_signature(item.get("transcript") or "")
        if sig in existing_signatures:
            continue
        entries.append(item)
        existing_signatures.add(sig)
        recent_samples.append(item.get("transcript") or "")
        need -= 1

    if entries:
        _save_passage_cache(entries)
    return entries


def _select_cached_item(task_type: str, topic_hint: str | None, state: dict, fast_mode: bool = False) -> dict | None:
    entries = _prefetch_passage_cache(task_type, topic_hint, state, fast_mode=fast_mode)
    if not entries:
        return None

    used_ids = _used_prompt_ids(state, task_type)
    used_signatures = _used_signatures(state, task_type)

    def _match(entry: dict, allow_repeat: bool) -> bool:
        if entry.get("task_type") != task_type:
            return False
        if topic_hint:
            tags = [str(tag).strip().lower() for tag in entry.get("topic_tags", []) if str(tag).strip()]
            if topic_hint not in tags:
                return False
        signature = _transcript_signature(entry.get("transcript") or "")
        if not allow_repeat and (entry.get("prompt_id") in used_ids or signature in used_signatures):
            return False
        return True

    candidates = [entry for entry in entries if _match(entry, allow_repeat=False)]
    if not candidates:
        # 当本地同题材都用过时，强制生成新题，避免重复刷到旧题。
        recent_samples = [entry.get("transcript") or "" for entry in entries[-10:]]
        source_bundle = _select_source_bundle(task_type, topic_hint, fast_mode=fast_mode)
        timeout_seconds = 35 if fast_mode else 55
        fresh_item = _llm_generate_item(task_type, topic_hint, source_bundle, recent_samples, timeout_seconds=timeout_seconds)
        if not fresh_item:
            fresh_item = _fallback_item(task_type, topic_hint)

        fresh_signature = _transcript_signature(fresh_item.get("transcript") or "")
        if fresh_signature not in {_transcript_signature(entry.get("transcript") or "") for entry in entries}:
            entries.append(fresh_item)
            _save_passage_cache(entries)
            candidates = [fresh_item]

    if not candidates:
        candidates = [entry for entry in entries if _match(entry, allow_repeat=True)]
    if not candidates:
        candidates = [entry for entry in entries if entry.get("task_type") == task_type]
    if not candidates:
        return None

    candidates.sort(key=lambda item: (int(item.get("used_count", 0)), item.get("last_used_at") or ""))
    selected = random.choice(candidates[: min(3, len(candidates))])

    for entry in entries:
        if entry.get("prompt_id") == selected.get("prompt_id"):
            entry["used_count"] = int(entry.get("used_count", 0)) + 1
            entry["last_used_at"] = datetime.now().isoformat()
            break

    entries = [
        entry
        for entry in entries
        if int(entry.get("used_count", 0)) < PASSAGE_CACHE_MAX_USES or entry.get("prompt_id") == selected.get("prompt_id")
    ]
    _save_passage_cache(entries)
    return selected


def _format_questions_block(questions: list[dict], start_index: int = 1) -> tuple[list[str], int]:
    lines: list[str] = []
    current = start_index
    for item in questions:
        q_type = str(item.get("type") or "detail")
        label = QUESTION_TYPE_LABELS.get(q_type, q_type)
        lines.append(f"{current}. [{label}] {item.get('question')}")
        options = item.get("options") if isinstance(item.get("options"), dict) else {}
        for key in ("A", "B", "C", "D"):
            lines.append(f"   {key}. {options.get(key, '')}")
        lines.append("")
        current += 1
    return lines, current


def _render_audio_block(item: dict, passage_index: int | None = None, include_steps: bool = True) -> list[str]:
    task_type = item.get("task_type")
    spec = LISTENING_TASK_SPECS.get(task_type)
    transcript = str(item.get("transcript") or "").strip()
    wc = _word_count(transcript)

    title_prefix = ""
    if passage_index is not None:
        title_prefix = f"Passage {passage_index} | "

    marker_start = "<<<TTS_TEXT_START>>>"
    marker_end = "<<<TTS_TEXT_END>>>"
    if passage_index is not None:
        marker_start = f"<<<TTS_TEXT_START_P{passage_index}>>>"
        marker_end = f"<<<TTS_TEXT_END_P{passage_index}>>>"

    lines = [
        f"{title_prefix}Task Type: {spec['label']} ({spec['label_cn']})",
        f"Audio Length Target: {spec['target_words']} | Current: {wc} words",
    ]
    if include_steps:
        lines.extend([
            f"🤖 自动播放指令（先调用 browser 工具，再由 browser 工具调用 playwright 自动执行，用 {TTS_BROWSER_NAME} 打开 TTS 网站）:",
            f"1) 调用 browser 工具，并由 browser 工具调用 playwright 自动执行，用 {TTS_BROWSER_NAME} 打开 {TTS_SITE_URL}",
            f"2) 把 {marker_start} 和 {marker_end} 之间文本粘贴到左侧输入框",
            "3) 确认文本已粘贴完成后，点击“生成”或“play”，并播放音频 1 次；如果页面提示生成失败，就点击“生成失败”后继续播放",
            "4) 播放后只回复用户: 🎧 音频已播放，请开始答题；不要向用户贴出原文文本",
            "",
        ])

    lines.extend([
        marker_start,
        transcript,
        marker_end,
    ])
    return lines


def _render_section_audio_block(section: dict) -> list[str]:
    passages = section.get("passages") if isinstance(section.get("passages"), list) else []
    combined_segments: list[str] = []
    for idx, item in enumerate(passages, 1):
        task_type = str(item.get("task_type") or "")
        spec = LISTENING_TASK_SPECS.get(task_type) or {}
        transcript = str(item.get("transcript") or "").strip()
        if not transcript:
            continue
        header = f"Passage {idx}. {spec.get('label', task_type) or task_type}."
        combined_segments.append(f"{header}\n{transcript}")

    combined_text = "\n\n".join(segment for segment in combined_segments if segment).strip()
    total_words = _word_count(combined_text)

    return [
        "Section Audio | Full Set Combined Playback",
        f"Audio Length Target: full section combined | Current: {total_words} words",
        f"🤖 自动播放指令（整套只粘贴 1 次，不要按 Passage 分 4 次生成）:",
        f"1) 调用 browser 工具，并由 browser 工具调用 playwright 自动执行，用 {TTS_BROWSER_NAME} 打开 {TTS_SITE_URL}（只打开一次）",
        "2) 只把下方 <<<TTS_TEXT_START>>> 和 <<<TTS_TEXT_END>>> 之间的整套文本一次性全部粘贴到左侧输入框，不要拆成四段",
        "3) 确认整套文本已全部粘贴完成后，只生成 1 次并开始连续播放；如果页面提示生成失败，就点击“生成失败”后继续播放",
        "4) 播放过程中不要代替用户分段暂停；由用户自己在需要时手动暂停",
        "5) 全部播放开始后只回复用户: 🎧 全部音频已开始播放，请按需要自行暂停后答题；不要向用户贴出原文文本",
        "",
        "<<<TTS_TEXT_START>>>",
        combined_text,
        "<<<TTS_TEXT_END>>>",
    ]


def _render_single_set(item: dict, topic_hint: str | None = None) -> str:
    task_type = item.get("task_type")
    spec = LISTENING_TASK_SPECS[task_type]

    lines = [
        "🎧 TOEFL 听力题已生成（2026 任务类型模板）",
        f"题型: {task_type} ({spec['label_cn']})",
    ]
    topic_display = _topic_hint_display(topic_hint)
    if topic_display:
        lines.append(f"话题提示: {topic_display}")

    lines.append("")
    lines.extend(_render_audio_block(item))
    lines.append("")
    q_lines, _ = _format_questions_block(item.get("questions", []), 1)
    lines.extend(q_lines)

    lines.extend([
        "请一次性提交全部答案:",
        "!eng listen submit 1A 2B 3C ...",
        "或: !eng listen submit ABCD...",
        "查看单题解析: !eng listen explain <题号>",
    ])

    if item.get("references"):
        lines.append("")
        lines.append("参考来源:")
        for idx, ref in enumerate(item.get("references", [])[:2], 1):
            title = ref.get("title") or ref.get("domain") or "source"
            lines.append(f"{idx}. {title}")

    return "\n".join(lines)


def _render_section_set(section: dict) -> str:
    lines = [
        "🎧 TOEFL 听力仿真套题（2026 模板）",
        "结构: Choose Response + Conversation + Announcement + Academic Talk",
    ]
    topic_display = _topic_hint_display(section.get("topic_hint"))
    if topic_display:
        lines.append(f"话题提示: {topic_display}")
    lines.append(f"总题量: {len(section.get('questions', []))}")
    lines.append("")
    lines.extend(_render_section_audio_block(section))
    lines.extend([
        "",
        "分 Passage 题目清单:",
        "",
    ])

    passages = section.get("passages") if isinstance(section.get("passages"), list) else []
    question_cursor = 1
    for idx, item in enumerate(passages, 1):
        lines.append(f"Passage {idx} Questions:")
        q_lines, question_cursor = _format_questions_block(item.get("questions", []), question_cursor)
        lines.extend(q_lines)

    lines.extend([
        "请一次性提交全部答案:",
        "!eng listen submit 1A 2B 3C ...",
        "或: !eng listen submit ABCD...",
        "查看单题解析: !eng listen explain <题号>",
    ])
    return "\n".join(lines)


def _parse_prompt_request(raw: str) -> tuple[str | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, None

    # 常规: <task> <topic>
    parts = text.split(None, 1)
    first = parts[0] if parts else ""
    remainder = parts[1].strip() if len(parts) > 1 else ""
    task = _normalize_task_type(first)
    if task:
        return task, _normalize_topic_hint(remainder)

    # 紧凑命令: promptconversation科技 / promptacademictalk生物
    lowered = text.lower()
    for alias in sorted(LISTENING_TASK_ALIASES.keys(), key=len, reverse=True):
        target = LISTENING_TASK_ALIASES[alias]
        if target not in LISTENING_TASK_SPECS and target != "section":
            continue

        if alias.isascii():
            if lowered.startswith(alias):
                tail = text[len(alias) :].strip()
                return target, _normalize_topic_hint(tail)
        else:
            if text.startswith(alias):
                tail = text[len(alias) :].strip()
                return target, _normalize_topic_hint(tail)

    # 只有 topic
    return None, _normalize_topic_hint(text)


def _build_active_question_set(state: dict) -> tuple[str, dict] | tuple[None, None]:
    latest_set = state.get("latest_set") if isinstance(state.get("latest_set"), dict) else None
    latest_section = state.get("latest_section") if isinstance(state.get("latest_section"), dict) else None
    if latest_set and not latest_section:
        return "set", latest_set
    if latest_section and not latest_set:
        return "section", latest_section
    if not latest_set and not latest_section:
        return None, None

    set_time = latest_set.get("generated_at") or ""
    section_time = latest_section.get("generated_at") or ""
    if section_time >= set_time:
        return "section", latest_section
    return "set", latest_set


def _flatten_section_questions(section: dict) -> list[dict]:
    out: list[dict] = []
    passages = section.get("passages") if isinstance(section.get("passages"), list) else []
    qid = 1
    for passage_idx, item in enumerate(passages, 1):
        for q in item.get("questions", []):
            merged = dict(q)
            merged["global_id"] = qid
            merged["passage_index"] = passage_idx
            merged["task_type"] = item.get("task_type")
            out.append(merged)
            qid += 1
    return out


def _parse_answers(raw: str, total: int) -> dict[int, str] | None:
    text = (raw or "").strip().upper()
    if not text or total <= 0:
        return None

    indexed = re.findall(r"(\d{1,2})\s*[:：.\-]?\s*([ABCD])", text)
    if indexed:
        answer_map: dict[int, str] = {}
        for idx_text, ans in indexed:
            idx = int(idx_text)
            if 1 <= idx <= total:
                answer_map[idx] = ans
        if len(answer_map) == total:
            return answer_map

    letters = re.sub(r"[^ABCD]", "", text)
    if len(letters) == total:
        return {i + 1: letters[i] for i in range(total)}

    tokens = re.findall(r"\b([ABCD])\b", text)
    if len(tokens) == total:
        return {i + 1: tokens[i] for i in range(total)}

    return None


def _score_questions(flat_questions: list[dict], answer_map: dict[int, str]) -> tuple[int, list[dict]]:
    correct = 0
    details: list[dict] = []
    for idx, question in enumerate(flat_questions, 1):
        right = str(question.get("answer") or "A").upper()
        user = answer_map.get(idx, "")
        ok = user == right
        if ok:
            correct += 1
        details.append({
            "index": idx,
            "user": user,
            "right": right,
            "is_correct": ok,
            "type": question.get("type") or "detail",
            "question": question.get("question") or "",
            "explanation": question.get("explanation") or "",
            "evidence": question.get("evidence") or "",
            "passage_index": question.get("passage_index"),
            "task_type": question.get("task_type"),
        })
    return correct, details


def list_task_types() -> str:
    lines = [
        "🎧 TOEFL 听力题型（2026 官网任务类型模板）",
        "",
        "1) choose_response - Listen and Choose a Response（1题）",
        "2) conversation - Listen to a Conversation（5题）",
        "3) announcement - Listen to an Announcement（4题）",
        "4) academic_talk - Listen to an Academic Talk（6题）",
        "5) section - 2026 仿真整套（以上 4 段合并，共16题）",
        "",
        "用法:",
        "- !eng listen prompt <题型> [生物|科技|校园|政策|人文|心理|环境...]",
        "- !eng listen prompt section [话题]  生成整套听力（一次性连续播放，不逐段等待）",
        "- !eng listen submit <全部答案>  一次性提交并评分",
        "- !eng listen explain <题号>  查看单题解析与证据定位",
    ]
    return "\n".join(lines)


def listen_help() -> str:
    return "\n".join([
        "🎧 TOEFL 听力教练命令",
        "- !eng listen types",
        "- !eng listen prompt <choose_response|conversation|announcement|academic_talk|section> [话题]",
        "- section 会输出一次性连续播放指令（4段音频依次自动播放）",
        "- !eng listen submit <答案串> 例如: 1A 2C 3B 或 ABCD...",
        "- !eng listen explain <题号>",
        "- !eng listen <答案串>  默认按最近听力题评分",
    ])


def _build_single_set(task_type: str, topic_hint: str | None, state: dict, fast_mode: bool = False, record_history: bool = True) -> dict:
    item = _select_cached_item(task_type, topic_hint, state, fast_mode=fast_mode)
    if not item:
        source_bundle = _select_source_bundle(task_type, topic_hint, fast_mode=fast_mode)
        timeout_seconds = 35 if fast_mode else 55
        item = _llm_generate_item(task_type, topic_hint, source_bundle, [], timeout_seconds=timeout_seconds)
    if not item:
        item = _fallback_item(task_type, topic_hint)

    if record_history:
        _remember_prompt_history(state, task_type, item)
    return item


def start_prompt(task_type: str | None = None, topic_hint: str | None = None) -> str:
    state = _load_state()
    normalized = _normalize_task_type(task_type) or "academic_talk"
    if normalized == "section":
        return start_section(topic_hint)
    if normalized not in LISTENING_TASK_SPECS:
        return "⚠️ 听力题型不支持。发送 !eng listen types 查看全部题型。"

    item = _build_single_set(normalized, topic_hint, state, fast_mode=False)

    state.update({
        "latest_set": {
            "prompt_id": item.get("prompt_id"),
            "task_type": normalized,
            "topic_hint": topic_hint,
            "topic_label_cn": item.get("topic_label_cn"),
            "transcript": item.get("transcript"),
            "questions": item.get("questions"),
            "references": item.get("references", []),
            "source": item.get("source", "unknown"),
            "generated_at": datetime.now().isoformat(),
        },
        "latest_section": state.get("latest_section"),
    })
    _save_state(state)

    _record_event("listening_prompt_generated", {
        "task_type": normalized,
        "question_count": LISTENING_TASK_SPECS[normalized]["question_count"],
        "topic_hint": topic_hint,
    })

    return _render_single_set(item, topic_hint)


def start_section(topic_hint: str | None = None) -> str:
    state = _load_state()
    passages_by_task: dict[str, dict] = {}
    passages = []
    flat_questions = []
    global_id = 1

    max_workers = min(4, len(SECTION_BLUEPRINT_2026))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(_build_single_set, task_type, topic_hint, state, True, False): task_type
            for task_type in SECTION_BLUEPRINT_2026
        }
        for future in concurrent.futures.as_completed(future_map):
            task_type = future_map[future]
            try:
                passages_by_task[task_type] = future.result()
            except Exception:
                passages_by_task[task_type] = _fallback_item(task_type, topic_hint)

    for task_type in SECTION_BLUEPRINT_2026:
        item = passages_by_task.get(task_type) or _fallback_item(task_type, topic_hint)
        passages.append(item)
        _remember_prompt_history(state, task_type, item)
        for q in item.get("questions", []):
            merged = dict(q)
            merged["global_id"] = global_id
            merged["task_type"] = task_type
            flat_questions.append(merged)
            global_id += 1

    section = {
        "section_id": f"listen_section_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(100, 999)}",
        "topic_hint": topic_hint,
        "passages": passages,
        "questions": flat_questions,
        "generated_at": datetime.now().isoformat(),
    }

    state["latest_section"] = section
    _save_state(state)

    _record_event("listening_section_generated", {
        "topic_hint": topic_hint,
        "question_count": len(flat_questions),
    })

    return _render_section_set(section)


def submit_answers(answer_text: str) -> str:
    state = _load_state()
    active_type, active = _build_active_question_set(state)
    if not active_type or not active:
        return "⚠️ 还没有听力题。先发送 !eng listen prompt <题型> 或 !eng listen prompt section。"

    if active_type == "set":
        questions = active.get("questions") if isinstance(active.get("questions"), list) else []
        flat_questions = [dict(q) for q in questions]
    else:
        flat_questions = _flatten_section_questions(active)

    total = len(flat_questions)
    if total == 0:
        return "⚠️ 当前听力题为空，请重新生成。"

    answer_map = _parse_answers(answer_text, total)
    if not answer_map:
        return f"⚠️ 答案格式不正确。请一次性提交 {total} 题，例如: !eng listen submit 1A 2B 3C ..."

    correct_count, details = _score_questions(flat_questions, answer_map)
    percent = round((correct_count / total) * 100, 1)
    scaled = round((correct_count / total) * 30, 1)

    wrong_items = [item for item in details if not item["is_correct"]]

    result_payload = {
        "target": active_type,
        "submitted_at": datetime.now().isoformat(),
        "total": total,
        "correct": correct_count,
        "percent": percent,
        "scaled": scaled,
        "answers": {str(k): v for k, v in answer_map.items()},
        "details": details,
    }
    state["last_result"] = result_payload
    _save_state(state)

    _record_event("listening_scored", {
        "target": active_type,
        "total": total,
        "correct": correct_count,
        "scaled": scaled,
    })

    lines = [
        "📊 TOEFL 听力评分完成",
        f"得分: {correct_count}/{total} ({percent}%)",
        f"TOEFL 估算分: {scaled}/30",
        "",
    ]
    for item in details:
        flag = "✅" if item["is_correct"] else "❌"
        type_label = QUESTION_TYPE_LABELS.get(item["type"], item["type"])
        lines.append(f"{item['index']}. {flag} 你的答案 {item['user']} | 正确答案 {item['right']} ({type_label})")

    if wrong_items:
        lines.append("")
        lines.append("错题分析:")
        for item in wrong_items[:6]:
            lines.append(f"- Q{item['index']}：{_clip(item['question'], 90)}")
            if item.get("explanation"):
                lines.append(f"  原因: {item['explanation']}")
            if item.get("evidence"):
                lines.append(f"  证据: {item['evidence']}")

    # 用户提交后展示原文，便于按题复盘听力细节。
    lines.append("")
    lines.append("📜 听力原文（提交后公开）:")
    if active_type == "set":
        task_type = str(active.get("task_type") or "")
        spec = LISTENING_TASK_SPECS.get(task_type) or {}
        transcript = str(active.get("transcript") or "").strip()
        lines.append(f"{spec.get('label_cn', task_type) or '单题'}:")
        lines.append(transcript or "（无可用原文）")
    else:
        passages = active.get("passages") if isinstance(active.get("passages"), list) else []
        if not passages:
            lines.append("（无可用原文）")
        for idx, passage in enumerate(passages, 1):
            task_type = str(passage.get("task_type") or "")
            spec = LISTENING_TASK_SPECS.get(task_type) or {}
            transcript = str(passage.get("transcript") or "").strip()
            lines.append(f"Passage {idx} - {spec.get('label_cn', task_type) or task_type}:")
            lines.append(transcript or "（无可用原文）")
            lines.append("")

    lines.extend([
        "查看单题详解:",
        "!eng listen explain <题号>",
    ])
    return "\n".join(lines)


def explain_question(question_no: str) -> str:
    state = _load_state()
    active_type, active = _build_active_question_set(state)
    if not active_type or not active:
        return "⚠️ 还没有可解析的听力题。先发送 !eng listen prompt。"

    try:
        qid = int(str(question_no).strip())
    except Exception:
        return "⚠️ 题号格式不正确。用法: !eng listen explain <题号>"

    if active_type == "set":
        questions = active.get("questions") if isinstance(active.get("questions"), list) else []
        if qid < 1 or qid > len(questions):
            return f"⚠️ 题号超出范围。当前范围: 1-{len(questions)}"
        question = dict(questions[qid - 1])
        question["task_type"] = active.get("task_type")
        transcript = str(active.get("transcript") or "").strip()
        passage_index = 1
    else:
        flat_questions = _flatten_section_questions(active)
        if qid < 1 or qid > len(flat_questions):
            return f"⚠️ 题号超出范围。当前范围: 1-{len(flat_questions)}"
        question = flat_questions[qid - 1]
        passage_index = int(question.get("passage_index") or 1)
        passages = active.get("passages") if isinstance(active.get("passages"), list) else []
        transcript = ""
        if 1 <= passage_index <= len(passages):
            transcript = str(passages[passage_index - 1].get("transcript") or "").strip()

    last_result = state.get("last_result") if isinstance(state.get("last_result"), dict) else {}
    answer_map = last_result.get("answers") if isinstance(last_result.get("answers"), dict) else {}
    user_answer = answer_map.get(str(qid), "未作答")

    q_type = str(question.get("type") or "detail")
    type_label = QUESTION_TYPE_LABELS.get(q_type, q_type)

    lines = [
        f"🧩 听力第 {qid} 题解析（{type_label}）",
        f"题目: {question.get('question')}",
    ]

    options = question.get("options") if isinstance(question.get("options"), dict) else {}
    for key in ("A", "B", "C", "D"):
        lines.append(f"{key}. {options.get(key, '')}")

    lines.extend([
        "",
        f"正确答案: {question.get('answer')}",
        f"你的答案: {user_answer}",
    ])

    explanation = str(question.get("explanation") or "").strip()
    if explanation:
        lines.extend(["", "为什么选这个:", explanation])

    evidence = str(question.get("evidence") or "").strip()
    if evidence:
        lines.append("")
        lines.append(f"证据句: {evidence}")

    if transcript:
        lines.append("")
        lines.append(f"原文定位（Passage {passage_index}）:")
        lines.append(_clip(transcript, 900))

    return "\n".join(lines)


def handle_listening_command(args: str) -> str:
    text = (args or "").strip()
    if not text:
        return start_prompt()

    lowered = text.lower()
    if lowered in {"help", "?", "说明", "用法"}:
        return listen_help()
    if lowered in {"types", "type", "题型"}:
        return list_task_types()

    if lowered.startswith("prompt") or lowered in {"new", "题", "题目"}:
        payload = "" if lowered in {"new", "题", "题目"} else text[6:].strip()
        task_type, topic_hint = _parse_prompt_request(payload)
        return start_prompt(task_type, topic_hint)

    if lowered.startswith("section"):
        topic_hint = _normalize_topic_hint(text[7:].strip())
        return start_section(topic_hint)

    if lowered.startswith("submit") or lowered.startswith("answer"):
        payload = text.split(None, 1)
        answer_text = payload[1].strip() if len(payload) > 1 else ""
        if not answer_text:
            return "⚠️ 用法: !eng listen submit <全部答案>"
        return submit_answers(answer_text)

    if lowered.startswith("explain"):
        payload = text.split(None, 1)
        if len(payload) < 2 or not payload[1].strip():
            return "⚠️ 用法: !eng listen explain <题号>"
        return explain_question(payload[1].strip())

    first, *rest = text.split(None, 1)
    normalized = _normalize_task_type(first)
    if normalized == "section":
        hint = _normalize_topic_hint(rest[0].strip()) if rest else None
        return start_section(hint)
    if normalized in LISTENING_TASK_SPECS:
        remainder = rest[0].strip() if rest else ""
        if not remainder or remainder.lower() in {"prompt", "new", "题目", "题"}:
            return start_prompt(normalized)
        if remainder.lower().startswith("prompt"):
            _, hint = _parse_prompt_request(remainder[6:].strip())
            return start_prompt(normalized, hint)
        if remainder.lower().startswith("submit") or remainder.lower().startswith("answer"):
            answer_payload = remainder.split(None, 1)
            answer_text = answer_payload[1].strip() if len(answer_payload) > 1 else ""
            return submit_answers(answer_text)
        topic_hint = _normalize_topic_hint(remainder)
        if topic_hint:
            return start_prompt(normalized, topic_hint)
        return submit_answers(remainder)

    # 兜底：把整段当答案提交
    return submit_answers(text)
