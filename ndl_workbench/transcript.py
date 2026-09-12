"""Turn NDL's fulltext OCR into a per-frame transcription plus translation chunks.

This is the Python port of scripts/ndl_fulltext_pull.ps1, including the
piecewise page map that script grew for volumes whose numbering restarts or is
interrupted by unnumbered plates.

Two things this module deliberately does NOT do:

  * It does not read the scans. NDL has already run production OCR; we pass it
    through and label it as uncorrected machine output, because that is what it
    is. The geta mark 〓 stays exactly where NDL put it.

  * It does not silently trust the page mapping. A single linear fit is wrong
    for any volume with an inserted plate section or a restarting appendix, so
    the fit reports its own worst deviation and the caller can override it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from . import reflow

BOM = "﻿"

_TOC_LINE = re.compile(r"^(.*?)(?:/(\d+))?\s*\((\d+)\.jp2\)\s*$")

_KANJI_DIGIT = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
                "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


# --------------------------------------------------------------------------
# table-of-contents anchors
# --------------------------------------------------------------------------


@dataclass
class TocAnchor:
    section: str
    printed_page: Optional[int]
    frame: int


def toc_anchors(book: dict[str, Any]) -> list[TocAnchor]:
    out: list[TocAnchor] = []
    for entry in book.get("index") or []:
        m = _TOC_LINE.match(str(entry))
        if not m:
            continue
        out.append(
            TocAnchor(
                section=m.group(1).strip(),
                printed_page=int(m.group(2)) if m.group(2) else None,
                frame=int(m.group(3)),
            )
        )
    return out


# --------------------------------------------------------------------------
# page mapping
# --------------------------------------------------------------------------


@dataclass
class PageMap:
    """frame -> printed page(s).

    `segments` is a list of (start_frame, offset|None) sorted by frame, where a
    page is 2*frame + offset and None marks an unnumbered stretch (plates).
    Frames before the first segment are front matter.
    """

    segments: list[tuple[int, Optional[int]]]
    source: str            # how it was derived, for the file header
    max_deviation: Optional[int] = None
    slope: int = 2         # 2 = one frame is a two-page spread, 1 = single page

    @property
    def is_estimated(self) -> bool:
        return self.source.startswith("fit")

    def label(self, frame: int) -> str:
        """The PRINTED: line for one frame.

        Wording matches scripts/ndl_fulltext_pull.ps1 exactly, so the packaged
        app and the PowerShell script produce interchangeable transcriptions.
        """
        seg: Optional[tuple[int, Optional[int]]] = None
        for s in self.segments:
            if frame >= s[0]:
                seg = s
        if seg is None:
            return "front matter (before printed page 1)"
        if seg[1] is None:
            return "unnumbered plates / insert (no printed folio)"
        p = self.slope * frame + seg[1]
        if self.is_estimated:
            if self.slope == 2:
                return f"printed pages ~{p}-{p + 1} (estimated)"
            return f"printed page ~{p} (estimated)"
        if p < 1:
            return f"printed page {p + 1} (from page map)"
        return f"printed pages {p}-{p + 1} (from page map)"

    def describe(self) -> str:
        parts = []
        for start, off in self.segments:
            if off is None:
                parts.append(f"frame {start}+: unnumbered plates")
            else:
                parts.append(f"frame {start}+: page = 2*frame + {off}")
        s = "; ".join(parts) if parts else "unknown"
        if self.is_estimated and self.max_deviation is not None:
            s += f" (fitted from TOC anchors, max deviation {self.max_deviation} - VERIFY against the scan)"
        elif not self.is_estimated:
            s += " (hand-specified page map)"
        return s


def parse_page_map(spec: str) -> PageMap:
    """Parse "27:-54;38:?;52:-70;173:-345" into a PageMap."""
    segments: list[tuple[int, Optional[int]]] = []
    for part in (spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        bits = part.split(":")
        if len(bits) != 2:
            raise ValueError(f"page-map segment {part!r} is not startFrame:offset")
        frame = int(bits[0].strip())
        off_s = bits[1].strip()
        segments.append((frame, None if off_s == "?" else int(off_s)))
    segments.sort(key=lambda s: s[0])
    return PageMap(segments, source="manual")


def fit_page_map(anchors: Iterable[TocAnchor]) -> PageMap:
    """The original single-line fit: page = slope*frame + offset.

    Kept because it is right for a plainly-printed volume, and because its
    deviation is the signal that tells the user to write a real map instead.
    """
    a = sorted([x for x in anchors if x.printed_page is not None], key=lambda x: x.frame)
    if len(a) < 2:
        return PageMap([], source="fit/none")

    rates = []
    for prev, cur in zip(a, a[1:]):
        df = cur.frame - prev.frame
        if df > 0:
            rates.append((cur.printed_page - prev.printed_page) / df)  # type: ignore[operator]
    if not rates:
        return PageMap([], source="fit/none")
    rates.sort()
    median_rate = rates[(len(rates) - 1) // 2]
    slope = 2 if abs(median_rate - 2) <= abs(median_rate - 1) else 1

    offsets = sorted(x.printed_page - slope * x.frame for x in a)  # type: ignore[operator]
    offset = offsets[(len(offsets) - 1) // 2]
    max_dev = max(abs(x.printed_page - (slope * x.frame + offset)) for x in a)  # type: ignore[operator]

    first = a[0].frame
    if slope == 2:
        # Express as the standard 2*frame + off form used by manual maps: the
        # spread holds pages (p, p+1), so the stored offset is one less than
        # the fit's, which named the higher page.
        return PageMap([(first, offset - 1)], source="fit/spread",
                       max_deviation=int(max_dev), slope=2)
    return PageMap([(first, offset)], source="fit/single",
                   max_deviation=int(max_dev), slope=1)


# --------------------------------------------------------------------------
# printed-folio verification
# --------------------------------------------------------------------------


def _kanji_number(s: str) -> Optional[int]:
    if not s or not re.fullmatch(r"[〇一二三四五六七八九十百]+", s):
        return None
    total = cur = 0
    for ch in s:
        if ch in _KANJI_DIGIT:
            cur = _KANJI_DIGIT[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
    return total + cur


def verify_page_map(fulltext: dict[str, Any], pmap: PageMap, *, floor: int = 20) -> tuple[int, int, list[int]]:
    """Check the map against printed folios the OCR actually caught.

    Returns (confirmed, unconfirmed, unconfirmed_frames). Only standalone kanji
    numerals at or above `floor` are considered, because low numbers are list
    item markers, not folios.
    """
    confirmed = 0
    unconfirmed: list[int] = []
    for entry in sorted(fulltext.get("list", []), key=lambda e: e.get("page", 0)):
        frame = int(entry.get("page", 0))
        label = pmap.label(frame)
        m = re.search(r"printed pages? (\d+)(?:-(\d+))?", label)
        if not m:
            continue
        expected = {int(m.group(1))}
        if m.group(2):
            expected.add(int(m.group(2)))
        found = set()
        for line in _frame_lines(entry):
            v = _kanji_number(line.strip())
            if v is not None and floor <= v <= 400:
                found.add(v)
        if not found:
            continue
        if found & expected:
            confirmed += 1
        else:
            unconfirmed.append(frame)
    return confirmed, len(unconfirmed), unconfirmed


# --------------------------------------------------------------------------
# transcription
# --------------------------------------------------------------------------


def _frame_lines(entry: dict[str, Any]) -> list[str]:
    """The text lines of one frame, rebuilt from NDL's per-line coordinates.

    An empty coordinate array is a real answer - a blank leaf - and stays empty.
    The "no OCR text" placeholder is only for a frame NDL returned no coordinate
    data for at all, which is what the PowerShell script does; keeping the two
    identical means either tool can produce a given volume's transcription.

    Entries NDL repeated verbatim are dropped - see `reflow.dedupe` - exactly as
    `ndl_fulltext_pull.ps1` drops them, so the two tools still produce the same
    transcription. A line the page does not carry twice was never part of the
    mirror of the scan.
    """
    coord = entry.get("coordjson")
    if not coord or coord == "null":
        contents = entry.get("contents")
        if contents and str(contents).strip():
            return [str(contents)]
        return ["(no OCR text on this frame)"]
    try:
        return [str(l.get("contenttext", ""))
                for l in reflow.dedupe(json.loads(coord))]
    except (ValueError, AttributeError):
        return []


def _write_chunks(blocks: list[str], chunk_dir: Path, frames_per_chunk: int) -> list[Path]:
    """Cut the frame blocks into translation-sized chunk files."""
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for old in chunk_dir.glob("chunk_*.txt"):
        old.unlink()
    paths: list[Path] = []
    for i in range(0, len(blocks), frames_per_chunk):
        p = chunk_dir / f"chunk_{i // frames_per_chunk + 1:02d}.txt"
        p.write_text(BOM + "".join(blocks[i:i + frames_per_chunk]), encoding="utf-8")
        paths.append(p)
    return paths


@dataclass
class BuildResult:
    transcription_path: Path
    chunk_paths: list[Path]
    frames: int
    title: str
    published: str
    page_map: PageMap


def build_transcription(
    pid: str,
    book: dict[str, Any],
    fulltext: dict[str, Any],
    out_dir: Path,
    *,
    page_map: Optional[PageMap] = None,
    frames_per_chunk: int = 15,
    retrieved: str = "",
    write_chunks: bool = True,
) -> BuildResult:
    """Write <pid>_transcription_ja.txt, the faithful line-by-line record.

    Set write_chunks=False when the caller is going to cut the chunks from the
    reading text instead - that is the better input for a translator, and two
    sets of chunk files in one folder would be a trap.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = toc_anchors(book)
    pmap = page_map or fit_page_map(anchors)

    title = str(book.get("title") or f"NDL pid {pid}")
    volume = str(book.get("volume") or "")
    published = str(book.get("published") or "")
    publisher = str(book.get("publisher") or "")

    head = [
        "=" * 78,
        f"{title} {volume}".strip() + " - transcription from NDL's own OCR (uncorrected machine output)",
        f"Source      : NDL Digital Collections pid {pid} - https://dl.ndl.go.jp/pid/{pid}",
        f"Published   : {published} - publisher {publisher}",
        f"Text source : https://lab.ndl.go.jp/dl/api/book/fulltext-json/{pid}",
        "Attribution : National Diet Library. Unreadable glyphs appear as the geta mark.",
        f"Page mapping: {pmap.describe()}",
        f"Retrieved   : {retrieved}",
        "=" * 78,
        "",
    ]

    blocks: list[str] = []
    for entry in sorted(fulltext.get("list", []), key=lambda e: e.get("page", 0)):
        frame = int(entry.get("page", 0))
        b = [
            f"=== Frame {frame} ===",
            f"URL: https://dl.ndl.go.jp/pid/{pid}/1/{frame}",
            f"PRINTED: {pmap.label(frame)}",
        ]
        for t in (x for x in anchors if x.frame == frame):
            note = f" - printed page {t.printed_page}" if t.printed_page is not None else ""
            b.append(f"[TOC: {t.section}{note}]")
        b.extend(_frame_lines(entry))
        b.append("")
        blocks.append("\n".join(b) + "\n")

    transcription = out_dir / f"{pid}_transcription_ja.txt"
    transcription.write_text(BOM + "\n".join(head) + "".join(blocks), encoding="utf-8")

    chunk_paths = (_write_chunks(blocks, out_dir / "chunks", frames_per_chunk)
                   if write_chunks else [])

    return BuildResult(
        transcription_path=transcription,
        chunk_paths=chunk_paths,
        frames=len(blocks),
        title=f"{title} {volume}".strip(),
        published=published,
        page_map=pmap,
    )


