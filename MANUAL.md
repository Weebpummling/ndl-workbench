# NDL Workbench — manual

A desktop front end for the three things this project does to a National Diet
Library volume: pull the text, translate it, and hand back a document you can
read. Paste a reference number, press Search, work down the buttons.

---

## 1. What it does, in order

| Step | Button | Result |
|---|---|---|
| Find | **Search** | A reference number, an NDL URL, or a keyword phrase |
| 1 | **Fetch transcription** | `<pid>_transcription_ja.txt` — NDL's own OCR, one block per frame, plus `chunks/` |
| 2 | **Translate → DOCX** | `<pid>_translation_en.md` and `<pid>_translation_en.docx` |
| — | **Render DOCX from markdown…** | A `.docx` from any markdown file, if you translated elsewhere |

Everything for one volume lands in a single folder:

```
<data home>\manuals\ndl-<pid>-<slug>\
    ndl_book_raw.json              cached bibliographic record + TOC anchors
    ndl_fulltext_raw.json          cached OCR (fetched once, ever)
    <pid>_transcription_ja.txt     the Japanese, one line per OCR line (archival)
    <pid>_reading_ja.txt           the same text, ruby stripped and sentences rejoined
    chunks\chunk_01.txt …          translation-sized pieces, cut from the reading text
    <pid>_translation_en.md        the English
    <pid>_translation_en.docx      the readable deliverable
```

The data home defaults to `%JP_OCR_DATA%`, or `C:\Users\<you>\jp-ocr-data` if
that variable is not set. Change it in **Settings**.

---

## 2. The search bar

It takes three kinds of input and works out which is which.

**A reference number** — `843085`. The app checks the item and, if its text is
public, shows one row ready to fetch.

**Any NDL URL** — `https://dl.ndl.go.jp/pid/843085/1/170`. The pid is pulled out
of it; the frame number is ignored. `info:ndljp/pid/843085` works too.

**A phrase** — `歩兵操典`, `軍隊内務書`, `陸軍 衛生`. This searches the Next-Gen
Digital Library index. That index is the useful one to search here because
everything in it is internet-public and has OCR, so every hit is a volume this
app can carry all the way to a DOCX. Results show pid, title, author, publisher,
year and frame count; double-click a row to fetch it.

A search that finds nothing is not proof the volume does not exist — it may be
in NDL's collection but outside the public index. See §5.

---

## 2a. Two Japanese files, and which one you want

Step 1 writes the volume's text twice, because the shape that is faithful to the
page and the shape a translator can read are not the same shape.

**`<pid>_transcription_ja.txt` — the archival record.** One output line per line
NDL detected on the page, with the frame URL, printed-page label and any
table-of-contents anchor above each frame. This is the file to use when checking
a reading against the scan: line *n* here is line *n* on the page.

**`<pid>_reading_ja.txt` — the reading text.** The same words, reshaped:

- **Furigana removed.** These volumes are printed with ruby beside most kanji,
  and NDL's OCR emits every ruby column as its own line, interleaved with the
  body. In *Senji heisotsu kyōkasho* (pid 843085) that is **65% of all text
  lines** — paste the raw file into a translator and two thirds of what it reads
  is disconnected katakana. Ruby is identified from the line geometry NDL ships
  with the text (a ruby column is markedly thinner than a body column), not by
  guessing from the characters.
- **Wrapped lines rejoined.** Every printed line is a column-width fragment, not
  a sentence. Left alone, a translator renders each fragment as its own
  utterance and the grammar collapses. Fragments are joined and broken again at
  sentence-ending punctuation and at numbered items.

No characters are altered by either step — lines are dropped or joined, and what
survives is exactly what NDL read, geta marks included.

The chunk files, and the paste-sized pieces in the Free translators tab, are cut
from the **reading** text. That is also why translation costs less than it used
to: the reading file for pid 843085 is 199 KB against 370 KB line-by-line.

**Where it is weakest.** Pages with no sentence punctuation — plate captions,
insignia charts, advertisement columns, tables of contents — come out as long
runs of labels, because there is nothing to break on. Prose pages, which are the
bulk of most volumes, come out clean. Consecutive headings set without full
stops also arrive joined; line length is deliberately *not* used as a paragraph
signal, because NDL splits one printed column into several boxes wherever ruby
interrupts it, so a short box means "interrupted by furigana" rather than
"paragraph ended".

---

## 3. Page mapping, and why the app nags about it

Each frame of a scan is usually one two-page spread, so the app labels every
frame with the printed page numbers it holds. It works the mapping out from the
volume's table-of-contents anchors.

That fit is right for a plainly printed book and wrong for a book with a plate
section bound into the middle, or an appendix whose numbering restarts at 1.
When the fit is bad the app says so:

