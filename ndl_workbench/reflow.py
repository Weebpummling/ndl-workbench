"""Turn NDL's line-by-line OCR into prose a translator can actually read.

The transcription this project writes is faithful to the scan: one output line
per line NDL detected on the page. That is the right shape for checking a
reading against the image, and the wrong shape for feeding to a translator, for
two reasons.

**Ruby.** These volumes are printed with furigana beside most kanji, and NDL's
OCR emits each ruby column as its own line, interleaved with the body text. A
translator handed that sees a sentence, then a meaningless fragment of katakana,
then more sentence. It produces mush.

**Hard wrapping.** Every body line is a column-width fragment, not a sentence.
Given one fragment at a time a translator translates each as a standalone
utterance and the grammar falls apart.

So this module rebuilds paragraphs: drop the ruby, join the fragments, break at
sentence ends. Ruby is identified from the line geometry NDL ships with the
text - a ruby column is markedly thinner than a body column - rather than by
guessing from the characters, so the test does not care whether a given reading
happens to look like a word.

Nothing here edits the characters themselves. Lines are dropped or joined; what
survives is exactly what NDL read, including the geta mark for glyphs it could
not resolve.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

# A line ends a sentence if it ends with one of these.
_SENTENCE_END = "。．.!?！？"
# ...and these close a quotation that may follow the stop.
_CLOSERS = "」』）)"

# A line that is only a numbered item marker, e.g. 三、 or （二）
_ITEM_MARKER = re.compile(r"^[（(]?[一二三四五六七八九十百〇0-9]+[）)、.]?$")
# A line that begins one, e.g. 三、軍人ハ...
_STARTS_ITEM = re.compile(r"^[（(]?[一二三四五六七八九十百]+[）)、]")


@dataclass
class Line:
    text: str
    thickness: float   # column width for vertical text, line height for horizontal
    length: float = 0.0   # the long dimension: how far the line ran before stopping
    is_ruby: bool = False


def _thickness(line: dict[str, Any]) -> float:
    """The short dimension of the line's box.

    For vertical text a line is a tall thin column and its thickness is the
    width; for horizontal text it is the height. Taking the short side handles
    both without needing to know the page's orientation up front.
    """
    w = float(line.get("xmax", 0)) - float(line.get("xmin", 0))
    h = float(line.get("ymax", 0)) - float(line.get("ymin", 0))
    return w if h >= w else h


def _length(line: dict[str, Any]) -> float:
    """The long dimension: how far the line ran before it stopped."""
    w = float(line.get("xmax", 0)) - float(line.get("xmin", 0))
    h = float(line.get("ymax", 0)) - float(line.get("ymin", 0))
    return max(w, h)


def _otsu(values: list[float]) -> float:
    """Threshold that best splits one list of numbers into two groups.

    Otsu's method: try every boundary and keep the one that maximises the
    variance between the two groups. One dimension, a few hundred values, no
    dependencies.
    """
    ordered = sorted(values)
    n = len(ordered)
    total = sum(ordered)
    best_split, best_score = ordered[0], -1.0
    run = 0.0
    for i in range(1, n):
        run += ordered[i - 1]
        w0, w1 = i, n - i
        m0 = run / w0
        m1 = (total - run) / w1
        score = w0 * w1 * (m0 - m1) ** 2
        if score > best_score:
            best_score, best_split = score, (ordered[i - 1] + ordered[i]) / 2
    return best_split


def classify(raw_lines: list[dict[str, Any]], *, min_lines: int = 6,
             min_separation: float = 1.7) -> list[Line]:
    """Mark each line as ruby or body from its geometry.

    The split is only applied when the two groups are genuinely far apart. A
    page with no furigana at all - a plate, a table of contents, an
    advertisement - has one cluster, and splitting it would throw away real
    text. In that case everything is body.
    """
    lines = [Line(str(l.get("contenttext", "")), _thickness(l), _length(l)) for l in raw_lines]
    usable = [l for l in lines if l.text.strip()]
    if len(usable) < min_lines:
        return lines

    thicknesses = [l.thickness for l in usable]
    if max(thicknesses) <= 0:
        return lines

    threshold = _otsu(thicknesses)
    thin = [t for t in thicknesses if t <= threshold]
    thick = [t for t in thicknesses if t > threshold]
    if not thin or not thick:
        return lines

    mean_thin = sum(thin) / len(thin)
    mean_thick = sum(thick) / len(thick)
    if mean_thin <= 0 or mean_thick / mean_thin < min_separation:
        return lines  # one cluster: no ruby on this page

    for line in lines:
        if line.text.strip() and line.thickness <= threshold:
            line.is_ruby = True
    return lines


def reflow(lines: list[Line]) -> list[str]:
    """Join body fragments back into sentences and numbered items.

    A paragraph ends at sentence-ending punctuation, or where a new numbered
    item begins. Line length deliberately plays no part: NDL splits one printed
    column into several boxes wherever a ruby annotation interrupts it, so a
    short box means "interrupted by furigana", not "the paragraph ended".
    Treating short boxes as breaks shreds sentences.

    The cost of leaving it out is that consecutive headings or maxims set
    without full stops arrive joined into one paragraph. That is far cheaper
    than broken grammar, and a translator handles it.
    """
    out: list[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            out.append(buf.strip())
        buf = ""

    for line in lines:
        if line.is_ruby:
            continue
        text = line.text.strip()
        if not text:
            continue

        # A bare item number belongs with the text that follows it.
        if _ITEM_MARKER.fullmatch(text):
            flush()
            buf = text
            continue

        # A new numbered item starts a new paragraph.
        if _STARTS_ITEM.match(text) and buf and not _ITEM_MARKER.fullmatch(buf.strip()):
            flush()

        buf += text

        stripped = buf.rstrip(_CLOSERS)
        if stripped and stripped[-1] in _SENTENCE_END:
            flush()

    flush()
    return out


def dedupe(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop entries NDL repeated verbatim - same text, same four coordinates.

    NDL's coordjson lists some lines twice. It is not rare: 2.7% of lines in pid
    843085 across 97 frames, 4.5% in pid 1457899 across 113. Every consumer that
    concatenates lines doubles that text, so it reached the transcription, the
    reading text, the chunks sent to a translator and the DOCX.

    Two boxes at identical coordinates cannot be two things on the page, so this
    removes a serialization artefact rather than judging the text. A line that
    genuinely repeats - a ditto column, a running head - has a different box and
    survives.
    """
    seen: set[tuple] = set()
    out = []
    for line in raw:
        key = (str(line.get("contenttext", "")), line.get("xmin"),
               line.get("ymin"), line.get("xmax"), line.get("ymax"))
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def frame_reading_text(entry: dict[str, Any]) -> tuple[list[str], int, int]:
    """Reading text for one frame: (paragraphs, body_lines, ruby_lines)."""
    coord = entry.get("coordjson")
    if not coord or coord == "null":
        contents = str(entry.get("contents") or "").strip()
        return ([contents] if contents else []), (1 if contents else 0), 0
    try:
        raw = json.loads(coord)
    except ValueError:
        return [], 0, 0
    lines = classify(dedupe(raw))
    ruby = sum(1 for l in lines if l.is_ruby)
    body = sum(1 for l in lines if l.text.strip() and not l.is_ruby)
    return reflow(lines), body, ruby