def slugify(title: str) -> str:
    """A short ASCII tail for the output directory, or '' when there is none."""
    ascii_bits = re.findall(r"[A-Za-z0-9]+", title)
    return "-".join(ascii_bits)[:40].lower().strip("-")


# --------------------------------------------------------------------------
# reading text: the same volume, shaped for a translator
# --------------------------------------------------------------------------


def build_reading_transcription(
    pid: str,
    book: dict[str, Any],
    fulltext: dict[str, Any],
    out_dir: Path,
    *,
    page_map: Optional[PageMap] = None,
    retrieved: str = "",
    frames_per_chunk: int = 15,
    write_chunks: bool = False,
) -> tuple[Path, int, int, list[Path]]:
    """Write <pid>_reading_ja.txt: ruby removed, wrapped lines rejoined.

    A companion to the faithful transcription, not a replacement for it. The
    transcription mirrors the page one OCR line at a time, which is what you
    want when checking a reading against the scan. This is the same text shaped
    for reading and for machine translation: furigana columns dropped, the
    column-width fragments joined back into sentences.

    Returns (path, body_lines_kept, ruby_lines_dropped, chunk_paths).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    anchors = toc_anchors(book)
    pmap = page_map or fit_page_map(anchors)

    title = str(book.get("title") or f"NDL pid {pid}")
    volume = str(book.get("volume") or "")
    published = str(book.get("published") or "")
    publisher = str(book.get("publisher") or "")

    blocks: list[str] = []
    kept = dropped = 0
    for entry in sorted(fulltext.get("list", []), key=lambda e: e.get("page", 0)):
        frame = int(entry.get("page", 0))
        paragraphs, body, ruby = reflow.frame_reading_text(entry)
        kept += body
        dropped += ruby

        b = [
            f"=== Frame {frame} ===",
            f"URL: https://dl.ndl.go.jp/pid/{pid}/1/{frame}",
            f"PRINTED: {pmap.label(frame)}",
        ]
        for t in (x for x in anchors if x.frame == frame):
            note = f" - printed page {t.printed_page}" if t.printed_page is not None else ""
            b.append(f"[TOC: {t.section}{note}]")
        b.extend(paragraphs if paragraphs else ["(no OCR text on this frame)"])
        b.append("")
        blocks.append("\n".join(b) + "\n")

    share = (100.0 * dropped / (kept + dropped)) if (kept + dropped) else 0.0
    head = [
        "=" * 78,
        f"{title} {volume}".strip() + " - READING TEXT (derived; not the archival transcription)",
        f"Source      : NDL Digital Collections pid {pid} - https://dl.ndl.go.jp/pid/{pid}",
        f"Published   : {published} - publisher {publisher}",
        f"Text source : https://lab.ndl.go.jp/dl/api/book/fulltext-json/{pid}",
        "Attribution : National Diet Library. Unreadable glyphs appear as the geta mark.",
        f"Page mapping: {pmap.describe()}",
        f"Retrieved   : {retrieved}",
        "-" * 78,
        "Derived from the line-by-line transcription for reading and machine",
        "translation:",
        f"  - {dropped} furigana (ruby) lines removed, {share:.0f}% of all text lines. NDL's OCR",
        "    emits each ruby column as its own line, interleaved with the body text;",
        "    left in, they wreck any translation. Identified by column thickness,",
        "    not by guessing from the characters.",
        f"  - {kept} body lines rejoined into sentences. Each printed line is a",
        "    column-width fragment, not a sentence.",
        "No characters were altered. To check a reading against the scan, use the",
        f"line-by-line file: {pid}_transcription_ja.txt",
        "=" * 78,
        "",
    ]

    path = out_dir / f"{pid}_reading_ja.txt"
    path.write_text(BOM + "\n".join(head) + "".join(blocks), encoding="utf-8")

    chunk_paths = (_write_chunks(blocks, out_dir / "chunks", frames_per_chunk)
                   if write_chunks else [])
    return path, kept, dropped, chunk_paths


# --------------------------------------------------------------------------
# paste-sized pieces, for the free web translators
# --------------------------------------------------------------------------

_STRIP_PREFIXES = ("URL:", "PRINTED:", "[TOC:", "SOURCE FILE:")


def build_paste_pieces(
    transcription: Path,
    out_dir: Path,
    *,
    max_chars: int = 4000,
) -> list[Path]:
    """Split a transcription into pieces small enough to paste into a web form.

    The free translators all cap how much text one box will take, and the cap
    differs between services and moves over time, so the size is the caller's
    choice rather than a constant baked in here.

    Provenance lines (frame URL, printed-page label, TOC anchors) are dropped:
    they are wasted characters in a character-limited box, and the frame marker
    that survives is enough to line the English back up with the Japanese. A
    frame is never split across two pieces unless it exceeds the cap on its own.
    """
    text = transcription.read_text(encoding="utf-8-sig")
    blocks = re.split(r"(?m)^=== Frame (\d+) ===$", text)

    # re.split with one capture group yields [preamble, num, body, num, body...]
    frames: list[tuple[str, list[str]]] = []
    for i in range(1, len(blocks), 2):
        number = blocks[i]
        body = [
            line for line in blocks[i + 1].splitlines()
            if line.strip() and not line.startswith(_STRIP_PREFIXES)
        ]
        frames.append((number, body))

    pieces: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            pieces.append("\n".join(current).strip() + "\n")
            current = []
            size = 0

    for number, body in frames:
        marker = f"--- Frame {number} ---"
        lines = [marker] + body
        length = sum(len(l) + 1 for l in lines)

        if length > max_chars:
            # One oversized frame: flush what is pending, then break the frame
            # across pieces, repeating the marker so each piece says where it
            # belongs.
            flush()
            part = [marker]
            part_len = len(marker) + 1
            for line in body:
                if part_len + len(line) + 1 > max_chars and len(part) > 1:
                    pieces.append("\n".join(part).strip() + "\n")
                    part = [marker + " (continued)"]
                    part_len = len(part[0]) + 1
                part.append(line)
                part_len += len(line) + 1
            if len(part) > 1:
                pieces.append("\n".join(part).strip() + "\n")
            continue

        if size + length > max_chars:
            flush()
        current.extend(lines)
        size += length

    flush()

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("piece_*.txt"):
        old.unlink()
    paths: list[Path] = []
    for n, piece in enumerate(pieces, start=1):
        path = out_dir / f"piece_{n:02d}.txt"
        path.write_text(piece, encoding="utf-8")
        paths.append(path)
    return paths
