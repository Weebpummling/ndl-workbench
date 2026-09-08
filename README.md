# NDL Workbench

A small Windows desktop tool for working with volumes in the [National Diet
Library Digital Collections](https://dl.ndl.go.jp/). Paste a reference number,
and it will pull NDL's own OCR into a clean per-frame transcription, optionally
translate it to English, and render a Word document you can actually read.

It was written for a research project transcribing early-20th-century Japanese
military manuals, and it is opinionated in the ways that work demanded:
provenance is recorded on every page, machine output is labelled as machine
output, and the tool never pretends to a certainty it does not have.

```
Search  →  1. Fetch transcription  →  2. Translate → DOCX
```

---

## What you get

For each volume, one folder:

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

The DOCX puts every frame under a Heading 2, so Word's navigation pane becomes a
clickable frame index — the thing that makes a 100-page translation usable.

### Why there are two Japanese files

Because the shape that is faithful to the page and the shape a translator can
read are not the same shape.

`<pid>_transcription_ja.txt` mirrors the scan: one output line per line NDL
detected, so line *n* here is line *n* on the page. Use it to check a reading
against the image.

`<pid>_reading_ja.txt` is the same words, reshaped for reading and for machine
translation:

- **Furigana removed.** NDL's OCR emits every ruby column as its own line,
  interleaved with the body text. In one 1904 volume that is **65% of all text
  lines** — paste the raw file into a translator and two thirds of what it reads
  is disconnected katakana. Ruby is found from the line geometry NDL ships with
  the text (a ruby column is markedly thinner than a body column), not by
  guessing from the characters.
- **Wrapped lines rejoined.** Each printed line is a column-width fragment, not
  a sentence; fragments are joined and broken again at sentence-ending
  punctuation and numbered items.

No characters are altered — lines are dropped or joined, and what survives is
exactly what NDL read. The chunks and the paste-sized pieces are cut from the
reading text, which also makes the paid path cheaper: 199 KB against 370 KB for
that same volume.

Pages without sentence punctuation — plate captions, insignia charts,
advertisements — still come out as runs of labels; there is nothing to break on.
Prose pages come out clean.

## Install

Download the latest [release](../../releases), unzip it anywhere, and run
**`NDL Workbench.exe`**. There is no installer and nothing to configure to get
started.

The binaries are unsigned, so Windows SmartScreen will warn on first run:
*More info → Run anyway*. If you would rather not trust a stranger's binary,
build it yourself — see below; it is one script.

`ndl-workbench-cli.exe` in the same zip is the same program with a console, for
scripting:

```
ndl-workbench-cli.exe --selftest
ndl-workbench-cli.exe --fetch 843085 --page-map "27:-54;38:?;52:-70;173:-345"
```

## The search bar

It takes three kinds of input and works out which is which:

| Input | Example |
|---|---|
| A reference number (PID) | `843085` |
| Any NDL URL | `https://dl.ndl.go.jp/pid/843085/1/170` |
| A phrase | `歩兵操典` |

Keyword search runs against the Next-Gen Digital Library index. That is the
useful index to search from here, because everything in it is internet-public
and already has OCR — so every hit is a volume this tool can carry all the way
to a finished document.

## Page mapping

A frame of a scan is usually one two-page spread, so each frame is labelled with
the printed pages it holds. The mapping is fitted from the volume's
table-of-contents anchors, which is right for a plainly printed book and wrong
for one with plates bound into the middle or an appendix that restarts at 1.

When the fit is bad, the tool says so instead of quietly emitting wrong page
numbers, and you can write the segments by hand:

```
27:-54;38:?;52:-70;173:-345
```

After every fetch it checks its own mapping against the page numbers the OCR
picked up off the paper, and reports the tally: `61 confirmed, 0 unconfirmed`.

## Translation

Optional, and it costs money: step 2 sends the chunks to the
[Anthropic API](https://docs.anthropic.com/) and writes English markdown, then
renders the DOCX. The tool asks before it starts and reports token usage when it
finishes. Credentials come from `ANTHROPIC_API_KEY`, an `ant auth login`
profile, or a key you paste into Settings.

The source is uncorrected machine OCR, and the translation is instructed to say
so rather than paper over it: `[illegible]` where NDL's OCR emitted 〓, and
`[OCR uncertain]` where the text will not support a confident reading. Fold-out
tables and dense advertisement pages are the weakest spots.

### No key? Use the free web translators

The **Free translators** tab does the same job by hand, for nothing. Every free
service caps how much text one box will take, so the app cuts the transcription
into pieces that fit and hands them to you one at a time: *Prepare pieces* →
*Copy to clipboard* → paste into [DeepL](https://www.deepl.com/translator#ja/en/),
[Google Translate](https://translate.google.com/?sl=ja&tl=en&op=translate),
[Papago](https://papago.naver.com/?sk=ja&tk=en),
[Bing](https://www.bing.com/translator/?from=ja&to=en) or
[Yandex](https://translate.yandex.com/?source_lang=ja&target_lang=en) → paste the
English into your own file. *Copy and next* advances and copies in one click.

The pieces keep their `--- Frame N ---` markers, so the English lines back up
with the Japanese; save the finished result as markdown and use **Render DOCX
from markdown…**. These are other companies' services and what you paste goes to
them — fine for public-domain NDL material, not for anything you are not free to
share.

## Restricted items

Many volumes are in NDL's collection but not published to the open internet. For
those, the fulltext API answers `HTTP 403 — This PID is not allowed`.

**That gate is on the item's rights tier, not on your account.** Signing in to a
personal transmission-service account gets you the viewer; it does not open the
text API. This tool will not try to work around it, and neither should you.

The legitimate route is to obtain page images by a means you are entitled to use
— NDL's remote-copy service, another holding library, your own photography — and
read them locally. The **Local OCR** tab drives
[NDLOCR-Lite](https://github.com/ndl-lab/ndlocr-lite), NDL's own CPU OCR
(CC BY 4.0, about a second a page), and folds the result into the same per-frame
shape as the online path, so translation and rendering work on it unchanged.

NDLOCR-Lite is **not bundled** — it is NDL's software under its own licence.
**Install NDLOCR-Lite…** in that tab fetches and sets it up for you;
[MANUAL.md](MANUAL.md) §5 has the manual commands. NDL's standalone Windows
build is a GUI with no command line and cannot be driven from here.

## Build from source

Needs Python 3.10+ and Windows.

```
git clone https://github.com/Weebpummling/ndl-workbench
cd ndl-workbench
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

That creates a private virtual environment, installs the dependencies into it,
runs the self-test, packages both binaries with PyInstaller and smoke-tests the
result. Nothing touches your system Python. Binaries land in `dist\`.

To run without packaging:

```
pip install -r requirements.txt
python -m ndl_workbench
```

## Manners toward NDL

This talks to a public institution's servers, on behalf of a project that
depends on their generosity. Three rules are built in:

- **One fetch per volume, ever.** Raw JSON is cached in the volume folder and
  re-read afterwards.
- **Requests are paced and backed off.** NDL returns HTTP 429 to bursts; the
  tool spaces its calls and retries with increasing delays.
- **The User-Agent identifies the project.** Requests are not disguised.

Please leave those alone. Text and images from the Digital Collections remain
the National Diet Library's, under their terms of use; cite the frame URL.

## Licence

MIT — see [LICENSE](LICENSE). This covers the tool only.

NDLOCR-Lite is CC BY 4.0 and belongs to the National Diet Library. Content
retrieved from the Digital Collections is governed by NDL's own terms.