```
WARNING: the fitted page mapping is off by up to 276 pages.
```

Fix it by writing the segments by hand in the **Page mapping** box and fetching
again:

```
27:-54;38:?;52:-70;173:-345
```

Read that as: from frame 27, page = 2×frame − 54; from frame 38, an unnumbered
plate insert; from frame 52, page = 2×frame − 70; from frame 173 the appendix
restarts, page = 2×frame − 345. Semicolons separate segments, `?` marks a
stretch with no printed folio.

After each fetch the app checks its own mapping against the page numbers the OCR
actually picked up off the paper and reports the tally:

```
page-map check against printed folios: 61 confirmed, 0 unconfirmed
```

A run of unconfirmed frames means the mapping is still wrong somewhere.

---

## 4. Translation

Step 2 sends the chunk files to the Anthropic API and writes the English
markdown, then renders the DOCX. Frame headings become Heading 2, so Word's
navigation pane doubles as a clickable frame index.

**It costs money.** The app asks before it starts and reports the token usage
per volume when it finishes. A 190-frame volume at the default settings runs to
roughly 13 chunks.

**Credentials.** Leave the key blank in Settings to let the SDK find your
credentials the normal way (`ANTHROPIC_API_KEY`, or a profile from
`ant auth login`). Put a key in Settings only if you need a specific one; it is
stored in plain text in the settings file, so prefer the environment.

**Cancel is safe.** The markdown is written after every chunk, so stopping half
way through leaves a usable partial file rather than nothing.

**What the translation will and won't do.** The source is uncorrected machine
OCR, and the instructions tell the model to mark what it cannot read rather than
invent a plausible sentence. Expect `[illegible]` where NDL's OCR emitted 〓, and
`[OCR uncertain]` where the text does not support a confident reading. Fold-out
tables, dense advertisement pages and heavily ruby-annotated letterpress are the
weakest spots. For citation, check the frame URL against the scan.

### No API key? Use the free web translators

The **Free translators** tab does the same job by hand, for nothing. The only
real obstacle is that every free service caps how much text one box will take,
and a volume is far past that cap — so the app cuts the transcription into
pieces that fit and feeds them to you one at a time.

1. Fetch a volume in the Library tab and leave it selected.
2. Go to **Free translators** → **Prepare pieces from selected volume**. They
   land in `paste\piece_01.txt`, `piece_02.txt`, … inside the volume folder.
3. **Copy to clipboard**, then click one of the service buttons to open it in
   your browser, and paste.
4. Paste the English into a text file of your own. **Copy and next** advances
   and copies in one click, which is the loop you actually repeat.
5. When the volume is done, save your English as one `.md` file and use
   **Render DOCX from markdown…** in the Library tab.

