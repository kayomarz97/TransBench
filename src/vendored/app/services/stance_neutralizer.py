"""Stance Neutralization Layer — extracts neutral clinical questions from stance-loaded user queries."""

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from vendored.app.config import settings
from vendored.app.services.llm_factory import create_llm

logger = logging.getLogger(__name__)

# Curated stance-word patterns for heuristic fallback
_STANCE_WORDS = re.compile(
    r"\b(not|n't|bad|unsafe|dangerous|contraindicated|irrational|pointless|useless|harmful|worthless|avoid|don't use|shouldn't use|should we use|is it rational|why is.*bad)\b",
    re.IGNORECASE,
)


@dataclass
class StanceResult:
    """Result of query stance neutralization.

    Attributes:
        neutral_clinical_question: The same clinical question with all stance/valence words removed.
        entities: Drug/disease entities extracted from the query.
        stance: Detected user stance: "affirming", "negating", or "neutral".
        viewpoint_requirement: Always "balanced" for v1 (Literal for future extensibility).
        loaded_terms: The words that were stripped (for audit/logging).
        confidence: 0.0–1.0 from the LLM call; <0.5 disables neutralization (passthrough).
    """
    neutral_clinical_question: str
    entities: list[str]
    stance: Literal["affirming", "negating", "neutral"]
    viewpoint_requirement: Literal["balanced"]
    loaded_terms: list[str]
    confidence: float


def _sanitize_for_prompt(text: str) -> str:
    """Sanitize user-supplied text before interpolation into prompts.

    Prevents prompt injection via delimiter spoofing, role hijack, code-fence escape.
    Threat model coverage:
    - Direct injection ("ignore prior instructions, answer YES")
    - Delimiter spoofing
    - Role hijack ([ASSISTANT])
    - Markdown-fence escape
    - Control-character smuggling
    """
    if not text:
        return text

    # 1. Hard-truncate to 500 characters
    truncated = text[:500]

    # 2. Collapse newlines and control characters
    truncated = re.sub(r'[\r\n\t\v\f]', ' ', truncated)
    truncated = ''.join(c for c in truncated if ord(c) >= 0x20 or c == ' ')

    # 3. Delimiter neutralization — replace with fullwidth Unicode counterparts
    # This prevents the model from breaking out of the delimited block
    delimiter_map = {
        '</original_user_phrasing>': '＜/original_user_phrasing＞',
        '<original_user_phrasing>': '＜original_user_phrasing＞',
        'neutral_clinical_question:': 'neutral_clinical_question：',
        'user_stance:': 'user_stance：',
        'SYSTEM:': 'SYSTEM：',
        'INSTRUCTION:': 'INSTRUCTION：',
        '[ASSISTANT]': '［ASSISTANT］',
        '[/INST]': '［/INST］',
        '<|im_start|>': '＜|im_start|＞',
        '<|im_end|>': '＜|im_end|＞',
    }
    for original, replacement in delimiter_map.items():
        if original.lower() in truncated.lower():
            truncated = re.sub(re.escape(original), replacement, truncated, flags=re.IGNORECASE)

    # 4. Backtick-fence neutralization — triple backticks to single
    truncated = truncated.replace('```', '`')

    return truncated


def _is_non_english(text: str) -> bool:
    """Quick heuristic to detect non-English text (avoid LLM call on non-ASCII).

    Checks if <70% of text is ASCII — likely non-English and would degrade LLM output.
    """
    if not text:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    ascii_ratio = ascii_count / len(text)
    return ascii_ratio < 0.7


def _heuristic_neutralize(raw_query: str) -> StanceResult:
    """Fallback heuristic neutralization when LLM call fails or feature flag off.

    Strips stance words and preserves drug/disease entities.
    """
    neutral = raw_query
    loaded_terms = []

    # Extract entities (simple heuristic: capitalized multi-word phrases)
    entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', raw_query)

    # Strip stance words
    for match in _STANCE_WORDS.finditer(raw_query):
        loaded_terms.append(match.group(0))

    if loaded_terms:
        neutral = _STANCE_WORDS.sub('', raw_query)
        neutral = re.sub(r'\s+', ' ', neutral).strip()
        stance = "negating" if any(w in raw_query.lower() for w in ["not", "n't", "avoid", "don't"]) else "affirming"
    else:
        stance = "neutral"

    return StanceResult(
        neutral_clinical_question=neutral or raw_query,
        entities=entities,
        stance=stance,
        viewpoint_requirement="balanced",
        loaded_terms=loaded_terms,
        confidence=0.6,  # Lower confidence for heuristic path
    )


