"""Entry point.

    python -m ndl_workbench              launch the window
    python -m ndl_workbench --selftest   exercise the pipeline without the GUI
    python -m ndl_workbench --fetch PID  headless fetch, for scripting

The selftest is what CI and a post-build smoke check run: it touches the search
API once, renders a DOCX, and reports the local-OCR install state, so a broken
build fails loudly instead of at the user's first click.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
import tempfile
from pathlib import Path


def _selftest() -> int:
    from . import ndl, ocr_local, transcript
    from .config import Settings
    from .docx_out import markdown_to_docx

    ok = True
    settings = Settings.load()
    print(f"data home        : {settings.data_home}")

    print("search API       : ", end="", flush=True)
    try:
        hits, total = ndl.search("歩兵操典", size=3)
        print(f"OK ({total} hits; first = pid {hits[0].pid} {hits[0].display_title})")
    except Exception as e:
        print(f"FAILED - {e}")
        ok = False

    print("pid parsing      : ", end="")
    cases = {
        "843085": "843085",
        "https://dl.ndl.go.jp/pid/843085/1/170": "843085",
        "info:ndljp/pid/13877972": "13877972",
        "歩兵操典": None,
    }
    bad = [k for k, v in cases.items() if ndl.parse_pid(k) != v]
    print("OK" if not bad else f"FAILED on {bad}")
    ok = ok and not bad

    print("page map         : ", end="")
    pm = transcript.parse_page_map("27:-54;38:?;52:-70;173:-345")
    checks = [
        (26, "front matter"),
        (27, "printed page 1"),
        (40, "unnumbered"),
        (52, "printed pages 34-35"),
        (170, "printed pages 270-271"),
        (173, "printed pages 1-2"),
    ]
    bad2 = [f for f, want in checks if want not in pm.label(f)]
    print("OK" if not bad2 else f"FAILED on frames {bad2}")
    ok = ok and not bad2

    print("ruby / reflow    : ", end="", flush=True)
    try:
        from . import reflow

        def col(text: str, x: int, thick: int, top: int = 0, length: int = 900):
            return {"contenttext": text, "xmin": x, "xmax": x + thick,
                    "ymin": top, "ymax": top + length}

        # Two body columns (thick) each preceded by a ruby column (thin), as NDL
        # emits them, with the sentence hard-wrapped across the two columns.
        entry = {"coordjson": json.dumps([
            col("グンジン", 1000, 50), col("軍人ハ忠節ヲ盡スヲ", 900, 110),
            col("ホンブン", 800, 50), col("本分トスベシ。", 700, 110),
            col("チウギ", 600, 50), col("忠義ノ心ヲ以テ", 500, 110),
            col("ツク", 400, 50), col("務メヲ盡スベシ。", 300, 110),
        ])}
        paragraphs, body, ruby = reflow.frame_reading_text(entry)
        joined = "".join(paragraphs)
        problems = []
        if ruby != 4:
            problems.append(f"dropped {ruby} ruby lines, expected 4")
        if body != 4:
            problems.append(f"kept {body} body lines, expected 4")
        if any(r in joined for r in ("グンジン", "ホンブン", "チウギ", "ツク")):
            problems.append("ruby text leaked into the output")
        if paragraphs != ["軍人ハ忠節ヲ盡スヲ本分トスベシ。", "忠義ノ心ヲ以テ務メヲ盡スベシ。"]:
            problems.append(f"sentences not rejoined: {paragraphs}")
        print("OK (ruby dropped, wrapped lines rejoined)" if not problems
              else "FAILED - " + "; ".join(problems))
        ok = ok and not problems
    except Exception as e:
        print(f"FAILED - {e}")
        ok = False

    print("paste pieces     : ", end="", flush=True)
    try:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "t_transcription_ja.txt"
            frames = []
            for n in range(1, 21):
                frames.append(
                    f"=== Frame {n} ===\n"
                    f"URL: https://dl.ndl.go.jp/pid/1/1/{n}\n"
                    f"PRINTED: printed pages {n}-{n + 1}\n"
                    + ("軍人ハ忠節ヲ盡スヲ本分トスベシ\n" * 12)
                )
            src.write_text("header\n\n" + "\n".join(frames), encoding="utf-8")
            pieces = transcript.build_paste_pieces(src, Path(td) / "paste", max_chars=1200)
            texts = [p.read_text(encoding="utf-8") for p in pieces]
            over = [p.name for p, t in zip(pieces, texts) if len(t) > 1200]
            covered = sum(t.count("--- Frame ") for t in texts)
            leaked = sum(t.count(x) for t in texts for x in ("URL:", "PRINTED:"))
        if over or covered < 20 or leaked:
            print(f"FAILED (over cap {over}, frames {covered}/20, leaked {leaked})")
            ok = False
        else:
            print(f"OK ({len(pieces)} pieces, 20/20 frames, none over cap)")
    except Exception as e:
        print(f"FAILED - {e}")
        ok = False

    print("docx render      : ", end="", flush=True)
    try:
        with tempfile.TemporaryDirectory() as td:
            md = Path(td) / "t.md"
            md.write_text(
                "# Title\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n"
                "## Frame 1 — front matter\n\n**Bold** and *italic* and 日本語.\n\n- one\n- two\n",
                encoding="utf-8",
            )
            out = markdown_to_docx(md, Path(td) / "t.docx")
            size = out.stat().st_size
        print(f"OK ({size} bytes)")
    except Exception as e:
        print(f"FAILED - {e}")
        ok = False

    print("translation SDK  : ", end="")
    try:
        import anthropic  # noqa: F401
        print("anthropic installed")
    except ImportError:
        print("anthropic NOT installed - automatic translation will be unavailable")

    print("NDLOCR-Lite      : ", end="")
    install = ocr_local.find_install(settings)
    print(f"found at {install.root} (usable={install.usable})" if install else "not found (configure in Settings)")

    print("\nSELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def _fetch(pid: str, page_map: str, chunk: int) -> int:
    from . import ndl, transcript
    from .config import Settings

    settings = Settings.load()
    status = ndl.probe_pid(pid)
    if not status.ocr_available:
        print(status.detail)
        return 2
    out = settings.volume_dir(pid, transcript.slugify(status.title))
    out.mkdir(parents=True, exist_ok=True)
    book = ndl.cached_fetch(out / "ndl_book_raw.json", lambda: ndl.book_record(pid), log=print)
    full = ndl.cached_fetch(out / "ndl_fulltext_raw.json", lambda: ndl.fulltext(pid), log=print)
    res = transcript.build_transcription(
        pid, book, full, out,
        page_map=transcript.parse_page_map(page_map) if page_map else None,
        frames_per_chunk=chunk,
        retrieved=_dt.date.today().isoformat(),
        write_chunks=False,
    )
    print(f"{res.frames} frames -> {res.transcription_path}")

    reading, kept, dropped, chunks = transcript.build_reading_transcription(
        pid, book, full, out,
        page_map=res.page_map, retrieved=_dt.date.today().isoformat(),
        frames_per_chunk=chunk, write_chunks=True,
    )
    share = (100.0 * dropped / (kept + dropped)) if (kept + dropped) else 0.0
    print(f"reading text -> {reading}")
    print(f"  {dropped} ruby lines removed ({share:.0f}%), {kept} body lines rejoined")
    print(f"{len(chunks)} chunks in {out / 'chunks'} (cut from the reading text)")
    print("page mapping:", res.page_map.describe())
    confirmed, bad, _ = transcript.verify_page_map(full, res.page_map)
    print(f"page-map check: {confirmed} confirmed, {bad} unconfirmed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ndl-workbench", description="NDL Workbench")
    ap.add_argument("--selftest", action="store_true", help="run a headless smoke test")
    ap.add_argument("--fetch", metavar="PID", help="fetch one volume headlessly")
    ap.add_argument("--page-map", default="", help="piecewise page map, e.g. 27:-54;38:?;52:-70")
    ap.add_argument("--chunk", type=int, default=15, help="frames per chunk")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.fetch:
        return _fetch(args.fetch, args.page_map, args.chunk)

    from .gui import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