| Service | |
|---|---|
| [DeepL](https://www.deepl.com/translator#ja/en/) | usually the best Japanese → English of the free tier |
| [Google Translate](https://translate.google.com/?sl=ja&tl=en&op=translate) | most generous with volume |
| [Papago](https://papago.naver.com/?sk=ja&tk=en) | Naver; strong on Japanese |
| [Bing Translator](https://www.bing.com/translator/?from=ja&to=en) | |
| [Yandex Translate](https://translate.yandex.com/?source_lang=ja&target_lang=en) | |

The buttons open each service pre-set to Japanese → English where the link
supports it.

**Two things to know.** The pieces keep `--- Frame N ---` markers and drop the
URL and printed-page lines — the markers survive translation and are what let
you line the English back up with the Japanese, while the provenance lines would
just eat into your character budget. And these are other companies' services:
what you paste goes to them, under their terms and their limits. Public-domain
NDL material is unproblematic; material you are not free to share is not.

If a service rejects a paste as too long, lower **characters per piece** and
prepare again. 4,000 is a reasonable starting point.

Nothing stops you using the raw `chunks\` files with some other tool instead —
they are plain UTF-8 with their full frame headers intact.

---

## 5. Restricted items and the Local OCR tab

Some volumes are in NDL's collection but not published to the open internet.
For those, the fulltext API answers:

```
HTTP 403 — This PID is not allowed
```

That gate is on the item's rights tier, not on your account. Signing in to a
personal transmission-service account gets you the viewer; it does not open the
text API, and this app will not try to work around it.

The legitimate route is to obtain page images by a means you are entitled to use
— NDL's remote-copy service, another holding library, your own photography —
and read them locally:

1. **Local OCR** tab → choose the image file or folder → set a job name.
2. **Check NDLOCR-Lite install** first if you have not used it before. The app
   looks in `%LOCALAPPDATA%\ndlocr-lite`, your home folder and `C:\ndlocr-lite`;
   point **Settings → NDLOCR-Lite folder** at it if it lives elsewhere.
3. **Run OCR**. Output goes to `<data home>\manuals\local-<job>\`.
4. The result is folded into the same per-frame transcription and chunk files
   the online path produces, so translation and rendering work on it unchanged.

If you already have OCR text from somewhere else, **Import existing OCR text…**
takes a folder of `.txt` files (one per page, numeric filename order) and builds
the same structure.

### Installing NDLOCR-Lite

NDLOCR-Lite is the National Diet Library's own OCR, published at
[ndl-lab/ndlocr-lite](https://github.com/ndl-lab/ndlocr-lite) under CC BY 4.0.
It runs on the CPU — around a second a page on an ordinary laptop — and the
models ship inside the repository, so there is no separate model download.

Install it where the app already looks, in its own virtual environment so it
cannot disturb any other Python on the machine:

```
git clone --depth 1 https://github.com/ndl-lab/ndlocr-lite "%LOCALAPPDATA%\ndlocr-lite\cli"
python -m venv "%LOCALAPPDATA%\ndlocr-lite\venv"
"%LOCALAPPDATA%\ndlocr-lite\venv\Scripts\python.exe" -m pip install -r "%LOCALAPPDATA%\ndlocr-lite\cli\requirements.txt"
```

It needs Python 3.10 or newer, and about 1.5 GB on disk once the dependencies
are in. Then press **Check NDLOCR-Lite install**: it should report the venv
interpreter and `cli\src\ocr.py`.

NDL also publish a standalone Windows build on their releases page if you would
rather not keep a Python environment around; the app can drive that instead via
the command setting below.

### The command template

The OCR command line is a **setting**, not a constant, because NDLOCR-Lite's
entry point has moved between versions. The default matches the current CLI:

```
{python} {cli} {srcarg} {input} --output {output}
```

`{python}` and `{cli}` come from the detected install, and `{srcarg}` resolves
itself to `--sourceimg`, `--sourcedir` or `--sourcepdf` according to what you
picked — PDFs are read directly, with no need to export the pages first. Adjust
the template if your version differs; the exact command and the tool's own
output both appear in the log pane.

NDLOCR-Lite writes `.txt`, `.json` and `.xml` for each page. The app reads the
`.txt` files, in numeric filename order, one page per frame.

---

## 6. Settings

| Setting | Notes |
|---|---|
| Data home | Where volumes are written. Defaults to `%JP_OCR_DATA%`. |
| NDLOCR-Lite folder | Blank = auto-detect. |
| NDLOCR-Lite command | Placeholders `{python} {cli} {input} {output}`. |
| Anthropic API key | Blank = use the environment or an `ant auth login` profile. |
| Model | `claude-opus-5` by default. |
| Effort | `low` … `max`. Higher costs more and reads harder pages better. |
| Frames per chunk | 15 is a good default; lower it for very dense pages. |
| DOCX fonts | Latin and CJK faces used in the rendered document. |

Settings are stored as JSON at `<data home>\ndl-workbench-settings.json`.

---

## 7. Manners toward NDL

This tool talks to a public institution's servers. Three rules are built in and
worth knowing about:

- **One fetch per volume, ever.** The raw JSON is cached in the volume folder
  and re-read on later runs. Delete the JSON files if you truly need a re-fetch.
- **Requests are paced and backed off.** NDL returns HTTP 429 to bursts; the app
  spaces its calls and retries with increasing delays rather than hammering.
- **The User-Agent identifies the project.** Requests are not disguised.

---

## 8. Troubleshooting

**"This PID is not allowed"** — restricted item. §5.

**A keyword search returns nothing, but the volume exists.** The index only
covers internet-public items. Search NDL Digital Collections in a browser, then
paste the pid; if the app reports no public OCR, it is a §5 case.

**"the fitted page mapping is off by up to N pages"** — §3.

**Translation stops with a rate-limit message.** Finished chunks are already
written. Wait, then run step 2 again.

**"anthropic NOT installed"** in the selftest — that build was packaged without
the translation dependency. The fetch, import and render paths still work.

**Nothing happens when I press a button.** Look at the log pane at the bottom;
every step, and every failure, is written there with a timestamp.

---

## 9. Running it without the window

The same code runs headless, which is useful for scripting a batch of volumes:

```
ndl-workbench-cli.exe --selftest
ndl-workbench-cli.exe --fetch 843085 --page-map "27:-54;38:?;52:-70;173:-345" --chunk 15
```

`--selftest` checks the search API, pid parsing, page mapping, DOCX rendering,
the translation SDK and the NDLOCR-Lite install, and exits non-zero if anything
core is broken.