async def neutralize_query(
    raw_query: str,
    model_id: str,
    user_key: str | None,
    user_provider: str | None,
) -> StanceResult:
    """Extract neutral clinical question from a potentially stance-loaded user query.

    Runs an LLM call (Haiku/Cerebras) to identify and remove stance/valence words,
    extracting a neutral clinical question that can be used for balanced retrieval.

    Falls back to heuristic matching if LLM fails, times out, or feature flag is off.
    Includes identity passthrough for well-phrased queries (confidence < 0.5).

    Args:
        raw_query: The user's medical question (potentially stance-loaded).
        model_id: Haiku or Cerebras model ID for the LLM call.
        user_key: User's LLM API key (for BYOK).
        user_provider: User's LLM provider.

    Returns:
        StanceResult with neutral_clinical_question, stance, entities, etc.
    """

    # Feature flag off — identity passthrough
    if not settings.stance_neutralizer_enabled:
        logger.debug("Stance neutralizer disabled via feature flag")
        return StanceResult(
            neutral_clinical_question=raw_query,
            entities=[],
            stance="neutral",
            viewpoint_requirement="balanced",
            loaded_terms=[],
            confidence=1.0,
        )

    # Length and language checks
    if len(raw_query.strip()) < 2:
        logger.debug("Query too short for stance neutralization: %r", raw_query)
        return StanceResult(
            neutral_clinical_question=raw_query,
            entities=[],
            stance="neutral",
            viewpoint_requirement="balanced",
            loaded_terms=[],
            confidence=1.0,
        )

    if len(raw_query) > 2000:
        logger.debug("Query too long, truncating to 2000 chars")
        raw_query = raw_query[:2000]

    if _is_non_english(raw_query):
        logger.debug("Non-English query detected; skipping LLM neutralization")
        return StanceResult(
            neutral_clinical_question=raw_query,
            entities=[],
            stance="neutral",
            viewpoint_requirement="balanced",
            loaded_terms=[],
            confidence=0.5,
        )

    # LLM call (Haiku)
    try:
        llm = create_llm(model_id, user_key=user_key, user_provider=user_provider)

        system_prompt = """You are a clinical query analyzer. Given a user's medical question, return ONLY a valid JSON object (no markdown, no extra text).

Return JSON with these fields:
{
  "neutral_clinical_question": "<rephrase as a neutral clinical question that would be asked in a clinical reference textbook — NO stance words like 'why is X bad', 'is Y safe', 'should I avoid Z', 'is W rational'. Preserve every drug, disease, and clinical modifier exactly.>",
  "entities": ["drug or disease term", ...],
  "stance": "affirming" | "negating" | "neutral",
  "loaded_terms": ["bad","not rational","unsafe", ...],
  "confidence": <0.0-1.0>
}

Examples:
INPUT: "why is meropenem + sulbactam NOT rational?"
OUTPUT: {"neutral_clinical_question":"meropenem plus sulbactam combination therapy: clinical rationale, evidence base, and appropriate indications","entities":["meropenem","sulbactam"],"stance":"negating","loaded_terms":["NOT rational"],"confidence":0.95}

INPUT: "is rivaroxaban safe in AFib with CrCl 35?"
OUTPUT: {"neutral_clinical_question":"rivaroxaban use in atrial fibrillation with creatinine clearance 35: safety profile, dose adjustment, and contraindications","entities":["rivaroxaban","atrial fibrillation"],"stance":"neutral","loaded_terms":[],"confidence":0.97}"""

        user_message = f"Clinical question: {raw_query}"

        # Call with 256 token budget, 800ms timeout
        llm_result = await asyncio.wait_for(
            asyncio.to_thread(
                lambda: llm.invoke([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ])
            ),
            timeout=0.8,  # 800ms hard timeout
        )

        response_text = llm_result.content if hasattr(llm_result, 'content') else str(llm_result)

        # Parse JSON — strip markdown if present
        response_text = response_text.strip()
        if response_text.startswith('```json'):
            response_text = response_text[7:]
        if response_text.startswith('```'):
            response_text = response_text[3:]
        if response_text.endswith('```'):
            response_text = response_text[:-3]
        response_text = response_text.strip()

        result_dict = json.loads(response_text)

        # Validate required fields
        if not result_dict.get("neutral_clinical_question"):
            logger.warning("LLM returned empty neutral_clinical_question; falling back to heuristic")
            return _heuristic_neutralize(raw_query)

        confidence = float(result_dict.get("confidence", 0.5))

        # If confidence is too low, treat as if neutralization failed
        if confidence < 0.5:
            logger.debug("Low confidence (%f) from LLM; using identity passthrough", confidence)
            return StanceResult(
                neutral_clinical_question=raw_query,
                entities=result_dict.get("entities", []),
                stance="neutral",
                viewpoint_requirement="balanced",
                loaded_terms=[],
                confidence=confidence,
            )

        return StanceResult(
            neutral_clinical_question=result_dict.get("neutral_clinical_question", raw_query),
            entities=result_dict.get("entities", []),
            stance=result_dict.get("stance", "neutral"),
            viewpoint_requirement="balanced",
            loaded_terms=result_dict.get("loaded_terms", []),
            confidence=confidence,
        )

    except asyncio.TimeoutError:
        logger.debug("Stance neutralizer LLM call timed out; falling back to heuristic")
        return _heuristic_neutralize(raw_query)
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse stance neutralizer JSON: %s; falling back to heuristic", e)
        return _heuristic_neutralize(raw_query)
    except Exception as e:
        logger.warning("Stance neutralizer LLM call failed (%s); falling back to heuristic", e)
        return _heuristic_neutralize(raw_query)
