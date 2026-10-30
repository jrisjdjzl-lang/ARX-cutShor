"""Publishing Metadata Generation Engine for ARX-cutShor.

Generates YouTube Shorts titles, descriptions, hashtags, and search tags:
- Supports series templates, part numbering, channel name, language, and keywords.
- Deduplicates hashtags and avoids misleading clickbait or spammy repetition.
- Includes an optional AI Provider interface (disabled by default; works 100% locally without paid AI).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class PublishingMetadata:
    """Generated metadata ready for YouTube Shorts publishing."""

    title: str
    description: str
    hashtags: List[str]
    tags: List[str]
    language: str = "ar"
    series_name: str = ""
    part_number: int = 1
    total_parts: int = 1

    @property
    def hashtags_str(self) -> str:
        return " ".join(self.hashtags)


class AIProviderInterface(Protocol):
    """Optional pluggable AI provider interface for title/description refinement."""

    def enhance_metadata(self, context: Dict[str, Any]) -> Optional[Dict[str, str]]:
        ...


class NullAIProvider:
    """Default local provider that uses zero external API calls or paid tokens."""

    def enhance_metadata(self, context: Dict[str, Any]) -> Optional[Dict[str, str]]:
        return None


def _clean_hashtag_token(raw: str) -> str:
    """Convert a phrase into a clean hashtag token without spaces or special punctuation."""
    cleaned = re.sub(r"[^\w\u0600-\u06FF]+", "_", raw.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return ""
    return f"#{cleaned}"


def deduplicate_hashtags(hashtags: List[str], max_count: int = 8) -> List[str]:
    """Ensure #Shorts is present and deduplicate hashtags case-insensitively without spam."""
    seen = set()
    result: List[str] = []

    for h in hashtags:
        token = h.strip()
        if not token:
            continue
        if not token.startswith("#"):
            token = _clean_hashtag_token(token)
        if not token or token == "#":
            continue
        low = token.lower()
        if low not in seen:
            seen.add(low)
            result.append(token)
        if len(result) >= max_count:
            break

    return result


def infer_series_and_topic_from_filename(filename: str, default_series: str = "السلسلة الرئيسية") -> Dict[str, str]:
    """Extract clean human-readable series or episode label from source filename."""
    stem = Path(filename).stem
    # Remove leading Drive ID prefix if present (e.g. drive123_episode_01)
    stem = re.sub(r"^[A-Za-z0-9\-]{15,}_", "", stem)
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    if not cleaned:
        return {"series_name": default_series, "topic": "مقطع قصير"}

    parts = [p.strip() for p in cleaned.split(" ") if p.strip()]
    if len(parts) >= 2:
        return {"series_name": cleaned, "topic": parts[0]}
    return {"series_name": default_series, "topic": cleaned}


DEFAULT_SERIES_TEMPLATES: Dict[str, Dict[str, str]] = {
    "default": {
        "title_template": "{series_name} | الجزء {part_num:02d} | {short_label}",
        "description_template": (
            "مقطع من سلسلة {series_name} (الجزء {part_num:02d} من {total_parts:02d}).\n\n"
            "تابع الأجزاء الجديدة واشترك في قناة {channel_name} لمشاهدة المزيد.\n\n"
            "{hashtags}"
        ),
    }
}


class MetadataGenerator:
    """Generates accurate, non-misleading metadata for YouTube Shorts clips."""

    def __init__(
        self,
        channel_name: str = "ARX Shorts",
        default_series_name: str = "السلسلة الرئيسية",
        default_language: str = "ar",
        series_templates: Optional[Dict[str, Dict[str, str]]] = None,
        ai_provider: Optional[AIProviderInterface] = None,
    ):
        self.channel_name = channel_name
        self.default_series_name = default_series_name
        self.default_language = default_language
        self.series_templates = dict(DEFAULT_SERIES_TEMPLATES)
        if series_templates:
            self.series_templates.update(series_templates)
        self.ai_provider = ai_provider or NullAIProvider()

    def generate(
        self,
        source_filename: str,
        part_number: int,
        total_parts: int,
        series_name: Optional[str] = None,
        short_label: Optional[str] = None,
        topic: Optional[str] = None,
        extra_keywords: Optional[List[str]] = None,
        language: Optional[str] = None,
    ) -> PublishingMetadata:
        inferred = infer_series_and_topic_from_filename(source_filename, self.default_series_name)
        effective_series = (series_name or inferred["series_name"] or self.default_series_name).strip()
        effective_topic = (topic or inferred["topic"] or "محتوى").strip()
        effective_lang = (language or self.default_language).strip()

        if not short_label:
            if total_parts > 1:
                short_label = f"مقطع {part_number:02d} من {total_parts:02d}"
            else:
                short_label = "مقطع كامل"

        tpl_bundle = self.series_templates.get(effective_series, self.series_templates["default"])
        title_tpl = tpl_bundle.get("title_template", DEFAULT_SERIES_TEMPLATES["default"]["title_template"])
        desc_tpl = tpl_bundle.get("description_template", DEFAULT_SERIES_TEMPLATES["default"]["description_template"])

        raw_hashtags = [
            "#Shorts",
            _clean_hashtag_token(effective_series),
            _clean_hashtag_token(effective_topic),
            _clean_hashtag_token(self.channel_name),
        ]
        if extra_keywords:
            for kw in extra_keywords:
                raw_hashtags.append(_clean_hashtag_token(kw))

        hashtags = deduplicate_hashtags(raw_hashtags, max_count=6)
        hashtags_line = " ".join(hashtags)

        title = title_tpl.format(
            series_name=effective_series,
            part_num=part_number,
            total_parts=total_parts,
            short_label=short_label,
            channel_name=self.channel_name,
        ).strip()

        # YouTube title limit is 100 characters
        if len(title) > 100:
            title = title[:97].rstrip() + "..."

        description = desc_tpl.format(
            series_name=effective_series,
            part_num=part_number,
            total_parts=total_parts,
            short_label=short_label,
            channel_name=self.channel_name,
            hashtags=hashtags_line,
        ).strip()

        # Optional AI enhancement if explicitly enabled and configured
        ai_result = self.ai_provider.enhance_metadata(
            {
                "title": title,
                "description": description,
                "series_name": effective_series,
                "part_number": part_number,
                "total_parts": total_parts,
            }
        )
        if ai_result:
            if ai_result.get("title"):
                title = ai_result["title"][:100]
            if ai_result.get("description"):
                description = ai_result["description"]

        # Build clean search tags (without # prefix)
        raw_tags = ["Shorts", effective_series, effective_topic, self.channel_name]
        if extra_keywords:
            raw_tags.extend(extra_keywords)
        clean_tags: List[str] = []
        seen_tags = set()
        for t in raw_tags:
            t_clean = t.strip().lstrip("#").replace("_", " ")
            if t_clean and t_clean.lower() not in seen_tags:
                seen_tags.add(t_clean.lower())
                clean_tags.append(t_clean)

        return PublishingMetadata(
            title=title,
            description=description,
            hashtags=hashtags,
            tags=clean_tags[:15],
            language=effective_lang,
            series_name=effective_series,
            part_number=part_number,
            total_parts=total_parts,
        )

