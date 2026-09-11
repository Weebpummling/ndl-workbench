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
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import Settings

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".pdf"}


def _is_store_alias(exe: Path) -> bool:
    """Whether this is the Microsoft Store app-execution stub, not an interpreter.

    Windows ships zero-byte reparse points under WindowsApps that advertise the
    Store instead of running anything. Executing one is what produced the 9009
    failure, so every interpreter this module accepts is checked against it.
    """
    try:
        if "windowsapps" in str(exe).lower():
            return True
        return exe.stat().st_size == 0
    except OSError:
        return True


# Python versions on which NDLOCR-Lite's pinned requirements are known to
# install on Windows. Checked against tag 1.3.1 (10 Sep 2026): 3.10-3.12 from
# wheels alone; 3.13 has no PyYAML==6.0.1 wheel but that sdist builds pure
# Python without a compiler. 3.14 - python.org's default download - has no
# wheels for numpy, lxml or PyYAML, so pip tries to compile them and fails
# after a long wall of output. The range only ranks candidates and words the
# advice; pip's own dry run in install() is the gate, so an upstream pin bump
# that adds 3.14 wheels needs no change here.
NDLOCR_PYTHON_MIN = (3, 10)
NDLOCR_PYTHON_MAX = (3, 13)

# Pinned packages whose source distribution builds without a compiler, so a
# missing wheel for them is not a reason to refuse an interpreter. Anything
# else without a wheel is refused: compiling numpy or lxml on a user's machine
# is exactly the failure this exists to prevent.
PURE_PYTHON_SDISTS = {"pyyaml"}


def _fmt_version(v: tuple[int, int]) -> str:
    return f"{v[0]}.{v[1]}"


def supported_python(version: tuple[int, int]) -> bool:
    """Whether the pinned requirements are known to install on this version."""
    return NDLOCR_PYTHON_MIN <= version <= NDLOCR_PYTHON_MAX


