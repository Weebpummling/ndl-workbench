"""Local OCR for material NDL will not serve as text.

When an item is restricted, the fulltext API is closed to everyone regardless
of who is signed in. The legitimate route is to obtain page images another way
- NDL's remote-copy service, another library, your own photography - and read
them here. This module drives NDLOCR-Lite over those images and then lands the
result in the same per-frame shape the online path produces, so the translation
and DOCX halves of the app work on it unchanged.

The exact command line is a setting rather than a constant. NDLOCR-Lite is
installed per-machine, its entry point has moved between versions, and guessing
it would produce a tool that fails in a way the user cannot diagnose. The app
detects the install, shows what it found, and runs the template.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import Settings

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}


@dataclass
class Install:
    root: Path
    python: Optional[Path]
    cli: Optional[Path]
    gui_exe: Optional[Path]

    @property
    def usable(self) -> bool:
        """Whether a command line can actually be built from this install.

        Both halves are required. The desktop application NDL publishes on its
        releases page is a GUI with no command line at all, so a `gui_exe` on
        its own is not something this app can drive. Counting it as usable is
        what produced the 9009 failure: the template filled `{python}` with a
        bare `python`, Windows resolved that to the App Execution alias, and the
        user got a Microsoft Store advert instead of OCR.
        """
        return bool(self.python and self.cli)

    def describe(self) -> str:
        bits = [f"root: {self.root}"]
        bits.append(f"python: {self.python}" if self.python else "python: NOT FOUND")
        bits.append(f"cli: {self.cli}" if self.cli else "cli: NOT FOUND")
        if self.gui_exe:
            bits.append(f"gui: {self.gui_exe}  (desktop app - has no command line, cannot be driven from here)")
        return "\n".join(bits)

    def problem(self) -> str:
        """Why this install cannot be run, phrased so the user can act on it."""
        if self.usable:
            return ""
        if self.gui_exe and not self.cli and not self.python:
            return (
                f"{self.root} holds the NDLOCR-Lite desktop application. That build is a\n"
                "GUI only - it has no command line, so this app cannot drive it.\n"
                "\n"
                "The Local OCR tab needs the source checkout plus a Python environment:\n"
                "    git clone https://github.com/ndl-lab/ndlocr-lite <root>\\cli\n"
                "    py -3.11 -m venv <root>\\venv\n"
                "    <root>\\venv\\Scripts\\python -m pip install -r <root>\\cli\\requirements.txt\n"
                "\n"
                "so that both <root>\\cli\\src\\ocr.py and <root>\\venv\\Scripts\\python.exe\n"
                "exist, then point Settings > NDLOCR-Lite folder at <root>.\n"
                "Python 3.10 or newer is required. Keep the path free of full-width characters."
            )
        missing = []
        if not self.python:
            missing.append(
                "a Python interpreter (looked for venv\\Scripts\\python.exe, "
                "venv/bin/python, .venv\\Scripts\\python.exe under the root)"
            )
        if not self.cli:
            missing.append(
                "the CLI entry point (looked for cli\\src\\ocr.py, src\\ocr.py, ocr.py)"
            )
        return (
            f"Found an NDLOCR-Lite folder at {self.root} but not "
            + ", and not ".join(missing)
            + ".\n\n"
            + self.describe()
            + "\n\nIf the interpreter you want lives somewhere else, replace {python} in\n"
              "Settings > NDLOCR-Lite command with its full path."
        )


def candidate_roots(settings: Settings) -> list[Path]:
    out: list[Path] = []
    if settings.ndlocr_dir:
        out.append(Path(settings.ndlocr_dir))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        out.append(Path(local) / "ndlocr-lite")
    out.append(Path.home() / "ndlocr-lite")
    out.append(Path("C:/ndlocr-lite"))
    return out


def find_install(settings: Settings) -> Optional[Install]:
    """Locate NDLOCR-Lite, or return None so the caller can explain the gap.

    A partial install must not mask a complete one further down the list. An
    unpacked desktop app in the configured folder used to short-circuit the
    search and hide a working checkout in %LOCALAPPDATA%, so the first *usable*
    root wins and a partial one is only returned when nothing better exists.
    """
    partial: Optional[Install] = None
    for root in candidate_roots(settings):
        if not root.is_dir():
            continue
        python = None
        for rel in ("venv/Scripts/python.exe", "venv/bin/python", ".venv/Scripts/python.exe"):
            p = root / rel
            if p.is_file():
                python = p
                break
        cli = None
        # ndl-lab/ndlocr-lite puts the entry point at src/ocr.py; the earlier
        # layouts are kept so an older install still resolves.
        for rel in ("cli/src/ocr.py", "src/ocr.py", "ocr.py",
                    "cli/main.py", "cli/run_ndlocr.py", "cli/src/main.py", "main.py"):
            p = root / rel
            if p.is_file():
                cli = p
                break
        gui = None
        win = root / "windows"
        if win.is_dir():
            exes = sorted(win.glob("*.exe"))
            gui = exes[0] if exes else None
        install = Install(root=root, python=python, cli=cli, gui_exe=gui)
        if install.usable:
            return install
        if partial is None and (python or cli or gui):
            partial = install
    return partial


def source_flag(src: Path) -> str:
    """Which of NDLOCR-Lite's three input flags this source needs."""
    if src.is_dir():
        return "--sourcedir"
    if src.suffix.lower() == ".pdf":
        return "--sourcepdf"
    return "--sourceimg"


