"""Translation capability.

Gated by config.translation_enabled (source_lang != target_lang). When off, the
engine never calls into this module and never writes the `zh` field. When on,
behavior matches the legacy Google translate endpoint used by se_regressor.py.
"""

import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


class TranslationDisabled(Exception):
    """Raised if translate_* is invoked while translation is off."""


def translate_text(text, source_lang="en", target_lang="zh", timeout=8, retries=3):
    """Translate a single string via the Google gtx endpoint."""
    text = text.strip()
    if not text:
        return ""

    tl = _google_target_code(target_lang)
    sl = _google_source_code(source_lang)

    query = urllib.parse.urlencode({
        "client": "gtx",
        "sl": sl,
        "tl": tl,
        "dt": "t",
        "q": text,
    })
    url = "https://translate.googleapis.com/translate_a/single?" + query
    last_exc = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
            return "".join(part[0] for part in data[0] if part[0])
        except Exception as exc:
            last_exc = exc
            if attempt == retries - 1:
                raise
            time.sleep(attempt + 1)
    raise last_exc  # pragma: no cover


def _google_target_code(target_lang):
    # Google uses zh-CN for mainland Chinese.
    if target_lang.lower() in {"zh", "zh-cn", "zhcn"}:
        return "zh-CN"
    return target_lang


def _google_source_code(source_lang):
    return source_lang


def question_translation_texts(q):
    """Collect the topic + option texts that need translating for one question."""
    texts = [str(q.get("topic", "")).strip()]
    for choice in q.get("options", []):
        from .tui.render import split_option
        _, option_text = split_option(choice)
        texts.append(option_text.strip())
    return [text for text in texts if text]


def add_question_translation(q, translations):
    """Write the `zh` object onto q, mapping True/False specially."""
    from .tui.render import split_option
    zh_options = []
    for choice in q.get("options", []):
        label, option_text = split_option(choice)
        normalized = option_text.strip().lower()
        if normalized == "true":
            zh_options.append(label + "正确")
        elif normalized == "false":
            zh_options.append(label + "错误")
        else:
            zh_options.append(label + translations[option_text.strip()])
    q["zh"] = {
        "topic": translations[str(q.get("topic", "")).strip()],
        "options": zh_options,
    }


def translate_question_records(records, source_lang, target_lang,
                                force=False, batch_size=20, workers=8,
                                progress_callback=None):
    """Add zh fields to records that lack them (or all, if force).

    `records` is an iterable of (chapter, q) tuples. Mutates q in place.
    Returns (updated_count, failures).
    """
    from .tui.render import has_translation

    targets = [(chapter, q) for chapter, q in records if force or not has_translation(q)]
    if not targets:
        return 0, []

    translations = {}
    failures = []
    updated = 0
    completed = 0

    def chunks(items, size):
        for i in range(0, len(items), size):
            yield items[i:i + size]

    for batch in chunks(targets, batch_size):
        needed = []
        seen = set()
        for _, q in batch:
            for text in question_translation_texts(q):
                if text not in translations and text not in seen:
                    needed.append(text)
                    seen.add(text)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(translate_text, text, source_lang, target_lang): text
                       for text in needed}
            for future in as_completed(futures):
                text = futures[future]
                try:
                    translations[text] = future.result()
                except Exception as exc:
                    translations[text] = None
                    failures.append(("text", text, str(exc)))

        for chapter, q in batch:
            texts = question_translation_texts(q)
            missing = [text for text in texts if not translations.get(text)]
            if missing:
                failures.append((chapter, q.get("topic", ""), "missing translated text"))
            else:
                add_question_translation(q, translations)
                updated += 1
            completed += 1
            if progress_callback:
                progress_callback(completed, len(targets))

    return updated, failures