def python_version(exe: Path) -> Optional[tuple[int, int]]:
    """(major, minor) of a real interpreter, None for anything that is not one."""
    if not exe.is_file() or _is_store_alias(exe):
        return None
    try:
        out = subprocess.run(
            [str(exe), "-c", "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.split()
    if out.returncode != 0 or len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def _usable_interpreter(exe: Path) -> bool:
    """A real Python at or above NDLOCR-Lite's own floor."""
    v = python_version(exe)
    return v is not None and v >= NDLOCR_PYTHON_MIN


def _run_capture(cmd: list[str]) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout if out.returncode == 0 else ""


def python_candidates() -> list[Path]:
    """Every interpreter this machine offers, likeliest first, no duplicates.

    PATH alone is not enough: a python.org install with "Add to PATH" unticked
    is only known to the `py` launcher, and a machine can hold a usable 3.12
    beside the 3.14 that PATH points at.
    """
    seen: dict[str, Path] = {}

    def add(p: Path) -> None:
        try:
            key = os.path.normcase(str(p.resolve()))
        except OSError:
            key = os.path.normcase(str(p))
        seen.setdefault(key, p)

    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            add(Path(found))
    # " -V:3.13 *   C:\...\python.exe" (3.11+ launcher) or " -3.11-64   C:\...".
    for line in _run_capture(["py", "-0p"]).splitlines():
        m = re.match(r"\s*-\S+\s+\*?\s*(\S.*?python\S*\.exe)\s*$", line, re.IGNORECASE)
        if m:
            add(Path(m.group(1)))
    default = _run_capture(["py", "-3", "-c", "import sys; print(sys.executable)"]).strip()
    if default:
        add(Path(default))
    bases = [Path(os.environ[k]) / "Programs" / "Python" for k in ("LOCALAPPDATA",) if os.environ.get(k)]
    bases += [Path(os.environ[k]) for k in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(k)]
    bases.append(Path("C:/"))
    for base in bases:
        try:
            for p in sorted(base.glob("Python3*/python.exe")):
                add(p)
        except OSError:
            pass
    return list(seen.values())


def ranked_pythons() -> list[tuple[tuple[int, int], Path]]:
    """Usable interpreters, best first: supported versions, newest first, then the rest."""
    out: list[tuple[tuple[int, int], Path]] = []
    for exe in python_candidates():
        v = python_version(exe)
        if v is not None and v >= NDLOCR_PYTHON_MIN:
            out.append((v, exe))
    out.sort(key=lambda t: (not supported_python(t[0]), -t[0][0], -t[0][1]))
    return out


def system_python() -> Optional[Path]:
    """The interpreter already on the machine that NDLOCR-Lite should run under, or None.

    Consulted when the NDLOCR-Lite folder has no environment of its own, and
    by the installer to build one. A supported version wins over a newer one
    wherever it sits on PATH; a newer one is only returned when nothing else
    exists, so install() can check it and explain rather than guess.
    """
    ranked = ranked_pythons()
    return ranked[0][1] if ranked else None


@dataclass
class Install:
    root: Path
    python: Optional[Path]
    cli: Optional[Path]
    gui_exe: Optional[Path]
    # "venv" when the interpreter came from an environment inside the root,
    # "system" when it was already on the machine. Only affects what we say.
    python_source: str = "venv"

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
        if self.python and self.python_source == "system":
            bits.append(f"python: {self.python}  (already on this machine, not a venv)")
        elif self.python:
            bits.append(f"python: {self.python}")
        else:
            bits.append("python: NOT FOUND")
        bits.append(f"cli: {self.cli}" if self.cli else "cli: NOT FOUND")
        if self.gui_exe:
            bits.append(f"gui: {self.gui_exe}  (desktop app - no command line)")
        return "\n".join(bits)

    def problem(self) -> str:
        """Why this install cannot be run, phrased so the user can act on it.

        Written against the real paths, never a `<root>` placeholder, and it
        checks for a Python already on the machine before telling anyone to
        install one - being told to install software you already have is how a
        diagnosis loses the reader.
        """
        if self.usable:
            return ""
        root = self.root
        have = self.python or system_python()

        if self.gui_exe and not self.cli:
            lines = [
                f"{root} holds the NDLOCR-Lite desktop application.",
                "That build is a GUI only - it has no command line, so this",
                "app cannot drive it. Leave it where it is; it is not the problem.",
                "",
                "What is missing is NDLOCR-Lite's source checkout. Add it:",
                "",
                f'  git clone https://github.com/ndl-lab/ndlocr-lite "{root}\\cli"',
            ]
            if have:
                hv = python_version(have)
                lines += [
                    "",
                    f"Python is already here, so no new install is needed:",
                    f"  {have}" + (f"  (Python {_fmt_version(hv)})" if hv else ""),
                ]
                if hv and not supported_python(hv):
                    lines += [
                        "",
                        f"  Note: NDLOCR-Lite's pinned packages are only published for Python "
                        f"{_fmt_version(NDLOCR_PYTHON_MIN)}-{_fmt_version(NDLOCR_PYTHON_MAX)}, so",
                        f"  {_fmt_version(hv)} will most likely fail to install them. "
                        f"Install {_fmt_version(NDLOCR_PYTHON_MAX)} beside it.",
                    ]
                lines += [
                    "",
                    "Recommended - keep NDLOCR-Lite's pinned numpy and onnxruntime",
                    "out of that interpreter by giving it its own environment:",
                    "",
                    f'  "{have}" -m venv "{root}\\venv"',
                    f'  "{root}\\venv\\Scripts\\python" -m pip install -r "{root}\\cli\\requirements.txt"',
                    "",
                    "Or, to use the Python you already have and skip the venv:",
                    "",
                    f'  "{have}" -m pip install -r "{root}\\cli\\requirements.txt"',
                    "",
                    "then put that interpreter's full path in place of {python} in",
                    "Settings > NDLOCR-Lite command.",
                ]
            else:
                lines += [
                    "",
                    "No usable Python was found on this machine either.",
                    PYTHON_ADVICE.replace("press Install NDLOCR-Lite... again.", "run:"),
                    "",
                    f'  py -{_fmt_version(NDLOCR_PYTHON_MAX)} -m venv "{root}\\venv"',
                    f'  "{root}\\venv\\Scripts\\python" -m pip install -r "{root}\\cli\\requirements.txt"',
                ]
            lines += [
                "",
                f"Either way this app needs {root}\\cli\\src\\ocr.py to exist.",
                "Keep the path free of full-width characters.",
            ]
            return "\n".join(lines)

        missing = []
        if not self.python:
            missing.append(
                "a Python interpreter (looked for venv\\Scripts\\python.exe, "
                "venv/bin/python and .venv\\Scripts\\python.exe under the root, "
                "then for one already on this machine)"
            )
        if not self.cli:
            missing.append(
                "the CLI entry point (looked for cli\\src\\ocr.py, src\\ocr.py, ocr.py)"
            )
        out = [
            f"Found an NDLOCR-Lite folder at {root} but not "
            + ", and not ".join(missing) + ".",
            "",
            self.describe(),
        ]
        if have and not self.cli:
            out += [
                "",
                f"Python itself is fine ({have}); it is the checkout that is missing:",
                "",
                f'  git clone https://github.com/ndl-lab/ndlocr-lite "{root}\\cli"',
                f'  "{have}" -m pip install -r "{root}\\cli\\requirements.txt"',
            ]
        else:
            out += [
                "",
                "If the interpreter you want lives somewhere else, replace {python}",
                "in Settings > NDLOCR-Lite command with its full path.",
            ]
        return "\n".join(out)


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
        # A checkout with its dependencies installed into the machine's own
        # Python is a perfectly good install; requiring a venv would refuse to
        # run for someone who already has everything NDLOCR-Lite needs.
        python_source = "venv"
        if python is None and cli is not None:
            fallback = system_python()
            if fallback is not None:
                python, python_source = fallback, "system"
        gui = None
        win = root / "windows"
        if win.is_dir():
            exes = sorted(win.glob("*.exe"))
            gui = exes[0] if exes else None
        install = Install(root=root, python=python, cli=cli, gui_exe=gui,
                          python_source=python_source)
        if install.usable:
            return install
        if partial is None and (python or cli or gui):
            partial = install
    return partial


# --------------------------------------------------------------------------
# installing NDLOCR-Lite
# --------------------------------------------------------------------------

NDLOCR_REPO = "https://github.com/ndl-lab/ndlocr-lite"
NDLOCR_RELEASE_API = "https://api.github.com/repos/ndl-lab/ndlocr-lite/releases/latest"

# Used when the release list cannot be reached. Deliberately a tag and never a
# branch: upstream cuts its releases from a side branch, so `master` trails the
# newest tag by weeks and a clone of it silently installs older code.
NDLOCR_FALLBACK_TAG = "1.3.1"


def _stream(cmd: list[str], *, cwd: Optional[Path], log: Callable[[str], None],
            what: str) -> None:
    """Run a command, echo it into the log, raise with its tail if it fails."""
    log("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        log(line)
        tail = (tail + [line])[-12:]
    if proc.wait() != 0:
        raise RuntimeError(f"{what} failed:\n" + "\n".join(tail))


def latest_release_tag(log: Callable[[str], None] = lambda _m: None) -> str:
    """The newest published NDLOCR-Lite tag, or the pinned fallback."""
    import json
    import urllib.request

    try:
        req = urllib.request.Request(
            NDLOCR_RELEASE_API,
            headers={"User-Agent": "ndl-workbench", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            tag = (json.load(r) or {}).get("tag_name")
        if tag:
            log(f"newest NDLOCR-Lite release: {tag}")
            return str(tag)
    except Exception as e:
        log(f"could not reach the release list ({e}); using {NDLOCR_FALLBACK_TAG}")
    return NDLOCR_FALLBACK_TAG


def _fetch_source(cli_dir: Path, tag: str, log: Callable[[str], None]) -> None:
    """Put the NDLOCR-Lite checkout at `cli_dir`, by git if available or by zip.

    The download is about 300 MB by git or 150 MB as a zip: nearly all of it is
    the four ONNX models, which ship inside the repository rather than being
    fetched at run time. That is the whole reason the desktop application cannot stand in for
    this - it has the models but no command line.
    """
    import tempfile
    import urllib.request
    import zipfile

    cli_dir.parent.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        log(f"cloning {NDLOCR_REPO} at tag {tag} (about 300 MB)")
        _stream(["git", "clone", "--depth", "1", "--branch", tag, NDLOCR_REPO, str(cli_dir)],
                cwd=None, log=log, what="git clone")
        return

    log("git is not installed; downloading the source archive instead (about 150 MB)")
    url = f"{NDLOCR_REPO}/archive/refs/tags/{tag}.zip"
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "ndlocr-lite.zip"
        req = urllib.request.Request(url, headers={"User-Agent": "ndl-workbench"})
        with urllib.request.urlopen(req, timeout=120) as r, open(archive, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = next_mark = 0
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                f.write(block)
                done += len(block)
                if done >= next_mark:
                    log(f"  {done // (1 << 20)} MB"
                        + (f" of {total // (1 << 20)} MB" if total else ""))
                    next_mark = done + (25 << 20)
        log(f"extracting {archive.name}")
        with zipfile.ZipFile(archive) as z:
            z.extractall(td)
        inner = [p for p in Path(td).iterdir() if p.is_dir() and p.name.startswith("ndlocr-lite")]
        if not inner:
            raise RuntimeError("the downloaded archive did not contain a checkout")
        shutil.move(str(inner[0]), str(cli_dir))


PYTHON_ADVICE = (
    f"Install Python {_fmt_version(NDLOCR_PYTHON_MAX)} (64-bit) from\n"
    "https://www.python.org/downloads/windows/\n"
    "The newest release there is not the one to pick, and the Microsoft Store\n"
    "version's stub is what produces \"Python was not found\". Tick \"Add\n"
    "python.exe to PATH\", then press Install NDLOCR-Lite... again."
)


def _fetch_requirements(tag: str, log: Callable[[str], None]) -> Optional[str]:
    """requirements.txt for `tag` straight from GitHub, or None when offline.

    A few KB fetched ahead of the 300 MB checkout, so an interpreter pip cannot
    satisfy is refused in seconds instead of after the download.
    """
    import urllib.request

    url = f"https://raw.githubusercontent.com/ndl-lab/ndlocr-lite/{tag}/requirements.txt"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ndl-workbench"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="replace")
    except Exception as e:
        log(f"could not fetch requirements.txt ahead of the checkout ({e})")
        return None


def _venv_python(root: Path) -> Optional[Path]:
    for rel in ("venv/Scripts/python.exe", "venv/bin/python"):
        p = root / rel
        if p.is_file():
            return p
    return None


def _ensure_venv(root: Path, interpreter: Path, log: Callable[[str], None]) -> Path:
    """`<root>\\venv` made from `interpreter`, rebuilt when the one there cannot work.

    A leftover environment from a Python pip could not satisfy - or from a
    Python since uninstalled - would otherwise be reused forever, so the user
    who installs the right Python after a failure gets nowhere.
    """
    existing = _venv_python(root)
    if existing is not None:
        have = python_version(existing)
        want = python_version(interpreter)
        if have is None:
            log(f"the environment at {root / 'venv'} no longer runs; rebuilding it")
        elif have != want and want is not None and supported_python(want) and not supported_python(have):
            log(f"the environment at {root / 'venv'} is Python {_fmt_version(have)}, "
                f"which cannot install NDLOCR-Lite's packages; rebuilding it with {_fmt_version(want)}")
        else:
            log(f"environment already present at {root / 'venv'} (Python {_fmt_version(have)})")
            return existing
        shutil.rmtree(root / "venv", ignore_errors=True)
    log(f"creating the environment at {root / 'venv'}")
    _stream([str(interpreter), "-m", "venv", str(root / "venv")],
            cwd=None, log=log, what="venv creation")
    made = _venv_python(root)
    if made is None:
        raise RuntimeError(f"venv creation left no interpreter under {root / 'venv'}")
    return made


_UNSATISFIED = re.compile(r"Could not find a version that satisfies the requirement\s+([A-Za-z0-9_.\-]+)")


def _pip_flags(allow_source: Iterable[str]) -> list[str]:
    """Wheels only, except the listed pure-Python packages."""
    flags = ["--only-binary=:all:"]
    for name in allow_source:
        flags += ["--no-binary", name]
    return flags


def _preflight(venv_python: Path, requirements: Path, *, interpreter: Path,
               root: Path, log: Callable[[str], None]) -> list[str]:
    """Prove pip can satisfy the pins for this interpreter before anything heavy runs.

    Returns the packages that must come from source (see PURE_PYTHON_SDISTS).
    Raises with the advice when a pin has no wheel and is not one of those -
    on the interpreter this happens on, pip would otherwise attempt a C build
    and fail after minutes of output the user cannot read.
    """
    version = python_version(venv_python)
    allow: list[str] = []
    for _ in range(len(PURE_PYTHON_SDISTS) + 1):
        log("checking that prebuilt packages exist for this Python"
            + (f" (building {', '.join(allow)} from source)" if allow else ""))
        try:
            _stream([str(venv_python), "-m", "pip", "install", "--dry-run", "--ignore-installed",
                     "--quiet", *_pip_flags(allow), "-r", str(requirements)],
                    cwd=None, log=log, what="dependency check")
            return allow
        except RuntimeError as e:
            m = _UNSATISFIED.search(str(e))
            name = m.group(1) if m else None
            if name and name.lower() in PURE_PYTHON_SDISTS and name.lower() not in [a.lower() for a in allow]:
                allow.append(name)
                continue
            shutil.rmtree(root / "venv", ignore_errors=True)
            detail = [ln for ln in str(e).splitlines() if ln.startswith("ERROR:")][-2:]
            ver = f"Python {_fmt_version(version)}" if version else "This Python"
            raise RuntimeError(
                f"{ver} ({interpreter}) cannot install NDLOCR-Lite's pinned packages:\n"
                + "\n".join("  " + d for d in detail) + "\n\n"
                "There are no prebuilt packages for it, and compiling them here would fail.\n"
                f"Nothing was downloaded; the unusable environment at {root / 'venv'} was removed.\n\n"
                + PYTHON_ADVICE
            ) from None
    raise RuntimeError("dependency check did not settle")  # unreachable in practice


def install(root: Path, *, python: Optional[Path] = None,
            log: Callable[[str], None] = lambda _m: None) -> Install:
    """Install NDLOCR-Lite under `root` so the Local OCR tab can drive it.

    Creates `<root>\\cli` (the checkout, models included) and `<root>\\venv`
    (its dependencies), which is exactly what `find_install` looks for. Both
    steps are skipped if already present, so this is safe to run again after a
    failure part-way through.

    Order matters: the environment is built and pip asked to resolve the pins
    before the 300 MB checkout is fetched, because the failure this guards
    against - a Python newer than the pins have wheels for - is otherwise
    discovered last.
    """
    interpreter = python or system_python()
    if interpreter is None:
        raise RuntimeError(
            "No usable Python was found on this machine.\n\n" + PYTHON_ADVICE
        )
    version = python_version(interpreter)
    if version is None:
        raise RuntimeError(f"{interpreter} does not run as a Python interpreter.\n\n" + PYTHON_ADVICE)
    log(f"using {interpreter} (Python {_fmt_version(version)})")
    if not supported_python(version):
        log(f"note: NDLOCR-Lite's pinned packages are known to install on Python "
            f"{_fmt_version(NDLOCR_PYTHON_MIN)}-{_fmt_version(NDLOCR_PYTHON_MAX)}; "
            f"checking {_fmt_version(version)} before downloading anything")

    root.mkdir(parents=True, exist_ok=True)
    cli_dir = root / "cli"
    have_checkout = (cli_dir / "src" / "ocr.py").is_file()
    tag: Optional[str] = None
    if have_checkout:
        log(f"checkout already present at {cli_dir}")
    elif cli_dir.exists() and any(cli_dir.iterdir()):
        raise RuntimeError(
            f"{cli_dir} already exists but has no src\\ocr.py in it.\n"
            "Move or delete that folder and run this again."
        )
    else:
        tag = latest_release_tag(log)

    requirements = cli_dir / "requirements.txt"
    early = root / "requirements-check.txt"
    if not have_checkout and tag is not None:
        text = _fetch_requirements(tag, log)
        if text is not None:
            early.write_text(text, encoding="utf-8")
            requirements = early

    try:
        venv_python = _ensure_venv(root, interpreter, log)
        _stream([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"],
                cwd=None, log=log, what="pip upgrade")
        allow_source: Optional[list[str]] = None
        if requirements.is_file():
            allow_source = _preflight(venv_python, requirements, interpreter=interpreter, root=root, log=log)
    finally:
        if early.exists():
            early.unlink()

    if not have_checkout:
        assert tag is not None
        _fetch_source(cli_dir, tag, log)
    requirements = cli_dir / "requirements.txt"
    if not requirements.is_file():
        raise RuntimeError(f"no requirements.txt under {cli_dir}")
    if allow_source is None:
        allow_source = _preflight(venv_python, requirements, interpreter=interpreter, root=root, log=log)

    log("installing dependencies (a few hundred MB, this is the slow part)")
    _stream([str(venv_python), "-m", "pip", "install", *_pip_flags(allow_source), "-r", str(requirements)],
            cwd=None, log=log, what="dependency install")

    log("verifying")
    _stream([str(venv_python), str(cli_dir / "src" / "ocr.py"), "--version"],
            cwd=cli_dir / "src", log=log, what="verification")

    result = Install(root=root, python=venv_python, cli=cli_dir / "src" / "ocr.py",
                     gui_exe=None, python_source="venv")
    log("NDLOCR-Lite is installed and runnable:\n" + result.describe())
    return result


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