def build_command(template: str, install: Install, src: Path, out_dir: Path) -> list[str]:
    """Fill the configured template.

    Placeholders: {python} {cli} {srcarg} {input} {output}.

    Never substitutes a bare `python` for a missing interpreter: on Windows that
    resolves to the App Execution alias, which prints a Microsoft Store advert
    and exits 9009 - a failure that looks like NDLOCR-Lite's fault and is not.
    """
    if not install.python or not install.cli:
        raise RuntimeError(install.problem())
    mapping = {
        "python": str(install.python),
        "cli": str(install.cli),
        "srcarg": source_flag(src),
        "input": str(src),
        "output": str(out_dir),
    }
    parts: list[str] = []
    for token in template.split():
        for k, v in mapping.items():
            token = token.replace("{" + k + "}", v)
        parts.append(token)
    return [p for p in parts if p]


def run_ocr(
    src: Path,
    out_dir: Path,
    *,
    settings: Settings,
    log: Callable[[str], None] = lambda _m: None,
) -> Path:
    """Run NDLOCR-Lite over a file or a directory of page images."""
    install = find_install(settings)
    if install is None:
        raise RuntimeError(
            "NDLOCR-Lite was not found. Install it, then set its folder in "
            "Settings > Local OCR. The app looked in: "
            + ", ".join(str(p) for p in candidate_roots(settings))
        )
    if not install.usable:
        raise RuntimeError(install.problem())

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = build_command(settings.ndlocr_cmd, install, src, out_dir)
    log("running: " + " ".join(cmd))

    proc = subprocess.Popen(
        cmd,
        cwd=str(install.cli.parent if install.cli else install.root),  # models resolve relative to the script
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log(line.rstrip())
    code = proc.wait()
    if code != 0:
        hint = ""
        if code == 9009:
            hint = (
                "\n\nExit code 9009 means Windows could not find the program at all - "
                "nothing ran.\nIf the log above says \"Python was not found\", the command "
                "hit the Microsoft Store\napp-execution alias instead of a real interpreter. "
                "Put the full path to a\npython.exe into Settings > NDLOCR-Lite command in "
                "place of {python}."
            )
        raise RuntimeError(
            f"NDLOCR-Lite exited with code {code}.{hint}\n\n"
            f"Command: {' '.join(cmd)}\n"
            f"{install.describe()}\n\n"
            "The command template is in Settings > Local OCR if it needs adjusting "
            "for your install."
        )
    return out_dir


# --------------------------------------------------------------------------
# importing OCR output into the pipeline's own shape
# --------------------------------------------------------------------------

_NUM = re.compile(r"(\d+)")


def _sort_key(p: Path):
    nums = [int(n) for n in _NUM.findall(p.stem)]
    return (nums or [0], p.stem)


def import_text_dir(
    text_dir: Path,
    out_dir: Path,
    *,
    label: str,
    source_note: str,
    frames_per_chunk: int = 15,
    log: Callable[[str], None] = lambda _m: None,
) -> tuple[Path, list[Path]]:
    """Fold a directory of per-page .txt files into transcription + chunks.

    One file becomes one frame, in numeric filename order. This is the join
    between the local-OCR path and everything downstream.
    """
    files = sorted((p for p in text_dir.rglob("*.txt")), key=_sort_key)
    if not files:
        raise RuntimeError(f"No .txt output found under {text_dir}")

    out_dir.mkdir(parents=True, exist_ok=True)
    head = [
        "=" * 78,
        f"{label} - transcription from local NDLOCR-Lite output (uncorrected machine output)",
        f"Source      : {source_note}",
        "Attribution : text produced locally by NDLOCR-Lite; the images are the rights holder's.",
        f"Pages       : {len(files)} text files, ordered by filename",
        "=" * 78,
        "",
    ]

    blocks: list[str] = []
    for n, f in enumerate(files, start=1):
        text = f.read_text(encoding="utf-8", errors="replace").strip()
        blocks.append(
            "\n".join([
                f"=== Frame {n} ===",
                f"SOURCE FILE: {f.name}",
                text if text else "(no OCR text on this page)",
                "",
            ]) + "\n"
        )

    transcription = out_dir / "local_transcription_ja.txt"
    transcription.write_text("\ufeff" + "\n".join(head) + "".join(blocks), encoding="utf-8")

    chunk_dir = out_dir / "chunks"
    chunk_dir.mkdir(exist_ok=True)
    for old in chunk_dir.glob("chunk_*.txt"):
        old.unlink()
    chunks: list[Path] = []
    for i in range(0, len(blocks), frames_per_chunk):
        p = chunk_dir / f"chunk_{i // frames_per_chunk + 1:02d}.txt"
        p.write_text("\ufeff" + "".join(blocks[i:i + frames_per_chunk]), encoding="utf-8")
        chunks.append(p)

    log(f"imported {len(files)} pages into {transcription.name} and {len(chunks)} chunk file(s)")
    return transcription, chunks


def count_images(path: Path) -> int:
    if path.is_file():
        return 1 if path.suffix.lower() in IMAGE_SUFFIXES else 0
    return sum(1 for p in path.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)
