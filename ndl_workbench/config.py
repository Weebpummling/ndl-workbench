"""Settings for the NDL Workbench desktop app.

One JSON file, next to the data home, holding the handful of paths and knobs a
user can legitimately want to change. Everything has a working default, so a
fresh install runs without the user opening settings at all.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "NDL Workbench"

# Politeness: NDL sees this on every request. Keep it identifying and honest -
# the ingestion tooling in this repo has used the same string since Spike B.
USER_AGENT = "jp-vertical-ocr-optimization (research ingestion; polite, cached)"


def default_data_home() -> Path:
    """The project data home. JP_OCR_DATA wins; else the documented default."""
    env = os.environ.get("JP_OCR_DATA")
    if env:
        return Path(env)
    return Path.home() / "jp-ocr-data"


def settings_path() -> Path:
    return default_data_home() / "ndl-workbench-settings.json"


@dataclass
class Settings:
    # Where volumes land: <data_home>/manuals/ndl-<pid>/
    data_home: str = field(default_factory=lambda: str(default_data_home()))

    # NDLOCR-Lite. Empty means "not configured"; the OCR tab then explains how
    # to point at it rather than failing with a path error.
    ndlocr_dir: str = ""
    # Placeholders: {python} {cli} {srcarg} {input} {output}. {srcarg} resolves
    # to --sourceimg, --sourcedir or --sourcepdf depending on what was chosen.
    # This is a setting rather than a constant because NDLOCR-Lite's entry point
    # has moved between versions; a wrong guess baked into a binary is
    # undiagnosable from the GUI.
    ndlocr_cmd: str = "{python} {cli} {srcarg} {input} --output {output}"

    # Translation. The key may also come from the environment or an `ant auth
    # login` profile - leave blank to let the SDK resolve credentials itself.
    anthropic_api_key: str = ""
    model: str = "claude-opus-5"
    effort: str = "high"          # low | medium | high | xhigh | max
    frames_per_chunk: int = 15

    # Rendering
    latin_font: str = "Georgia"
    cjk_font: str = "Yu Mincho"

    def manuals_dir(self) -> Path:
        return Path(self.data_home) / "manuals"

    def volume_dir(self, pid: str, slug: str = "") -> Path:
        name = f"ndl-{pid}" + (f"-{slug}" if slug else "")
        return self.manuals_dir() / name

    @classmethod
    def load(cls) -> "Settings":
        p = settings_path()
        if p.is_file():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return cls()
            known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
            return cls(**{k: v for k, v in raw.items() if k in known})
        return cls()

    def save(self) -> Path:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
        return p
