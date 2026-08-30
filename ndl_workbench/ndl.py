"""NDL access layer: search, book record, fulltext OCR, rights probe.

Two hosts are involved and they behave differently:

  lab.ndl.go.jp/dl/api/...  the Next-Gen Digital Library. Its index covers only
      items NDL has published to the open internet, and every item in it has
      machine OCR we can fetch. This is what the search bar searches, because
      a hit here is by definition a volume this pipeline can process end to end.

  dl.ndl.go.jp/...          the main Digital Collections. Restricted items live
      here. We touch it only to check an item's status, never to work around it.

The rights gate is on the item, not on the caller: a restricted pid returns
403 "This PID is not allowed" from the fulltext endpoint no matter who asks,
and signing in to a personal account does not change that. So `probe_pid`
reports the state plainly and the GUI routes the user to the local-OCR path
instead of pretending there is a way through.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .config import USER_AGENT

LAB = "https://lab.ndl.go.jp/dl/api"
DL = "https://dl.ndl.go.jp"

# NDL throttles bursts hard (the workstation has seen 429s from per-cell tile
# fetches). One request at a time, with backoff, and never a parallel sweep.
_MIN_INTERVAL_S = 0.34
_last_request_at = 0.0


class NDLError(RuntimeError):
    """A request failed in a way the user needs to hear about verbatim."""


class RestrictedItem(NDLError):
    """The pid exists but its OCR is not publicly available."""


def _throttle() -> None:
    global _last_request_at
    wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _get(url: str, *, timeout: int = 60, retries: int = 3) -> bytes:
    """GET with an identifying UA, pacing, and backoff on 429/5xx."""
    last: Optional[Exception] = None
    for attempt in range(retries):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()[:200]
            except Exception:
                pass
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                # 500 from the book endpoint is how the lab API answers for a
                # pid outside its index, so a retry is cheap and settles it.
                time.sleep(2 ** attempt * 1.5)
                last = e
                continue
            raise NDLError(
                f"HTTP {e.code} from {url}"
                + (f" - {body.decode('utf-8', 'replace').strip()}" if body else "")
            ) from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 1.5)
                last = e
                continue
            raise NDLError(f"Network error reaching {url}: {e.reason}") from e
    raise NDLError(f"Gave up on {url}: {last}")


# --------------------------------------------------------------------------
# pid parsing
# --------------------------------------------------------------------------

_PID_IN_URL = re.compile(r"(?:info:ndljp/pid/|dl\.ndl\.go\.jp/(?:pid|info:ndljp/pid)/)(\d+)")


def parse_pid(text: str) -> Optional[str]:
    """Pull a pid out of whatever the user pasted: bare number, or any NDL URL."""
    s = (text or "").strip()
    if not s:
        return None
    if s.isdigit():
        return s
    m = _PID_IN_URL.search(s)
    return m.group(1) if m else None


# --------------------------------------------------------------------------
# search
# --------------------------------------------------------------------------


@dataclass
class SearchHit:
    pid: str
    title: str
    volume: str
    author: str
    publisher: str
    year: str
    call_no: str
    pages: int

    @property
    def display_title(self) -> str:
        return f"{self.title} {self.volume}".strip()


def search(keyword: str, *, size: int = 20, offset: int = 0) -> tuple[list[SearchHit], int]:
    """Search the Next-Gen Digital Library index.

    Returns (hits, total). Every hit is OCR-available by construction, which is
    the whole point of searching this index rather than NDL Search: the user
    only sees volumes the rest of the app can actually process.
    """
    q = urllib.parse.urlencode({"keyword": keyword, "size": size, "from": offset})
    data = json.loads(_get(f"{LAB}/book/search?{q}").decode("utf-8"))
    hits = []
    for e in data.get("list", []):
        hits.append(
            SearchHit(
                pid=str(e.get("id", "")),
                title=str(e.get("title") or ""),
                volume=str(e.get("volume") or ""),
                author=str(e.get("responsibility") or ""),
                publisher=str(e.get("publisher") or ""),
                year=str(e.get("published") or ""),
                call_no=str(e.get("callNo") or ""),
                pages=int(e.get("page") or 0),
            )
        )
    return hits, int(data.get("hit", 0))


# --------------------------------------------------------------------------
# per-volume fetches
# --------------------------------------------------------------------------


def book_record(pid: str) -> dict[str, Any]:
    """Bibliographic record + table-of-contents anchors for one volume."""
    try:
        return json.loads(_get(f"{LAB}/book/{pid}").decode("utf-8"))
    except NDLError as e:
        if "HTTP 500" in str(e) or "HTTP 404" in str(e):
            raise RestrictedItem(
                f"pid {pid} is not in the Next-Gen Digital Library index - it is "
                f"most likely a transmission-service (restricted) item, so there "
                f"is no public OCR to fetch."
            ) from e
        raise


def fulltext(pid: str) -> dict[str, Any]:
    """NDL's own machine OCR for the whole volume, with per-line coordinates."""
    try:
        return json.loads(_get(f"{LAB}/book/fulltext-json/{pid}").decode("utf-8"))
    except NDLError as e:
        if "HTTP 403" in str(e):
            raise RestrictedItem(
                f"NDL refused the OCR for pid {pid}: \"This PID is not allowed\". "
                f"The fulltext API serves internet-public items only, and the "
                f"allowlist keys on the item's rights tier - signing in does not "
                f"open it. Use the Local OCR tab with images you have obtained "
                f"legitimately."
            ) from e
        raise


@dataclass
class PidStatus:
    pid: str
    ocr_available: bool
    title: str
    detail: str


def probe_pid(pid: str) -> PidStatus:
    """Decide, with one cheap request, whether this pid can be processed."""
    try:
        rec = book_record(pid)
    except RestrictedItem as e:
        return PidStatus(pid, False, "", str(e))
    except NDLError as e:
        return PidStatus(pid, False, "", str(e))
    title = " ".join(x for x in [str(rec.get("title") or ""), str(rec.get("volume") or "")] if x)
    detail = " / ".join(
        x for x in [
            str(rec.get("responsibility") or ""),
            str(rec.get("publisher") or ""),
            str(rec.get("published") or ""),
        ] if x
    )
    return PidStatus(pid, True, title.strip(), detail)


def viewer_url(pid: str, frame: int | None = None) -> str:
    return f"{DL}/pid/{pid}" + (f"/1/{frame}" if frame else "")


# --------------------------------------------------------------------------
# caching wrapper
# --------------------------------------------------------------------------


def cached_fetch(
    path,
    fetch: Callable[[], dict[str, Any]],
    *,
    log: Callable[[str], None] = lambda _m: None,
) -> dict[str, Any]:
    """Fetch once per volume, ever. Re-reads the file on every later run.

    NDL is a public institution serving scans for free; the pipeline should hit
    each endpoint once per volume and then leave it alone.
    """
    if path.is_file():
        log(f"cached: {path.name}")
        return json.loads(path.read_text(encoding="utf-8"))
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log(f"fetched: {path.name}")
    return data
