<p align="center">
  <img src="public/og.png" alt="Paperclock — Your files know what's coming" width="900">
</p>

# Paperclock ⏳

> **A private, on-device deadline radar for the commitments hiding in your files.**  
> Drop in a folder, and Paperclock extracts renewals, cancellation windows, expiries, tax filings, payments, appointments, and warranties—then turns them into an interactive timeline and a standard `.ics` calendar.

---

> [!NOTE]
> **Project Status: Early Development**  
> Paperclock is in active, early-stage development. The engine and extraction heuristics are being continuously improved and expanded with new format readers, domain recognizers, and platform features. Feedback and contributions are welcome!

---

## 🧭 What Paperclock Is (and What It Is Not)

### ✅ What It Is
- **100% Local & Zero-Knowledge**: Runs entirely on your local machine. No accounts, no cloud APIs, and zero document uploads.
- **Deterministic & Explainable**: Every detected deadline links to the exact source file, line/page number, and the clause context that triggered it.
- **Cross-Platform On-Device OCR**: Reads digital documents as well as scanned image-only PDFs and photos/receipts (`.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff`) using lightweight local ONNX models.
- **Smart Filtering**: Applies a 4-stage deterministic gating pipeline (path pruning, document allowlisting, multi-term domain corroboration, and temporal clause validation) to filter out historical dates and example text.
- **Bank Statement Reconciliation**: Automatically parses transaction statements (`.csv`, `.xlsx`, tabular `.pdf`), balances income vs expenses, detects recurring debits, and verifies running arithmetic.
- **Calendar Ready**: Exports clean `.ics` calendar files with configurable 7-day and 1-day reminder alarms.

### ❌ What It Is Not
- **Not a Cloud Service**: Your files never leave your device.
- **Not a Generative LLM**: It does not summarize prose or hallucinate imaginary dates.
- **Not a Generic Desktop Search Tool**: It specifically answers one question: *“What in this folder might cost me money, legal standing, or coverage if I forget it?”*
- **Not Legal or Financial Advice**: Paperclock is a radar to flag deadlines; always review the original document before taking action.

---

## 📂 Supported Formats

- **PDF Documents**: Native text PDFs as well as scanned image-only PDFs (with automatic 150 DPI OCR fallback).
- **Images & Scans**: `.png`, `.jpg`, `.jpeg`, `.webp`, `.tiff` (processed via on-device RapidOCR).
- **Word & Office**: `.docx` and Apple `.pages` documents with preview data.
- **Email & Messages**: `.eml` files and Outlook `.msg` containers.
- **Spreadsheets & Data**: `.csv`, `.xlsx`, and tabular bank statements.

*Files up to 8 MB per document are supported with bounded memory streaming.*

---

## 🚀 Quickstart

### Prerequisites
- **Python**: 3.11 or newer
- **Node.js**: 22.13 or newer

### 1. Run Everything (Web UI + Engine)

```bash
./run.sh
```

Open [http://localhost:3000](http://localhost:3000) in your browser. Drop any folder or run the built-in 10-second demo.

### 2. Command-Line Interface (CLI)

Prefer running from the terminal?

```bash
# Scan a folder of policies or contracts
.venv/bin/paperclock scan ~/Documents/Policies

# Scan and export directly to a calendar file
.venv/bin/paperclock scan ~/Documents/Policies --calendar deadlines.ics

# Output structured JSON
.venv/bin/paperclock scan ~/Documents/Policies --json
```

---

## ⚙️ Architecture

```text
Local Folder / Drag & Drop
        │
        ├── Chunked Browser Manifest ── Local SQLite Index (Cache Hits)
        │
        └── Bounded File Batches ── Multi-Threaded Python Engine
                                      ├── Precision-Gated Timeline & .ics
                                      ├── RapidOCR Engine (Scans / Images)
                                      └── Statement Reconciliation Engine
```

- **Frontend**: React + TypeScript client with a Web Worker for non-blocking file hashing and preparation.
- **Backend Engine**: Python HTTP loopback service running bounded worker threads (`paperclock/server.py`).
- **Storage**: Private local SQLite database (`~/.paperclock/index.sqlite3`) with WAL mode for instant resumable scans.

---

## 🔒 Security & Privacy

Paperclock is designed with a defense-in-depth local security model:

- **Loopback Enforcement**: The engine binds exclusively to `127.0.0.1` and strictly validates `Host` and `Origin` headers.
- **Anti-Exploitation**: XML entities are disabled (`defusedxml`), and compressed archives enforce strict decompressed byte limits (`32 MB` max, `200:1` max ratio) to prevent zip bombs.
- **Data Isolation**: Local database files are restricted to the active operating system user (`0o600` / `0o700` permissions).
- **Safe Calendar Outputs**: Event descriptions and summaries are escaped to prevent CRLF and iCalendar injection.

See [SECURITY.md](SECURITY.md) for our security policy.

---

## 🛠️ Development & Testing

```bash
# Set up Python virtual environment
python3 -m venv .venv
.venv/bin/pip install -e .

# Install frontend dependencies
npm install

# Run the test suite (Python + Frontend)
npm test

# Run linter
npm run lint
```

---

## 🗺️ Roadmap

- [x] Cross-platform lightweight OCR for images and scanned PDFs
- [x] Bank statement debit/credit parsing and reconciliation
- [x] Date ambiguity picker ($DD/MM$ vs $MM/DD$)
- [ ] Standalone native desktop app packaging (macOS `.dmg` & Windows `.msi`)
- [ ] Background folder watcher ("Radar Daemon" for `~/Downloads` and `~/Documents`)
- [ ] Direct Apple Reminders / Google Calendar 1-click sync
- [ ] User custom rules & deadline dismissal management

---

## 📄 License

Paperclock is licensed under the [MIT License](LICENSE).
