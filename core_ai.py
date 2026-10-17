# -*- coding: utf-8 -*-
"""AI 调用 + 答案解析。原理：把题目文本/JSON 发给 OpenAI 兼容接口，再按约定格式拆题。"""
from __future__ import annotations

import json
import re
from typing import Any

import requests

DEFAULT_PROMPTS = {
    "flash": (
        "你是闪电侠答题助手。必须逐题作答，禁止总结 JSON 结构。\n"
        "【输出格式】\n"
        "1. 每题之间单独一行写 ---\n"
        "2. 每题第一行必须是：题号.[题型] 答案\n"
        "   例：1.[单选] A\n"
        "       2.[多选] ACD\n"
        "       3.[判断] 对\n"
        "       4.[填空] 答案文本\n"
        "3. 不要第二行解析，不要开场白、结束语、代码块。"
    ),
    "explain": (
        "你是详解学长。必须逐题作答，禁止总结 JSON 结构。\n"
        "【输出格式】\n"
        "1. 每题之间单独一行写 ---\n"
        "2. 第一行：题号.[题型] 答案（如 1.[单选] B）\n"
        "3. 第二行起：1-3 句解析\n"
        "4. 多选字母连写（ACD），判断用 对/错，不要开场白。"
    ),
}

def build_web_ai_pack(questions: str, profile: str = "explain", slice_info: str = "") -> str:
    """拼成可直接粘贴到网页版 ChatGPT / DeepSeek / 豆包 的完整提示。"""
    system = DEFAULT_PROMPTS.get(profile) or DEFAULT_PROMPTS["explain"]
    extra = f"\n{slice_info}\n" if slice_info else "\n"
    body = (questions or "").strip()
    return (
        f"{system}\n"
        f"{extra}"
        "请根据以下题目 JSON 或文本逐题作答，严格遵守上面的输出格式：\n\n"
        f"{body}\n"
    )


PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
    },
    "openai": {
        "label": "OpenAI / 兼容接口",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "lmstudio": {
        "label": "本地 LM Studio",
        "base_url": "http://127.0.0.1:1234/v1",
        "model": "local-model",
    },
    "llamacpp": {
        "label": "本地 llama-server",
        "base_url": "http://127.0.0.1:9080/v1",
        "model": "Qwen2.5-7B-Instruct-Q4_K_M",
    },
}


def chat_completions(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: int = 180,
) -> str:
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        if url.endswith("/v1"):
            url = url + "/chat/completions"
        else:
            url = url + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    body = {
        "model": model or "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"无法解析模型返回: {data}") from exc


ANSWER_HEAD = re.compile(
    r"^\s*(\d+)\s*[\.、．]\s*\[([^\]]+)\]\s*(.*)$"
)
ANSWER_HEAD_SIMPLE = re.compile(
    r"^\s*(\d+)\s*[\.、．]\s*([A-Za-z]{1,8}|对|错|正确|错误|True|False|√|×)\s*(.*)$",
    re.I,
)


def parse_answers(text: str) -> list[dict[str, Any]]:
    """把模型输出拆成 [{index, type, answer, explain}, ...]。"""
    raw = (text or "").replace("\r\n", "\n").strip()
    raw = re.sub(r"\n[ \t]*-{3,}[ \t]*\n", "\n---\n", raw)
    if not raw:
        return []

    chunks = [c.strip() for c in re.split(r"\n\s*---\s*\n", raw) if c.strip()]
    if len(chunks) == 1:
        lines = raw.split("\n")
        buf, chunks = [], []
        for line in lines:
            if re.match(r"^\s*\d+\s*[\.、．]", line) and buf:
                chunks.append("\n".join(buf).strip())
                buf = [line]
            else:
                buf.append(line)
        if buf:
            chunks.append("\n".join(buf).strip())

    items = []
    for i, chunk in enumerate(chunks, 1):
        lines = [ln for ln in chunk.split("\n") if ln.strip() and not re.match(r"^-{3,}$", ln.strip())]
        if not lines:
            continue
        first = lines[0].strip()
        m = ANSWER_HEAD.match(first) or ANSWER_HEAD_SIMPLE.match(first)
        if m:
            idx = int(m.group(1))
            if ANSWER_HEAD.match(first):
                qtype = m.group(2).strip()
                answer = (m.group(3) or "").strip()
            else:
                answer = m.group(2).strip()
                rest = (m.group(3) or "").strip()
                qtype = _guess_type(answer)
                if rest and not answer:
                    answer = rest
            explain = "\n".join(lines[1:]).strip()
        else:
            idx = i
            qtype = "未知"
            answer = first
            explain = "\n".join(lines[1:]).strip()
        items.append({"index": idx, "type": qtype, "answer": answer, "explain": explain})
    return items


def _guess_type(answer: str) -> str:
    a = answer.strip()
    if re.fullmatch(r"[A-Za-z]{2,}", a):
        return "多选"
    if re.fullmatch(r"[A-Za-z]", a):
        return "单选"
    if re.fullmatch(r"(对|错|正确|错误|True|False|√|×)", a, re.I):
        return "判断"
    return "填空"


def looks_like_questions(obj: Any) -> bool:
    if isinstance(obj, dict) and (
        "combinedQuestions" in obj or "ChoiceItems" in obj or "QuestionContent" in obj
    ):
        return True
    text = json.dumps(obj, ensure_ascii=False) if not isinstance(obj, str) else obj
    keys = text.lower()
    hits = 0
    for kw in ("question", "option", "题", "选项", "choice", "stem", "answer", "tigan", "choiceitems"):
        if kw in keys:
            hits += 1
    return hits >= 2 or ("A" in text and "B" in text and ("下列" in text or "哪" in text))


def flatten_ote_questions(obj: Any) -> list[dict] | None:
    """常见题库 start 接口：combinedQuestions 结构。"""
    if isinstance(obj, dict) and isinstance(obj.get("combinedQuestions"), list):
        raw = obj["combinedQuestions"]
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict) and "ChoiceItems" in obj[0]:
        raw = obj
    else:
        return None
    out = []
    for i, q in enumerate(raw, 1):
        qtype = q.get("QuestionType") or ""
        zh = {"SingleChoice": "单选", "MultiChoice": "多选", "Judge": "判断", "FillIn": "填空"}.get(qtype, qtype)
        opts = []
        # 判断题选项在 JudgeItems，单选多选在 ChoiceItems
        items = q.get("ChoiceItems") or q.get("JudgeItems") or []
        for it in items:
            opts.append({
                "code": it.get("ItemCode") or "",
                "text": it.get("ItemContent") or "",
                "id": it.get("ID") or "",
            })
        out.append({
            "index": q.get("OrderIndex") or i,
            "type": zh,
            "stem": q.get("QuestionContent") or "",
            "options": opts,
        })
    return out


