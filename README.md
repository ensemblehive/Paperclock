<p align="center">
  <img src="docs/social-preview.png" alt="Paperclock — Your files know what's coming" width="900">
</p>

# Paperclock

Paperclock is a private deadline radar for the commitments hiding in your files.
Drop in a folder and it finds renewals, cancellation windows, expiries, payments,
submissions, appointments, and warranties—then turns them into one explainable
timeline and an `.ics` calendar.

No account. No document upload. No model download. The Python engine runs on your
machine and every result shows the source line and why it was kept.

> **Demo GIF placeholder:** a short folder-drop → timeline → calendar export recording belongs here.

## Why this exists

Important dates rarely live in calendars. They live in policy PDFs, warranty
emails, contracts, project notes, and receipts. Search finds dates, but it cannot
tell an invoice date from a cancellation deadline. Cloud document assistants can,
but sending an entire personal or company folder to one is often a non-starter.

Paperclock sits in the useful middle: local, small, deterministic, and good at
answering one question—*what in this folder might cost me if I forget it?*

## Run it

You need Python 3.11+ and Node 22+.

```bash
./run.sh
```

Open [http://localhost:3000](http://localhost:3000), drop a folder, or click the
10-second demo. Folder scans have no file-count ceiling: files are indexed first,
then read in bounded batches so memory stays predictable. The first run creates
`.venv/` and installs the one Python parsing dependency plus the UI packages, all
inside this project.

Prefer a terminal?

```bash
.venv/bin/paperclock scan ~/Documents/Policies
.venv/bin/paperclock scan ~/Documents/Policies --calendar deadlines.ics --json
```

## What it reads

- PDF and DOCX
- email (`.eml`) and calendar (`.ics`)
- Markdown, plain text, CSV, JSON, YAML, TOML, HTML, XML, RTF, and logs

Individual files are capped at 8 MB as a safety guard, but the folder itself can
contain any number of files. Unsupported, oversized, and unreadable files are
skipped with a visible reason.

## How it works

The browser sends a lightweight manifest in chunks. Python checks its local SQLite
index, immediately restores unchanged results, and reads only new or modified files
through a bounded worker pool. Results stream into the timeline batch by batch with
real counts, speed, estimated time, current file, and cache hits. A scan can be
paused and resumed without throwing away completed work.

The extraction pipeline recognizes explicit and relative dates, scores nearby
language for action words, down-ranks historical metadata, explains each decision,
and removes duplicates. Numeric dates are never guessed silently: choose day-first
or month-first in the interface, and ambiguous results stay marked.

There is no trained model and no telemetry. [`pypdf`](https://pypi.org/project/pypdf/)
is the only Python dependency; PDF parsing is the one piece not worth reinventing.

```text
browser folder picker
        │
        ├── chunked manifest ── local SQLite index ── unchanged results
        │
        └── bounded file batches ── Python reader pool
                                      │
                                      └── live timeline / .ics export
```

## Project map

```text
paperclock/          Python engine, readers, scan index, server, CLI, calendar export
app/                 small React interface
tests/               Python behavior tests + production bundle smoke test
public/              static brand assets
run.sh               one-command local launcher
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
npm install

# terminal 1: Python engine
.venv/bin/paperclock serve

# terminal 2: interface
npm run dev

# everything that should pass before a PR
npm test
npm run lint
```

Adding a date form usually means one focused change in
[`paperclock/extractor.py`](paperclock/extractor.py) and one test. Readers are
isolated in [`paperclock/readers.py`](paperclock/readers.py). See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the short contribution guide.

## Honest limitations

- Scanned or image-only PDFs need OCR first.
- Relative phrases use the file modification time as their anchor; copied files
  can therefore deserve a manual check.
- Browsers cannot reopen a folder after a full page refresh without permission.
  Choose the same folder again and Paperclock will reuse its indexed work.
- Paperclock is a radar, not legal or financial advice. Confirm the source before
  acting on a date.

## License

MIT. See [`LICENSE`](LICENSE).
