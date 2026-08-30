"""The NDL Workbench window.

One search bar takes either a reference number or a phrase. Everything else is
a consequence of what that search finds: a volume with public OCR gets the
fetch/translate/render chain, a restricted one gets routed to local OCR with an
explanation instead of a failure.

All network and subprocess work happens on a worker thread and reports back
through a queue, so the window never freezes and every step is visible in the
log pane.
"""

from __future__ import annotations

import datetime as _dt
import queue
import subprocess
import sys
import threading
import traceback
import webbrowser
from pathlib import Path
from tkinter import (BOTH, END, LEFT, RIGHT, StringVar, Tk, X, Y, filedialog,
                     messagebox, ttk)
from tkinter.scrolledtext import ScrolledText

from . import ndl, ocr_local, transcript
from .config import APP_NAME, Settings
from .docx_out import markdown_to_docx
from .translate import (TranslationUnavailable, header_markdown,
                        translate_chunks)


def resource_path(name: str) -> Path:
    """Find a bundled data file, whether frozen by PyInstaller or run from source."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = Path(base) / name
        if p.exists():
            return p
    return Path(__file__).resolve().parent.parent / name


def open_in_explorer(path: Path) -> None:
    if not path.exists():
        return
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", str(path if path.is_dir() else path.parent)])
    else:  # pragma: no cover - the app targets Windows
        webbrowser.open(path.as_uri())


class Workbench(Tk):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings.load()
        self.title(APP_NAME)
        self.geometry("1120x760")
        self.minsize(940, 620)

        self.msgq: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.cancel_flag = threading.Event()
        self.hits: list[ndl.SearchHit] = []
        self.current_pid: str | None = None
        self.current_dir: Path | None = None

        self._build_search_bar()
        self._build_tabs()
        self._build_log()
        self.after(100, self._drain)
        self.log(f"{APP_NAME} ready. Data home: {self.settings.data_home}")

    # ---------------------------------------------------------------- layout

    def _build_search_bar(self) -> None:
        bar = ttk.Frame(self, padding=(10, 10, 10, 4))
        bar.pack(fill=X)
        ttk.Label(bar, text="Reference / search:").pack(side=LEFT)
        self.query = StringVar()
        entry = ttk.Entry(bar, textvariable=self.query)
        entry.pack(side=LEFT, fill=X, expand=True, padx=8)
        entry.bind("<Return>", lambda _e: self.on_search())
        entry.focus_set()
        ttk.Button(bar, text="Search", command=self.on_search).pack(side=LEFT)

        hint = ttk.Frame(self, padding=(10, 0, 10, 6))
        hint.pack(fill=X)
        ttk.Label(
            hint,
            foreground="#555",
            text=("Paste a PID (843085), a dl.ndl.go.jp URL, or type a title or keyword "
                  "in Japanese. Keyword search covers the Next-Gen Digital Library index — "
                  "every hit there has OCR this app can fetch."),
            wraplength=1060,
        ).pack(anchor="w")

    def _build_tabs(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=BOTH, expand=True, padx=10, pady=4)
        self._tab_library()
        self._tab_local_ocr()
        self._tab_settings()
        self._tab_manual()

    def _tab_library(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Library")

        cols = ("pid", "title", "author", "publisher", "year", "frames")
        self.tree = ttk.Treeview(tab, columns=cols, show="headings", height=11)
        for c, w, t in (
            ("pid", 90, "PID"), ("title", 420, "Title"), ("author", 200, "Author"),
            ("publisher", 160, "Publisher"), ("year", 70, "Year"), ("frames", 70, "Frames"),
        ):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill=BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-1>", lambda _e: self.on_fetch())

        opts = ttk.LabelFrame(tab, text="Page mapping (advanced)", padding=8)
        opts.pack(fill=X, pady=(8, 4))
        ttk.Label(
            opts, foreground="#555", wraplength=1040,
            text=("Leave blank to fit the mapping from the volume's table-of-contents anchors. "
                  "If the fit reports a large deviation — plates inserted mid-book, or an "
                  "appendix that restarts at 1 — write the segments by hand, e.g. "
                  "27:-54;38:?;52:-70;173:-345  (startFrame:offset, page = 2*frame + offset, "
                  "? = unnumbered stretch)."),
        ).pack(anchor="w")
        row = ttk.Frame(opts)
        row.pack(fill=X, pady=(6, 0))
        self.page_map = StringVar()
        ttk.Entry(row, textvariable=self.page_map).pack(side=LEFT, fill=X, expand=True)
        ttk.Label(row, text="  frames per chunk:").pack(side=LEFT)
        self.chunk_size = StringVar(value=str(self.settings.frames_per_chunk))
        ttk.Entry(row, textvariable=self.chunk_size, width=5).pack(side=LEFT)

        btns = ttk.Frame(tab)
        btns.pack(fill=X, pady=6)
        self.btn_fetch = ttk.Button(btns, text="1. Fetch transcription", command=self.on_fetch)
        self.btn_fetch.pack(side=LEFT)
        self.btn_translate = ttk.Button(btns, text="2. Translate → DOCX", command=self.on_translate)
        self.btn_translate.pack(side=LEFT, padx=6)
        ttk.Button(btns, text="Render DOCX from markdown…", command=self.on_render_only).pack(side=LEFT)
        ttk.Button(btns, text="Open output folder", command=self.on_open_folder).pack(side=LEFT, padx=6)
        ttk.Button(btns, text="View at NDL", command=self.on_open_viewer).pack(side=LEFT)
        self.btn_cancel = ttk.Button(btns, text="Cancel", command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side=RIGHT)

    def _tab_local_ocr(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Local OCR")
        ttk.Label(
            tab, wraplength=1040, justify=LEFT,
            text=("For items NDL does not publish as text. The fulltext API serves "
                  "internet-public items only and refuses everything else with "
                  "\"This PID is not allowed\" — that gate is on the item's rights tier, "
                  "not on your account, so signing in does not open it.\n\n"
                  "Obtain the page images by a route you are entitled to use (NDL's "
                  "remote-copy service, another holding library, your own photography), "
                  "then read them here. The output lands in the same per-frame shape as "
                  "the online path, so the Translate step works on it unchanged."),
        ).pack(anchor="w", pady=(0, 10))

        f = ttk.Frame(tab)
        f.pack(fill=X)
        ttk.Label(f, text="Images (file or folder):").pack(side=LEFT)
        self.ocr_src = StringVar()
        ttk.Entry(f, textvariable=self.ocr_src).pack(side=LEFT, fill=X, expand=True, padx=8)
        ttk.Button(f, text="File…", command=lambda: self._pick(self.ocr_src, False)).pack(side=LEFT)
        ttk.Button(f, text="Folder…", command=lambda: self._pick(self.ocr_src, True)).pack(side=LEFT, padx=4)

        f2 = ttk.Frame(tab)
        f2.pack(fill=X, pady=8)
        ttk.Label(f2, text="Job name:").pack(side=LEFT)
        self.ocr_name = StringVar(value="local-job")
        ttk.Entry(f2, textvariable=self.ocr_name, width=32).pack(side=LEFT, padx=8)
        ttk.Button(f2, text="Run OCR", command=self.on_run_ocr).pack(side=LEFT)
        ttk.Button(f2, text="Import existing OCR text…", command=self.on_import_ocr).pack(side=LEFT, padx=6)
        ttk.Button(f2, text="Check NDLOCR-Lite install", command=self.on_check_ocr).pack(side=LEFT)

    def _tab_settings(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Settings")
        self.svars: dict[str, StringVar] = {}
        rows = [
            ("data_home", "Data home", "Volumes are written to <data home>/manuals/ndl-<pid>/"),
            ("ndlocr_dir", "NDLOCR-Lite folder", "Leave blank to auto-detect"),
            ("ndlocr_cmd", "NDLOCR-Lite command", "Placeholders: {python} {cli} {input} {output}"),
            ("anthropic_api_key", "Anthropic API key", "Blank = use ANTHROPIC_API_KEY or an `ant auth login` profile"),
            ("model", "Model", "Default claude-opus-5"),
            ("effort", "Effort", "low | medium | high | xhigh | max"),
            ("frames_per_chunk", "Frames per chunk", "How many frames go to the model at once"),
            ("latin_font", "DOCX Latin font", ""),
            ("cjk_font", "DOCX CJK font", ""),
        ]
        for i, (key, label, hint) in enumerate(rows):
            ttk.Label(tab, text=label + ":").grid(row=i, column=0, sticky="e", padx=6, pady=3)
            var = StringVar(value=str(getattr(self.settings, key)))
            self.svars[key] = var
            show = "*" if key == "anthropic_api_key" else ""
            ttk.Entry(tab, textvariable=var, width=64, show=show).grid(row=i, column=1, sticky="we", pady=3)
            ttk.Label(tab, text=hint, foreground="#555").grid(row=i, column=2, sticky="w", padx=6)
        tab.columnconfigure(1, weight=1)
        ttk.Button(tab, text="Save settings", command=self.on_save_settings).grid(
            row=len(rows), column=1, sticky="w", pady=10)

    def _tab_manual(self) -> None:
        tab = ttk.Frame(self.nb, padding=8)
        self.nb.add(tab, text="Manual")
        text = ScrolledText(tab, wrap="word", font=("Segoe UI", 10))
        text.pack(fill=BOTH, expand=True)
        try:
            text.insert(END, resource_path("MANUAL.md").read_text(encoding="utf-8"))
        except OSError:
            text.insert(END, "MANUAL.md was not bundled with this build.")
        text.configure(state="disabled")

    def _build_log(self) -> None:
        frame = ttk.Frame(self, padding=(10, 0, 10, 10))
        frame.pack(fill=BOTH, expand=False)
        self.progress = ttk.Progressbar(frame, mode="determinate")
        self.progress.pack(fill=X, pady=(0, 4))
        self.logbox = ScrolledText(frame, height=11, wrap="word", font=("Consolas", 9))
        self.logbox.pack(fill=BOTH, expand=True)
        self.logbox.configure(state="disabled")

    # ------------------------------------------------------------- plumbing

    def log(self, msg: str) -> None:
        self.msgq.put(("log", msg))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.msgq.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert(END, f"{_dt.datetime.now():%H:%M:%S}  {payload}\n")
                    self.logbox.see(END)
                    self.logbox.configure(state="disabled")
                elif kind == "progress":
                    done, total = payload  # type: ignore[misc]
                    self.progress.configure(maximum=max(total, 1), value=done)
                elif kind == "hits":
                    self._show_hits(payload)  # type: ignore[arg-type]
                elif kind == "done":
                    self._set_busy(False)
                elif kind == "error":
                    self._set_busy(False)
                    messagebox.showerror(APP_NAME, str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.btn_fetch.configure(state=state)
        self.btn_translate.configure(state=state)
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if not busy:
            self.progress.configure(value=0)

    def _run(self, fn, *args) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Something is already running. Wait for it, or cancel.")
            return
        self.cancel_flag.clear()
        self._set_busy(True)

        def wrapper() -> None:
            try:
                fn(*args)
            except Exception as e:  # surfaced to the user, not swallowed
                self.log("ERROR: " + str(e))
                self.log(traceback.format_exc(limit=3))
                self.msgq.put(("error", e))
                return
            self.msgq.put(("done", None))

        self.worker = threading.Thread(target=wrapper, daemon=True)
        self.worker.start()

    def _pick(self, var: StringVar, want_dir: bool) -> None:
        path = filedialog.askdirectory() if want_dir else filedialog.askopenfilename()
        if path:
            var.set(path)

    # -------------------------------------------------------------- actions

    def on_search(self) -> None:
        q = self.query.get().strip()
        if not q:
            return
        pid = ndl.parse_pid(q)
        if pid:
            self._run(self._probe_one, pid)
        else:
            self._run(self._search_many, q)

    def _probe_one(self, pid: str) -> None:
        self.log(f"checking pid {pid} …")
        status = ndl.probe_pid(pid)
        if not status.ocr_available:
            self.log(f"pid {pid}: NO PUBLIC OCR")
            self.log(status.detail)
            self.log("→ use the Local OCR tab with images you are entitled to use.")
            self.msgq.put(("hits", []))
            return
        rec = ndl.book_record(pid)
        hit = ndl.SearchHit(
            pid=pid,
            title=str(rec.get("title") or ""),
            volume=str(rec.get("volume") or ""),
            author=str(rec.get("responsibility") or ""),
            publisher=str(rec.get("publisher") or ""),
            year=str(rec.get("published") or ""),
            call_no=str(rec.get("callNo") or ""),
            pages=int(rec.get("page") or 0),
        )
        self.log(f"pid {pid}: {hit.display_title} — OCR available")
        self.msgq.put(("hits", [hit]))

    def _search_many(self, keyword: str) -> None:
        self.log(f"searching the Next-Gen Digital Library for {keyword!r} …")
        hits, total = ndl.search(keyword, size=50)
        self.log(f"{total} hit(s); showing {len(hits)}")
        self.msgq.put(("hits", hits))

    def _show_hits(self, hits: list[ndl.SearchHit]) -> None:
        self.hits = hits
        self.tree.delete(*self.tree.get_children())
        for h in hits:
            self.tree.insert("", END, values=(h.pid, h.display_title, h.author,
                                              h.publisher, h.year, h.pages or ""))
        if hits:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self._on_select()

    def _on_select(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.current_pid = str(self.tree.item(sel[0])["values"][0])

    def _selected_hit(self) -> ndl.SearchHit | None:
        if not self.current_pid:
            return None
        for h in self.hits:
            if h.pid == self.current_pid:
                return h
        return None

    def _volume_dir(self, hit: ndl.SearchHit) -> Path:
        return self.settings.volume_dir(hit.pid, transcript.slugify(hit.display_title))

    def on_fetch(self) -> None:
        hit = self._selected_hit()
        if not hit:
            messagebox.showinfo(APP_NAME, "Select a volume first.")
            return
        self._run(self._fetch, hit)

    def _fetch(self, hit: ndl.SearchHit) -> None:
        out = self._volume_dir(hit)
        out.mkdir(parents=True, exist_ok=True)
        self.current_dir = out
        self.log(f"volume folder: {out}")

        book = ndl.cached_fetch(out / "ndl_book_raw.json",
                                lambda: ndl.book_record(hit.pid), log=self.log)
        full = ndl.cached_fetch(out / "ndl_fulltext_raw.json",
                                lambda: ndl.fulltext(hit.pid), log=self.log)

        spec = self.page_map.get().strip()
        pmap = transcript.parse_page_map(spec) if spec else None
        try:
            chunk_n = max(1, int(self.chunk_size.get()))
        except ValueError:
            chunk_n = self.settings.frames_per_chunk

        res = transcript.build_transcription(
            hit.pid, book, full, out,
            page_map=pmap, frames_per_chunk=chunk_n,
            retrieved=_dt.date.today().isoformat(),
        )
        self.log(f"{res.frames} frames → {res.transcription_path.name}")
        self.log(f"{len(res.chunk_paths)} chunk file(s) in chunks/")
        self.log("page mapping: " + res.page_map.describe())

        if res.page_map.is_estimated and (res.page_map.max_deviation or 0) > 3:
            self.log(
                f"WARNING: the fitted page mapping is off by up to "
                f"{res.page_map.max_deviation} pages. That usually means inserted "
                f"plates or a restarting appendix. Write a page map by hand and re-fetch."
            )
        confirmed, bad, bad_frames = transcript.verify_page_map(full, res.page_map)
        if confirmed or bad:
            self.log(f"page-map check against printed folios: {confirmed} confirmed, {bad} unconfirmed"
                     + (f" (frames {bad_frames[:8]}{'…' if len(bad_frames) > 8 else ''})" if bad else ""))
        self.log("done. Step 2 translates these chunks.")

    def on_translate(self) -> None:
        hit = self._selected_hit()
        if not hit:
            messagebox.showinfo(APP_NAME, "Select a volume first.")
            return
        out = self._volume_dir(hit)
        chunks = sorted((out / "chunks").glob("chunk_*.txt"))
        if not chunks:
            messagebox.showinfo(APP_NAME, "No chunks yet — run step 1 first.")
            return
        if not messagebox.askokcancel(
            APP_NAME,
            f"Translate {len(chunks)} chunk(s) of {hit.display_title} with "
            f"{self.settings.model}?\n\nThis calls the Anthropic API and costs money.",
        ):
            return
        self._run(self._translate, hit, out, chunks)

    def _translate(self, hit: ndl.SearchHit, out: Path, chunks: list[Path]) -> None:
        import json
        book_path = out / "ndl_book_raw.json"
        book = json.loads(book_path.read_text(encoding="utf-8")) if book_path.is_file() else {}
        transcription = out / f"{hit.pid}_transcription_ja.txt"
        pm_note = "see the transcription header"
        if transcription.is_file():
            for line in transcription.read_text(encoding="utf-8-sig").splitlines()[:12]:
                if line.startswith("Page mapping:"):
                    pm_note = line.split(":", 1)[1].strip()
                    break

        header = header_markdown(
            pid=hit.pid,
            title=hit.display_title,
            published=hit.year,
            publisher=str(book.get("publisher") or hit.publisher),
            frames=sum(1 for _ in transcription.read_text(encoding="utf-8-sig").split("=== Frame ")) - 1
            if transcription.is_file() else 0,
            page_map_note=pm_note,
            retrieved=_dt.date.today().isoformat(),
        )

        md = out / f"{hit.pid}_translation_en.md"
        try:
            res = translate_chunks(
                chunks, md,
                settings=self.settings, pid=hit.pid,
                title=hit.display_title, published=hit.year, header=header,
                log=self.log,
                should_cancel=self.cancel_flag.is_set,
                progress=lambda d, t: self.msgq.put(("progress", (d, t))),
            )
        except TranslationUnavailable as e:
            self.log("translation unavailable: " + str(e))
            self.log(f"the chunk files are in {out / 'chunks'} and can be translated elsewhere, "
                     f"then rendered with 'Render DOCX from markdown…'.")
            raise

        self.log(f"translated {res.chunks_done}/{len(chunks)} chunk(s); "
                 f"{res.input_tokens} in / {res.output_tokens} out tokens, "
                 f"{res.cached_tokens} read from cache")
        docx = markdown_to_docx(md, out / f"{hit.pid}_translation_en.docx",
                                latin_font=self.settings.latin_font,
                                cjk_font=self.settings.cjk_font)
        self.log(f"wrote {docx.name}")

    def on_render_only(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Markdown", "*.md"), ("All files", "*.*")])
        if not path:
            return
        md = Path(path)
        self._run(self._render, md)

    def _render(self, md: Path) -> None:
        docx = markdown_to_docx(md, md.with_suffix(".docx"),
                                latin_font=self.settings.latin_font,
                                cjk_font=self.settings.cjk_font)
        self.log(f"wrote {docx}")

    def on_open_folder(self) -> None:
        hit = self._selected_hit()
        target = self._volume_dir(hit) if hit else Path(self.settings.data_home)
        if not target.exists():
            target = Path(self.settings.data_home)
        open_in_explorer(target)

    def on_open_viewer(self) -> None:
        if self.current_pid:
            webbrowser.open(ndl.viewer_url(self.current_pid))

    def on_cancel(self) -> None:
        self.cancel_flag.set()
        self.log("cancel requested — will stop after the current step")

    # ------------------------------------------------------------ local OCR

    def on_check_ocr(self) -> None:
        install = ocr_local.find_install(self.settings)
        if install is None:
            self.log("NDLOCR-Lite not found. Looked in: "
                     + ", ".join(str(p) for p in ocr_local.candidate_roots(self.settings)))
            self.log("Set the folder in Settings > NDLOCR-Lite folder once it is installed.")
            return
        self.log("NDLOCR-Lite:\n" + install.describe())
        self.log("usable: " + ("yes" if install.usable else "no — no runnable entry point found"))

    def on_run_ocr(self) -> None:
        src = self.ocr_src.get().strip()
        if not src:
            messagebox.showinfo(APP_NAME, "Choose the image file or folder first.")
            return
        self._run(self._run_ocr, Path(src), self.ocr_name.get().strip() or "local-job")

    def _run_ocr(self, src: Path, name: str) -> None:
        out = Path(self.settings.data_home) / "manuals" / f"local-{name}"
        raw = out / "ocr_raw"
        n = ocr_local.count_images(src)
        self.log(f"{n} image file(s) under {src}")
        ocr_local.run_ocr(src, raw, settings=self.settings, log=self.log)
        transcription, chunks = ocr_local.import_text_dir(
            raw, out, label=name, source_note=str(src),
            frames_per_chunk=self.settings.frames_per_chunk, log=self.log)
        self.log(f"wrote {transcription}")
        self.log("Translate these chunks with 'Render DOCX from markdown…' after translating, "
                 "or point step 2 at this folder.")

    def on_import_ocr(self) -> None:
        path = filedialog.askdirectory(title="Folder of OCR .txt output")
        if not path:
            return
        name = self.ocr_name.get().strip() or "local-job"
        self._run(self._import_ocr, Path(path), name)

    def _import_ocr(self, text_dir: Path, name: str) -> None:
        out = Path(self.settings.data_home) / "manuals" / f"local-{name}"
        transcription, chunks = ocr_local.import_text_dir(
            text_dir, out, label=name, source_note=str(text_dir),
            frames_per_chunk=self.settings.frames_per_chunk, log=self.log)
        self.log(f"wrote {transcription} and {len(chunks)} chunk file(s)")

    # -------------------------------------------------------------- settings

    def on_save_settings(self) -> None:
        for key, var in self.svars.items():
            value = var.get()
            if key == "frames_per_chunk":
                try:
                    setattr(self.settings, key, max(1, int(value)))
                except ValueError:
                    messagebox.showerror(APP_NAME, "Frames per chunk must be a number.")
                    return
            else:
                setattr(self.settings, key, value)
        path = self.settings.save()
        self.log(f"settings saved to {path}")


def main() -> int:
    app = Workbench()
    app.mainloop()
    return 0