def parse_question_payload(text: str) -> list[dict]:
    raw = (text or "").strip()
    if not raw:
        return []
    obj: Any = None
    try:
        obj = json.loads(raw)
    except Exception:
        obj = None
    if obj is not None:
        flat = flatten_ote_questions(obj)
        if flat:
            return flat
        if isinstance(obj, list) and obj and isinstance(obj[0], dict) and (
            "stem" in obj[0] or "options" in obj[0] or "questionContent" in obj[0]
        ):
            out = []
            for i, q in enumerate(obj, 1):
                opts = q.get("options") or []
                norm_opts = []
                for it in opts:
                    if isinstance(it, dict):
                        norm_opts.append({
                            "code": it.get("code") or it.get("letter") or it.get("ItemCode") or "",
                            "text": it.get("text") or it.get("ItemContent") or "",
                        })
                    else:
                        norm_opts.append({"code": "", "text": str(it)})
                out.append({
                    "index": q.get("index") or q.get("n") or i,
                    "type": q.get("type") or "",
                    "stem": q.get("stem") or q.get("questionContent") or q.get("QuestionContent") or "",
                    "options": norm_opts,
                })
            return out
    return []


def format_qa_document(questions: list[dict], answers: list[dict], raw_ai: str = "") -> str:
    ans_map = {int(a.get("index") or i + 1): a for i, a in enumerate(answers or [])}
    lines = ["题目与答案", "=" * 20]
    if questions:
        for i, q in enumerate(questions, 1):
            idx = int(q.get("index") or i)
            a = ans_map.get(idx) or {}
            qtype = a.get("type") or q.get("type") or ""
            lines.append("")
            lines.append(f"{idx}.[{qtype}]" if qtype else f"{idx}.")
            stem = (q.get("stem") or "").strip()
            if stem:
                lines.append(stem)
            for opt in q.get("options") or []:
                code = (opt.get("code") or "").strip()
                text = (opt.get("text") or "").strip()
                if code or text:
                    lines.append(f"{code}. {text}".strip() if code else text)
            if a.get("answer"):
                lines.append(f"答案：{a.get('answer')}")
            if a.get("explain"):
                lines.append(f"解析：{a.get('explain')}")
    else:
        for i, a in enumerate(answers or [], 1):
            lines.append("")
            lines.append(f"{a.get('index') or i}.[{a.get('type') or ''}] {a.get('answer') or ''}".strip())
            if a.get("explain"):
                lines.append(f"解析：{a.get('explain')}")
        if raw_ai and not answers:
            lines.append("")
            lines.append(raw_ai)
    return "\n".join(lines).strip() + "\n"


def split_question_text(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    parts = re.split(r"(?=^\s*\d+\s*[\.、．]\s*)", raw, flags=re.M)
    parts = [p.strip() for p in parts if p.strip()]
    return parts if len(parts) > 1 else [raw]


def slice_payload(text: str, slice_size: int) -> list[tuple[int, int, str]]:
    """切成若干段，返回 [(start_no, end_no, payload), ...]。"""
    size = max(1, int(slice_size or 5))
    qs = parse_question_payload(text)
    if qs:
        chunks = []
        for i in range(0, len(qs), size):
            part = qs[i:i + size]
            start = int(part[0].get("index") or (i + 1))
            end = int(part[-1].get("index") or (i + len(part)))
            chunks.append((start, end, json.dumps(part, ensure_ascii=False)))
        return chunks
    parts = split_question_text(text)
    if len(parts) <= 1:
        return [(1, 1, text)]
    chunks = []
    for i in range(0, len(parts), size):
        part = parts[i:i + size]
        start = i + 1
        end = i + len(part)
        chunks.append((start, end, "\n\n".join(part)))
    return chunks


def compact_payload(obj: Any, limit: int = 60000) -> str:
    flat = flatten_ote_questions(obj)
    if flat:
        s = json.dumps(flat, ensure_ascii=False)
    elif isinstance(obj, str):
        s = obj
    else:
        s = json.dumps(obj, ensure_ascii=False)
    return s[:limit]
