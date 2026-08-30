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
    )
    print(f"{res.frames} frames -> {res.transcription_path}")
    print(f"{len(res.chunk_paths)} chunks in {out / 'chunks'}")
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
