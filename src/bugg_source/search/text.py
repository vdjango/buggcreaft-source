"""One versioned tokenizer/cleaner/chunker for indexing and natural queries."""

import hashlib
import re
import unicodedata
from functools import lru_cache

import jieba
from markdown_it import MarkdownIt

from mc_translate.content import HtmlText

TEXT_REVISION = "jieba-0.42.1-mc2-and-clean2-char256-overlap32"
STOP = frozenset(
    (
        "的 了 是 在 和 与 或 我 想 要 一个 可以 能 让 怎么 如何 有 用 将 把 就 都 也 "
        "时 里 再 去 中 并 等"
    ).split()
)


@lru_cache(maxsize=1)
def tokenizer():
    instance = jieba.Tokenizer()
    instance.initialize()
    for word in ("模组", "光影", "整合包", "资源包", "物品栏", "自动分类", "拔刀剑", "小地图"):
        instance.add_word(word)
    return instance


def terms(value: str, *, query: bool = False) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    segments = tokenizer().cut(normalized) if query else tokenizer().cut_for_search(normalized)
    words = list(
        dict.fromkeys(
            word.strip()
            for word in segments
            if word.strip() and word not in STOP and re.search(r"\w", word)
        )
    )
    # Preserve IDs, and support short unknown Chinese phrases without scanning bodies.
    words.extend(re.findall(r"[a-z0-9]+(?:[_-][a-z0-9]+)+", normalized))
    if not query:
        for phrase in re.findall(r"[\u4e00-\u9fff]+", normalized):
            words.extend(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return list(dict.fromkeys(words))[: 32 if query else 2048]


def clean(value: str, format: str) -> str:
    html = MarkdownIt("commonmark", {"html": True}).render(value) if format == "markdown" else value
    if format != "plain":
        parser = HtmlText()
        parser.feed(html)
        html = "".join(parser.parts)
    return re.sub(r"[ \t]+", " ", html).strip()


def chunks(payload: dict) -> tuple[list[dict], dict]:
    result = []
    truncated = False
    body_languages = []
    budget = 200000
    for document in payload.get("documents", []):
        text = clean(document["text"][:400000], document["format"])
        permitted = min(len(text), max(0, budget))
        truncated |= len(text) > permitted or len(document["text"]) > 400000
        text = text[:permitted]
        budget -= permitted
        if document["field"] == "body" and text:
            body_languages.append(document["language"])
        for ordinal, start in enumerate(range(0, len(text), 224)):
            part = text[start : start + 256]
            if not part.strip():
                continue
            result.append(
                dict(
                    field=document["field"],
                    language=document["language"],
                    is_translation=document["is_translation"],
                    source_hash=document["source_hash"],
                    document_revision=document["revision"],
                    ordinal=ordinal,
                    start_char=start,
                    text=part,
                    tokens=" ".join(terms(part)),
                )
            )
    return result, dict(
        truncated=bool(truncated),
        body_languages=sorted(set(body_languages)),
        chunks=len(result),
        body_available=bool(body_languages),
    )


def config_revision(model_revision: str) -> str:
    return hashlib.sha256(
        (TEXT_REVISION + "|" + model_revision + "|rrf60-window200").encode()
    ).hexdigest()


def snippet(text: str, query: str) -> dict:
    text = re.sub(r"\s+", " ", text).strip()
    matches = []
    for word in terms(query, query=True):
        matches.extend((m.start(), m.end()) for m in re.finditer(re.escape(word), text, re.I))
    offset = max(0, min((start for start, _ in matches), default=0) - 40)
    end = min(len(text), offset + 180)
    intervals = []
    for start, stop in sorted(matches):
        start, stop = max(start, offset) - offset, min(stop, end) - offset
        if stop <= start:
            continue
        if intervals and start <= intervals[-1]["end"]:
            intervals[-1]["end"] = max(intervals[-1]["end"], stop)
        else:
            intervals.append(dict(start=start, end=stop))
    return dict(text=text[offset:end], highlights=intervals)
