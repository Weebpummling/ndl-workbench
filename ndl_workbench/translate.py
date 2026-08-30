"""Translate the chunked Japanese transcription into an English markdown file.

The translation is a machine reading of a machine reading: NDL's OCR is
uncorrected, and this pass does not pretend otherwise. The system prompt tells
the model to mark what it cannot read rather than invent a plausible sentence,
and the file header says so to whoever opens the result.

Chunks are sent one at a time with a stable system prompt so the prompt cache
carries the instructions across the whole volume.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import Settings

SYSTEM_PROMPT = """\
You are translating a historical Japanese document into English for a research \
archive. The Japanese you receive is UNCORRECTED machine OCR from the National \
Diet Library, frame by frame, and it has three known defects you must handle \
rather than paper over:

1. Furigana (ruby readings) are interleaved into the text as separate lines. \
Treat them as reading aids for the line that follows and do not translate them \
as if they were sentences.
2. The geta mark 〓 stands for a glyph the OCR could not read. Render it as \
[illegible].
3. Old-form kanji, worn letterpress and vertical-column scrambling produce real \
misreads, especially in tables of contents and fold-out tables.

Rules:

- Translate faithfully into clear modern English prose. Keep the period register \
of an official document; do not modernise the tone or editorialise.
- Preserve the structure of each frame: keep every "### Frame N" heading, and \
under it state briefly what the frame is (a chapter opening, a plate, front \
matter, a blank page) before the translation.
- Where the source is a table or a list, render it as a markdown table or list.
- Where the OCR will not support a confident reading, say so inline with \
[OCR uncertain] and translate what the text does support. Never invent content \
to fill a gap, and never present a reconstruction as if it were legible.
- Give an important Japanese term in the original script in parentheses the \
first time it appears in a chapter, e.g. sergeant major (曹長).
- Output GitHub-flavoured markdown only. No preamble, no closing remarks.
"""

USER_TEMPLATE = """\
Volume: {title}
Published: {published}
NDL pid: {pid}

Translate the frames below. This is chunk {n} of {total}.

---

{chunk}
"""


@dataclass
class TranslationResult:
    markdown_path: Path
    chunks_done: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int


class TranslationUnavailable(RuntimeError):
    """No SDK, or no credentials - the caller should offer the manual route."""


def _client(settings: Settings):
    try:
        import anthropic  # noqa: F401
    except ImportError as e:  # pragma: no cover - packaging guard
        raise TranslationUnavailable(
            "The anthropic package is not installed in this build, so automatic "
            "translation is unavailable. The chunk files are still written and "
            "can be translated by hand or by any other tool."
        ) from e
    import anthropic

    key = (settings.anthropic_api_key or "").strip()
    try:
        # No key here means the SDK resolves credentials itself: the
        # ANTHROPIC_API_KEY environment variable, or an `ant auth login`
        # profile. An unset variable does not mean there are no credentials.
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    except Exception as e:
        raise TranslationUnavailable(f"Could not construct the Anthropic client: {e}") from e


def header_markdown(pid: str, title: str, published: str, publisher: str,
                    frames: int, page_map_note: str, retrieved: str) -> str:
    """The provenance block that opens every translated volume."""
    return f"""# {title} — English translation

| | |
|---|---|
| Source | NDL Digital Collections, PID {pid} — https://dl.ndl.go.jp/pid/{pid} |
| Title | {title} |
| Publisher | {publisher} {published} |
| Rights | Public domain · attribution 国立国会図書館 (National Diet Library) |
| Basis | English translation of NDL's uncorrected machine OCR (Next-Gen Digital Library fulltext API). Companion file: `{pid}_transcription_ja.txt` holds the Japanese original, frame by frame. |
| Frames | {frames} |
| Page mapping | {page_map_note} |
| Retrieved / translated | {retrieved} |

**Caveats.** The underlying text is machine OCR and was not human-corrected. 〓 marked
glyphs the OCR could not read and appears here as [illegible]; passages the translation
could not read confidently are marked [OCR uncertain]. Fold-out tables and dense
advertisement pages are the least reliable. For scholarly citation, verify against the
page image at the frame URL.

---

"""


def translate_chunks(
    chunk_paths: list[Path],
    out_md: Path,
    *,
    settings: Settings,
    pid: str,
    title: str,
    published: str,
    header: str = "",
    log: Callable[[str], None] = lambda _m: None,
    should_cancel: Callable[[], bool] = lambda: False,
    progress: Callable[[int, int], None] = lambda _d, _t: None,
) -> TranslationResult:
    """Translate every chunk and write one markdown file.

    Partial work survives cancellation: the file is written after each chunk,
    so stopping half way leaves a usable file rather than nothing.
    """
    import anthropic

    client = _client(settings)
    total = len(chunk_paths)
    out_md.parent.mkdir(parents=True, exist_ok=True)

    body: list[str] = [header] if header else []
    in_tok = out_tok = cache_tok = 0
    done = 0

    for i, path in enumerate(chunk_paths, start=1):
        if should_cancel():
            log("cancelled - keeping what was translated so far")
            break

        chunk = path.read_text(encoding="utf-8-sig")
        frames = re.findall(r"^=== Frame (\d+) ===", chunk, re.M)
        span = f"frames {frames[0]}-{frames[-1]}" if frames else path.stem
        log(f"translating chunk {i}/{total} ({span}) ...")

        try:
            # Streaming: a chunk of 15 frames can run long, and a large
            # max_tokens on a non-streaming call risks an HTTP timeout.
            with client.messages.stream(
                model=settings.model,
                max_tokens=64000,
                system=[{
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                thinking={"type": "adaptive"},
                output_config={"effort": settings.effort},
                messages=[{
                    "role": "user",
                    "content": USER_TEMPLATE.format(
                        title=title, published=published, pid=pid,
                        n=i, total=total, chunk=chunk,
                    ),
                }],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise TranslationUnavailable(
                "Anthropic rejected the credentials. Set an API key in Settings, "
                "or export ANTHROPIC_API_KEY, or run `ant auth login`."
            ) from e
        except anthropic.RateLimitError as e:
            raise RuntimeError(
                f"Rate limited on chunk {i}. Wait and re-run - finished chunks "
                f"are already written to {out_md.name}."
            ) from e
        except anthropic.APIStatusError as e:
            raise RuntimeError(f"API error on chunk {i} (HTTP {e.status_code}): {e.message}") from e
        except anthropic.APIConnectionError as e:
            raise RuntimeError(f"Network error on chunk {i}: {e}") from e

        if message.stop_reason == "refusal":
            detail = getattr(message, "stop_details", None)
            raise RuntimeError(
                f"The model declined chunk {i}"
                + (f" ({detail.category})" if detail and getattr(detail, "category", None) else "")
                + ". Translate this chunk by hand, or re-run after checking the source text."
            )

        text = "".join(b.text for b in message.content if b.type == "text").strip()
        body.append(text + "\n\n")

        u = message.usage
        in_tok += u.input_tokens or 0
        out_tok += u.output_tokens or 0
        cache_tok += (getattr(u, "cache_read_input_tokens", 0) or 0)

        out_md.write_text("".join(body), encoding="utf-8")
        done = i
        progress(i, total)

    return TranslationResult(out_md, done, in_tok, out_tok, cache_tok)
