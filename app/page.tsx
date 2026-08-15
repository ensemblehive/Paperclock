"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

const ENGINE = "http://127.0.0.1:4312";
const TEXT_TYPES = new Set(["eml", "csv"]);
const BINARY_TYPES = new Set(["pdf", "docx", "pages", "msg", "xlsx", "png", "jpg", "jpeg", "webp", "tiff"]);
const IGNORED_DIRECTORIES = new Set([
  ".git", ".svn", ".hg", "venv", ".venv", "env", "site-packages", "__pycache__",
  "node_modules", "bower_components", "build", "dist", "target", "out", "bin", "obj",
  "library", "appdata", "tmp", ".cache",
]);
const DISCOVERY_CONCURRENCY = 8;
const ENUMERATION_CHUNK = 250;
const EXTRACTION_REVISION = "8";

type BankSummaryData = {
  id: string;
  source: string;
  title: string;
  statement_start: string;
  statement_end: string;
  opening_balance: number | null;
  closing_balance: number | null;
  balance_change: number | null;
  total_income: number;
  total_expense: number;
  net_cashflow: number;
  transaction_count: number;
  credit_count: number;
  debit_count: number;
  average_expense: number;
  currency: string;
  categories: Array<{
    category: string;
    amount: number;
    percentage: number;
    count: number;
  }>;
  top_expenses: Array<{
    date: string;
    description: string;
    amount: number;
    category: string;
  }>;
  largest_credit: {
    date: string;
    description: string;
    amount: number;
  } | null;
  recurring_payments: Array<{
    description: string;
    amount: number;
    count: number;
  }>;
  verification: "verified" | "discrepancy" | "unverifiable";
  rows_rejected: number;
};

type Commitment = {
  id: string;
  date: string;
  title: string;
  category: string;
  source: string;
  line: number;
  snippet: string;
  confidence: number;
  reason: string;
  original: string;
  ambiguous: boolean;
  page?: number | null;
  document_id?: string | null;
  domain?: string | null;
  entity?: string | null;
  periodicity?: string | null;
  summary?: string | null;
  notice_days?: number | null;
  action_date?: string | null;
};

type ScanResult = {
  scan_id: string;
  status: "running" | "cancelled" | "complete";
  today: string;
  total: number;
  files_scanned: number;
  files_processed: number;
  files_cached: number;
  files_skipped: number;
  files_pending: number;
  dates_reviewed: number;
  noise_removed: number;
  commitments: Commitment[];
  bank_statements: BankSummaryData[];
  warnings: string[];
  rate: number;
  eta_seconds: number | null;
  resumed?: boolean;
};

type UploadFile = {
  path: string;
  content: string;
  encoding: "text" | "base64";
  modified: string;
  size?: number;
};

type ScanEntry = {
  path: string;
  size: number;
  modified: string;
  fingerprint: string;
  file?: File;
  prepared?: UploadFile;
};

type IncomingFile = {
  file: File;
  relativePath: string;
};

type FileSystemHandleLike = {
  kind: "file" | "directory";
  name: string;
  getFile?: () => Promise<File>;
  values?: () => AsyncIterable<FileSystemHandleLike>;
};

type LegacyEntry = {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
  file?: (success: (file: File) => void, failure: (error: DOMException) => void) => void;
  createReader?: () => { readEntries: (success: (entries: LegacyEntry[]) => void, failure: (error: DOMException) => void) => void };
};

type DroppedSource = {
  handlePromise?: Promise<FileSystemHandleLike | null>;
  entry?: LegacyEntry | null;
  fallbackFile?: File | null;
};

type DroppedSelection = {
  sources: DroppedSource[];
  fallbackFiles: File[];
};

type DiscoveryProgress = {
  discovered: number;
  supported: number;
  currentPath: string;
};

type DiscoveryResult = {
  files: IncomingFile[];
  discovered: number;
};

type ScanActivity = {
  active: boolean;
  phase: "preparing" | "indexing" | "reading" | "finishing" | "cancelled" | "complete";
  current: string;
  detail?: string;
  filePercent?: number | null;
  overallPercent?: number | null;
};

const categoryNames: Record<string, string> = {
  expiry: "Expiry",
  renewal: "Renewal",
  cancellation: "Cancel Window",
  money: "Money Due",
  submission: "Submission",
  appointment: "Appointment",
  delivery: "Delivery",
  warranty: "Coverage Ends",
  taxes: "Taxes & IRS",
  housing: "Housing & Lease",
  insurance: "Insurance Policy",
  subscription: "Subscription",
  utilities: "Utilities & Bills",
  banking: "Banking & Loans",
  legal: "Legal & NDAs",
  vehicle: "Vehicle Milestone",
  travel: "Travel & Identity",
  employment: "Employment & HR",
  billing: "Invoices & Billing",
  action: "Action Due",
};

function isoOffset(days: number) {
  const value = new Date();
  value.setHours(12, 0, 0, 0);
  value.setDate(value.getDate() + days);
  return value.toISOString().slice(0, 10);
}

const demoFiles = (): UploadFile[] => [
  {
    path: "Household/energy-plan.eml",
    content: `Subject: Energy plan renewal\nYour fixed-rate energy plan renews automatically on ${isoOffset(38)}. Cancel before ${isoOffset(10)} to avoid the new variable rate.\nStatement date: ${isoOffset(-12)}.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 190,
  },
  {
    path: "Work/vendor-agreement.eml",
    content: `Subject: Atlas vendor agreement\nThe contract security review must be submitted by ${isoOffset(21)}. The agreement terminates on ${isoOffset(67)}. Cancellation requires 30 days notice prior to termination.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 260,
  },
  {
    path: "Receipts/camera-warranty.eml",
    content: `Subject: Sony Camera warranty\nPurchase date: ${isoOffset(-280)}\nThe warranty coverage is valid until ${isoOffset(45)}. File any repair claim before that date.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 160,
  },
  {
    path: "Taxes/quarterly-estimate.eml",
    content: `Subject: IRS Estimated Tax Notice\nYour Q3 federal estimated tax payment is due by ${isoOffset(18)}.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 170,
  },
  {
    path: "Auto/car-insurance.eml",
    content: `Subject: Geico Auto Policy Renewal\nYour annual vehicle insurance premium of $185.00/mo (Policy #POL-99210) renews on ${isoOffset(28)}. Cancellation requires 14 days notice.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 195,
  },
];

export default function Home() {
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastEntriesRef = useRef<ScanEntry[]>([]);
  const pickerRequestRef = useRef(0);
  const [engineOnline, setEngineOnline] = useState<boolean | null>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activity, setActivity] = useState<ScanActivity | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [dateOrder, setDateOrder] = useState("day-first");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch(`${ENGINE}/api/health`)
      .then((response) => setEngineOnline(response.ok))
      .catch(() => setEngineOnline(false));
  }, []);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    const handleCancel = () => {
      pickerRequestRef.current = 0;
      setLoading(false);
      setActivity(result ? { active: false, phase: "complete", current: "Your horizon is ready" } : null);
    };
    input.addEventListener("cancel", handleCancel);
    return () => input.removeEventListener("cancel", handleCancel);
  }, [result]);

  const visible = useMemo(() => {
    if (!result) return [];
    const today = new Date(`${result.today}T12:00:00`);
    return result.commitments.filter((item) => {
      if (dismissed.has(item.id)) return false;
      const days = dayDifference(today, new Date(`${item.date}T12:00:00`));
      if (filter === "soon") return days >= 0 && days <= 30;
      if (filter === "expiry") return ["expiry", "renewal", "warranty", "insurance", "subscription"].includes(item.category);
      if (filter === "money") return ["money", "billing", "taxes", "utilities"].includes(item.category);
      if (filter === "taxes") return item.category === "taxes" || item.domain === "taxes";
      if (filter === "banking") return item.category === "banking" || item.domain === "banking";
      if (filter === "employment") return item.category === "employment" || item.domain === "employment";
      if (filter === "insurance") return item.category === "insurance" || item.domain === "insurance";
      if (filter === "housing") return item.category === "housing" || item.domain === "housing";
      if (filter === "subscription") return item.category === "subscription" || item.domain === "subscription";
      if (filter === "utilities") return item.category === "utilities" || item.domain === "utilities";
      if (filter === "legal") return item.category === "legal" || item.domain === "legal";
      if (filter === "warranty") return item.category === "warranty" || item.domain === "warranty";
      if (filter === "vehicle") return item.category === "vehicle" || item.domain === "vehicle";
      if (filter === "travel") return item.category === "travel" || item.domain === "travel";
      return true;
    });
  }, [dismissed, filter, result]);

  async function runScan(entries: ScanEntry[]) {
    if (!entries.length) {
      setError("No supported files found. Try PDF, DOCX, Pages, EML, or Outlook MSG.");
      return;
    }
    lastEntriesRef.current = entries;
    setLoading(true);
    setError("");
    setDismissed(new Set());
    setSelected(new Set());
    setActivity({
      active: true,
      phase: "indexing",
      current: `Indexing ${entries.length.toLocaleString()} supported ${entries.length === 1 ? "file" : "files"}`,
      detail: "Checking what has changed before reading file contents",
      filePercent: null,
      overallPercent: 30,
    });
    await waitForFirstPaint();
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      const scanKey = await makeScanKey(entries, (processed, total) => {
        setActivity({
          active: true,
          phase: "indexing",
          current: "Fingerprinting file names and timestamps",
          detail: `${processed.toLocaleString()} of ${total.toLocaleString()} indexed without opening contents`,
          filePercent: total ? Math.round((processed / total) * 100) : 100,
          overallPercent: total ? 30 + Math.round((processed / total) * 8) : 38,
        });
      });
      const startResponse = await fetch(`${ENGINE}/api/scans/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scan_key: scanKey, total: entries.length, date_order: dateOrder }),
        signal: controller.signal,
      });
      if (!startResponse.ok) throw new Error("The local engine could not start that scan.");
      let snapshot = await startResponse.json() as ScanResult;
      setResult(snapshot);
      setEngineOnline(true);

      const needed = new Set<string>();
      for (const manifest of chunk(entries, 400)) {
        const response = await fetch(`${ENGINE}/api/scans/${snapshot.scan_id}/manifest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ files: manifest.map(({ path, size, modified, fingerprint }) => ({ path, size, modified, fingerprint })) }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("The local engine could not index those files.");
        const payload = await response.json() as { needed: string[]; progress: ScanResult };
        payload.needed.forEach((fingerprint) => needed.add(fingerprint));
        snapshot = payload.progress;
        setResult(snapshot);
      }

      const pending = entries.filter((entry) => needed.has(entry.fingerprint));
      const batches = makeUploadBatches(pending);
      for (let index = 0; index < batches.length; index += 1) {
        const batch = batches[index];
        setActivity({
          active: true,
          phase: "preparing",
          current: batch[0].path,
          detail: "Preparing this batch locally",
          filePercent: 0,
        });
        await waitForFirstPaint();
        const body = await prepareBatchInWorker(batch, dateOrder, controller.signal, (progress) => {
          const withinFile = progress.total ? progress.loaded / progress.total : 1;
          const batchPercent = Math.round(((progress.index + withinFile) / progress.count) * 100);
          setActivity({
            active: true,
            phase: "preparing",
            current: progress.path,
            detail: `Preparing file ${progress.index + 1} of ${progress.count} · ${formatBytes(progress.loaded)} of ${formatBytes(progress.total)}`,
            filePercent: batchPercent,
          });
        });
        setActivity({
          active: true,
          phase: "reading",
          current: batch.length === 1 ? batch[0].path : `${batch[0].path} + ${batch.length - 1} more`,
          detail: batch.some((entry) => entry.path.toLowerCase().endsWith(".pdf"))
            ? "Python is extracting pages and checking date context"
            : "Python is checking date context and removing noise",
          filePercent: null,
        });
        await waitForFirstPaint();
        const response = await fetch(`${ENGINE}/api/scans/${snapshot.scan_id}/batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body,
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("A file batch could not be read.");
        snapshot = await response.json();
        setResult(snapshot);
      }

      setActivity({ active: true, phase: "finishing", current: "Removing duplicates and ordering the horizon", filePercent: null });
      const finishResponse = await fetch(`${ENGINE}/api/scans/${snapshot.scan_id}/complete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal: controller.signal,
      });
      if (!finishResponse.ok) throw new Error("The scan could not be finalized.");
      snapshot = await finishResponse.json();
      setResult(snapshot);
      setActivity({ active: false, phase: "complete", current: "Your horizon is ready" });
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === "AbortError") {
        setActivity((current) => current ? { ...current, active: false, phase: "cancelled", current: "Scan paused safely" } : null);
        return;
      }
      if (caught instanceof TypeError) setEngineOnline(false);
      setError(caught instanceof Error ? caught.message : "Paperclock’s local engine isn’t running. Start it with ./run.sh, then try again.");
      setActivity(null);
    } finally {
      setLoading(false);
      abortRef.current = null;
    }
  }

  async function handleFiles(
    incomingFiles: IncomingFile[],
    feedbackAlreadyVisible = false,
    discoveredCount = incomingFiles.length,
  ) {
    setLoading(true);
    setError("");
    if (!feedbackAlreadyVisible) {
      setActivity({
        active: true,
        phase: "preparing",
        current: "Opening the folder safely",
        detail: "Paperclock has received your selection",
        filePercent: 0,
        overallPercent: 12,
      });
    }
    try {
      await waitForFirstPaint();
      const entries = await prepareEntries(incomingFiles, (processed, total) => {
        setActivity({
          active: true,
          phase: "preparing",
          current: "Checking supported file types",
          detail: `${processed.toLocaleString()} of ${total.toLocaleString()} names checked`,
          filePercent: total ? Math.round((processed / total) * 100) : 100,
          overallPercent: total ? 15 + Math.round((processed / total) * 15) : 30,
        });
      });
      if (!entries.length) {
        setLoading(false);
        setActivity(null);
        setError(
          discoveredCount
            ? `Paperclock found ${discoveredCount.toLocaleString()} ${discoveredCount === 1 ? "file" : "files"}, but none were PDF, DOCX, Pages, EML, or Outlook MSG.`
            : "Paperclock could not open that dropped folder. Use “choose a folder” to select it through the folder picker.",
        );
        return;
      }
      await runScan(entries);
    } catch (caught) {
      setLoading(false);
      setActivity(null);
      setError(caught instanceof Error ? caught.message : "Those files could not be prepared.");
    }
  }

  async function chooseFolder() {
    const input = inputRef.current;
    if (!input || loading) return;
    pickerRequestRef.current += 1;
    flushSync(() => {
      setLoading(true);
      setError("");
      setActivity({
        active: true,
        phase: "preparing",
        current: "Ready for your folder",
        detail: "Choose it in the system window · processing starts automatically",
        filePercent: null,
        overallPercent: null,
      });
    });
    const directoryPicker = (window as typeof window & {
      showDirectoryPicker?: (options?: { mode: "read" }) => Promise<FileSystemHandleLike>;
    }).showDirectoryPicker;
    if (directoryPicker) {
      try {
        const handle = await directoryPicker.call(window, { mode: "read" });
        pickerRequestRef.current = 0;
        setActivity({
          active: true,
          phase: "preparing",
          current: "Discovering files in the selected folder",
          detail: "0 files found so far",
          filePercent: null,
          overallPercent: null,
        });
        await processDirectorySelection({ sources: [{ handlePromise: Promise.resolve(handle) }], fallbackFiles: [] });
      } catch (caught) {
        if (caught instanceof DOMException && caught.name === "AbortError") {
          setLoading(false);
          setActivity(result ? { active: false, phase: "complete", current: "Your horizon is ready" } : null);
          return;
        }
        setLoading(false);
        setActivity(null);
        setError("That folder could not be opened. Please try again or drag it onto Paperclock.");
      }
      return;
    }
    input.click();
  }

  function onDragEnter(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (dragging) return;
    setDragging(true);
    setLoading(true);
    setError("");
    setActivity({
      active: true,
      phase: "preparing",
      current: "Release to start",
      detail: "Paperclock is ready to inspect this folder locally",
      filePercent: null,
      overallPercent: null,
    });
  }

  function onDragLeave(event: DragEvent<HTMLDivElement>) {
    if (event.relatedTarget instanceof Node && event.currentTarget.contains(event.relatedTarget)) return;
    setDragging(false);
    setLoading(false);
    setActivity(null);
  }

  async function processDirectorySelection(selection: DroppedSelection) {
    try {
      await waitForFirstPaint();
      const discovery = await collectDroppedFiles(selection, ({ discovered, supported, currentPath }) => {
        setActivity({
          active: true,
          phase: "preparing",
          current: "Discovering files in the selected folder",
          detail: `${discovered.toLocaleString()} found · ${supported.toLocaleString()} supported${currentPath ? ` · ${currentPath}` : ""}`,
          filePercent: null,
          overallPercent: null,
        });
      });
      await handleFiles(discovery.files, true, discovery.discovered);
    } catch {
      setLoading(false);
      setActivity(null);
      setError("That folder could not be read. Please try again or choose a different folder.");
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const selection = captureDroppedSelection(event.dataTransfer);
    setLoading(true);
    setError("");
    setActivity({
      active: true,
      phase: "preparing",
      current: "Discovering files in the dropped folder",
      detail: "0 files found so far",
      filePercent: null,
      overallPercent: null,
    });
    void processDirectorySelection(selection);
  }

  function onInput(event: ChangeEvent<HTMLInputElement>) {
    pickerRequestRef.current = 0;
    const input = event.currentTarget;
    const fileList = input.files;
    if (!fileList) return;
    setLoading(true);
    setError("");
    setActivity({
      active: true,
      phase: "preparing",
      current: "Folder received",
      detail: "Starting local discovery now",
      filePercent: null,
      overallPercent: 3,
    });
    void (async () => {
      try {
        await waitForFirstPaint();
        const total = fileList.length;
        const incoming = await snapshotInputFiles(fileList, (processed) => {
          setActivity({
            active: true,
            phase: "preparing",
            current: "Receiving file names from the browser",
            detail: `${processed.toLocaleString()} of ${total.toLocaleString()} received`,
            filePercent: total ? Math.round((processed / total) * 100) : 100,
            overallPercent: total ? 5 + Math.round((processed / total) * 10) : 15,
          });
        });
        input.value = "";
        await handleFiles(incoming, true, total);
      } catch (caught) {
        input.value = "";
        setLoading(false);
        setActivity(null);
        setError(caught instanceof Error ? caught.message : "That folder could not be opened.");
      }
    })();
  }

  async function cancelScan() {
    const scanId = result?.scan_id;
    abortRef.current?.abort();
    if (!scanId) return;
    try {
      const response = await fetch(`${ENGINE}/api/scans/${scanId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (response.ok) setResult(await response.json());
    } catch {
      // Intentionally empty
    }
  }

  async function downloadCalendar(commitments: Commitment[], filename = "paperclock-selection.ics") {
    if (!commitments.length) {
      setError("Select at least one milestone to add to your calendar.");
      return;
    }
    setError("");
    try {
      const response = await fetch(`${ENGINE}/api/calendar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ commitments }),
      });
      if (!response.ok) throw new Error();
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1_000);
    } catch {
      setError("The calendar could not be created. Is the local engine still running?");
    }
  }

  function downloadCSV(commitments: Commitment[], filename = "paperclock-commitments.csv") {
    if (!commitments.length) {
      setError("No commitments available to export.");
      return;
    }
    setError("");
    const headers = ["Date", "Category", "Title", "Periodicity", "Notice Days", "Action Date", "Summary", "Source", "Confidence", "Reason"];
    const rows = commitments.map((item) => [
      `"${item.date}"`,
      `"${(item.category || "").replace(/"/g, '""')}"`,
      `"${(item.title || "").replace(/"/g, '""')}"`,
      `"${item.periodicity || ""}"`,
      item.notice_days !== undefined && item.notice_days !== null ? item.notice_days : "",
      `"${item.action_date || ""}"`,
      `"${(item.summary || item.snippet || "").replace(/"/g, '""')}"`,
      `"${(item.source || "").replace(/"/g, '""')}"`,
      item.confidence,
      `"${(item.reason || "").replace(/"/g, '""')}"`,
    ]);
    const csvContent = [headers.join(","), ...rows.map((row) => row.join(","))].join("\r\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function openSource(item: { source: string; page?: number | null }) {
    setError("");
    const entry = lastEntriesRef.current.find((candidate) => candidate.path === item.source);
    if (!entry?.file) {
      setError("That source is not available in this browser session. Scan the folder again to reopen it.");
      return;
    }
    const url = URL.createObjectURL(entry.file);
    const target = item.page && item.source.toLowerCase().endsWith(".pdf") ? `${url}#page=${item.page}` : url;
    const anchor = document.createElement("a");
    anchor.href = target;
    anchor.target = "_blank";
    anchor.rel = "noopener noreferrer";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 300_000);
  }

  const preflightPercent = activity?.overallPercent;

  return (
    <main className={result ? "app app--results" : "app"}>
      <header className="topbar">
        <button className="wordmark" onClick={() => setResult(null)} aria-label="Paperclock home">
          <span className="mark" aria-hidden="true"><i /><i /></span>
          <span>paperclock</span>
        </button>
        <div className="topbar__right">
          <span className={`engine ${engineOnline ? "engine--online" : ""}`}>
            <span className="engine__dot" />
            {engineOnline === null ? "Finding local engine" : engineOnline ? "Local engine ready" : "Engine offline"}
          </span>
          <span className="ensemble-lockup">
            <i aria-hidden="true" />
            <span>Product of <strong>Ensemble Hive</strong></span>
          </span>
        </div>
      </header>

      {!result ? (
        <section className="landing">
          <div className="eyebrow"><span>Private by design</span><span>Nothing leaves your computer</span></div>
          <h1>Your files know<br /><em>what’s coming.</em></h1>
          <p className="lede">
            Paperclock finds the renewals, cancellation windows, expiries, payments, and deadlines
            hiding across a folder—then gives you one calm timeline.
          </p>
          <div
            className={`dropzone ${dragging ? "dropzone--active" : ""} ${loading ? "dropzone--loading" : ""}`}
            aria-busy={loading}
            onDragEnter={onDragEnter}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
          >
            <input
              ref={inputRef}
              type="file"
              multiple
              // @ts-expect-error webkitdirectory is supported by Chromium and Safari.
              webkitdirectory=""
              onChange={onInput}
            />
            <div className="dropzone__icon" aria-hidden="true"><span>↓</span></div>
            <div>
              <strong>{dragging ? "Release to scan this folder" : loading ? "Mapping the paper trail…" : "Drop a folder here"}</strong>
              <span>{dragging ? "Paperclock is ready" : <>or <button onClick={chooseFolder}>choose a folder</button> · any number of files</>}</span>
            </div>
            <div className="format-row"><span>PDF</span><span>DOCX</span><span>PAGES</span><span>EML</span><span>MSG</span></div>
          </div>
          {loading && !result && activity && (
            <div className="preflight" role="status" aria-live="polite">
              <span className="preflight__pulse" aria-hidden="true"><i /><i /><i /></span>
              <div>
                <strong>{activity.current}</strong>
                <small>{activity.detail}</small>
              </div>
              <b>{preflightPercent === null || preflightPercent === undefined ? "…" : `${preflightPercent}%`}</b>
              <div className={`preflight__track ${preflightPercent === null || preflightPercent === undefined ? "preflight__track--indeterminate" : ""}`}>
                <i style={preflightPercent === null || preflightPercent === undefined ? undefined : { width: `${preflightPercent}%` }} />
              </div>
            </div>
          )}
          <div className="landing__footer">
            <button className="demo-button" disabled={loading} onClick={() => void runScan(prepareDemoEntries(demoFiles()))}>
              <span className="play">▶</span> Try the 10-second demo
            </button>
            <div className="date-order" aria-label="Numeric date order">
              <span>04/07 means</span>
              <button className={dateOrder === "day-first" ? "active" : ""} onClick={() => setDateOrder("day-first")}>4 July</button>
              <button className={dateOrder === "month-first" ? "active" : ""} onClick={() => setDateOrder("month-first")}>April 7</button>
            </div>
          </div>
          {error && <div className="notice" role="alert">{error}</div>}
          <div className="quiet-proof">
            <span>NO ACCOUNT</span><i /><span>NO CLOUD</span><i /><span>EXPLAINABLE RESULTS</span>
          </div>
        </section>
      ) : (
        <Results
          result={result}
          visible={visible}
          filter={filter}
          setFilter={setFilter}
          dismiss={(id) => {
            setDismissed((current) => new Set(current).add(id));
            setSelected((current) => { const next = new Set(current); next.delete(id); return next; });
          }}
          selected={selected}
          toggleSelected={(id) => setSelected((current) => {
            const next = new Set(current);
            if (next.has(id)) next.delete(id); else next.add(id);
            return next;
          })}
          selectVisible={() => setSelected(new Set(visible.map((item) => item.id)))}
          clearSelected={() => setSelected(new Set())}
          rescan={chooseFolder}
          downloadSelected={() => void downloadCalendar(result.commitments.filter((item) => selected.has(item.id)))}
          downloadOne={(item) => void downloadCalendar([item], `${calendarFilename(item.title)}.ics`)}
          downloadCSV={() => downloadCSV(result.commitments)}
          openSource={openSource}
          loading={loading}
          error={error}
          activity={activity}
          cancelScan={() => void cancelScan()}
          resumeScan={() => void runScan(lastEntriesRef.current)}
        />
      )}
      {result && (
        <input
          ref={inputRef}
          className="hidden-input"
          type="file"
          multiple
          // @ts-expect-error webkitdirectory is supported by Chromium and Safari.
          webkitdirectory=""
          onChange={onInput}
        />
      )}
    </main>
  );
}

function Results({
  result, visible, filter, setFilter, dismiss, selected, toggleSelected, selectVisible,
  clearSelected, rescan, downloadSelected, downloadOne, downloadCSV, openSource, loading, error,
  activity, cancelScan, resumeScan,
}: {
  result: ScanResult;
  visible: Commitment[];
  filter: string;
  setFilter: (value: string) => void;
  dismiss: (id: string) => void;
  selected: Set<string>;
  toggleSelected: (id: string) => void;
  selectVisible: () => void;
  clearSelected: () => void;
  rescan: () => void;
  downloadSelected: () => void;
  downloadOne: (item: Commitment) => void;
  downloadCSV: () => void;
  openSource: (item: { source: string; page?: number | null }) => void;
  loading: boolean;
  error: string;
  activity: ScanActivity | null;
  cancelScan: () => void;
  resumeScan: () => void;
}) {
  const today = useMemo(() => new Date(`${result.today}T12:00:00`), [result.today]);
  const nearest = result.commitments.find((item) => new Date(`${item.date}T12:00:00`) >= today);
  const groups = groupDocuments(visible, today);

  const showBankStatements = filter === "all" || filter === "banking";
  const sourceNames = new Set(result.commitments.map((item) => item.source));
  result.bank_statements.forEach((statement) => sourceNames.add(statement.source));
  const documentTotal = sourceNames.size;

  return (
    <section className="results-shell">
      <aside className="summary">
        <div>
          <p className="section-kicker">Folder pulse</p>
          <h2>{documentTotal}<span> documents<br />need attention</span></h2>
          <div className="summary__stats">
            <div><strong>{result.commitments.length}</strong><span>milestones found</span></div>
            <div><strong>{result.noise_removed}</strong><span>dates ignored</span></div>
          </div>
          <div className="nearest">
            <span>Next on the clock</span>
            <strong>{nearest ? relativeLabel(nearest.date, today) : "Nothing upcoming"}</strong>
            <p>{nearest?.title ?? "Your timeline is clear."}</p>
          </div>
        </div>
        <div className="summary__actions">
          <div className="selection-tools">
            <button onClick={selectVisible}>Select visible</button>
            <span>{selected.size} selected</span>
            {selected.size > 0 && <button onClick={clearSelected}>Clear</button>}
          </div>
          <button className="primary" disabled={selected.size === 0} onClick={downloadSelected}>
            Add {selected.size || "selected"} to calendar <span>↓</span>
          </button>
          <button className="secondary" onClick={downloadCSV} title="Export commitments as CSV">
            Export CSV / Spreadsheet <span>↓</span>
          </button>
          <button className="secondary" disabled={loading} onClick={rescan}>{loading ? "Scanning…" : "Scan another folder"}</button>
          <p>Deterministic local processing. File contents never leave your machine.</p>
        </div>
      </aside>

      <div className="timeline-panel">
        {activity && activity.phase !== "complete" && (
          <ScanProgress result={result} activity={activity} cancelScan={cancelScan} resumeScan={resumeScan} />
        )}

        <div className="timeline-head">
          <div className="timeline-title-wrap">
            <p className="section-kicker">Your horizon</p>
            <h1>{activity?.active ? "Reading your horizon" : "What needs attention"}</h1>
          </div>
          <div className="filters-container">
            <div className="filters" aria-label="Filter commitments">
              {[
                ["all", "All"],
                ["soon", "Next 30 days"],
                ["money", "Money & Invoices"],
                ["expiry", "Expiries & Renewals"],
                ["taxes", "Taxes & IRS"],
                ["banking", "Bank Statements"],
                ["employment", "Salary & Payroll"],
                ["insurance", "Insurance"],
                ["housing", "Housing & Lease"],
                ["subscription", "Subscriptions"],
                ["utilities", "Utilities"],
                ["legal", "Legal & NDAs"],
                ["warranty", "Warranties"],
                ["vehicle", "Vehicle"],
                ["travel", "Travel & Visa"],
              ].map(([value, label]) => (
                <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
              ))}
            </div>
          </div>
        </div>
        {error && <div className="notice notice--results" role="alert">{error}</div>}
        {result.warnings.length > 0 && (
          <details className="warnings"><summary>{result.files_skipped} files skipped</summary>{result.warnings.map((warning) => <p key={warning}>{warning}</p>)}</details>
        )}
        {showBankStatements && result.bank_statements.length > 0 && (
          <section className="statement-grid" aria-label="Bank statement analyses">
            {result.bank_statements.map((statement) => (
              <BankStatementCard key={statement.id} data={statement} openSource={openSource} />
            ))}
          </section>
        )}
        {visible.length === 0 && (!showBankStatements || result.bank_statements.length === 0) && activity?.active ? (
          <div className="waiting-state">
            <div className="waiting-papers" aria-hidden="true"><i /><i /><i /></div>
            <h3>Dates will appear here as they’re found</h3>
            <p>Paperclock is reading in small batches, so the interface stays responsive.</p>
          </div>
        ) : visible.length === 0 && (!showBankStatements || result.bank_statements.length === 0) ? (
          <div className="empty-state"><span>○</span><h3>No commitments in this view</h3><p>Try another filter or scan a different folder.</p></div>
        ) : (
          <div className="groups">
            {groups.map((group) => (
              <div className="time-group" key={group.label}>
                <div className="group-label"><span>{group.label}</span><i /></div>
                {group.documents.map((document) => (
                  <DocumentCard
                    key={document.source}
                    document={document}
                    today={today}
                    selected={selected}
                    toggleSelected={toggleSelected}
                    dismiss={dismiss}
                    downloadOne={downloadOne}
                    openSource={openSource}
                  />
                ))}
              </div>
            ))}
          </div>
        )}
        <footer className="results-footer">
          <span>Paperclock reviewed {result.dates_reviewed} date-like phrases.</span>
          <span>Human judgment still wins—open the source before acting.</span>
        </footer>
      </div>
    </section>
  );
}

function ScanProgress({
  result, activity, cancelScan, resumeScan,
}: {
  result: ScanResult;
  activity: ScanActivity;
  cancelScan: () => void;
  resumeScan: () => void;
}) {
  const percent = result.total ? Math.min(100, Math.round((result.files_processed / result.total) * 100)) : 0;
  const phaseLabel = activity.phase === "preparing" ? "Preparing" : activity.phase === "indexing" ? "Indexing" : activity.phase === "finishing" ? "Finishing" : activity.phase === "cancelled" ? "Paused" : "Reading";
  const hasFileProgress = activity.phase === "preparing" && activity.filePercent !== null && activity.filePercent !== undefined;
  return (
    <section className={`scan-progress scan-progress--${activity.phase}`} role="status" aria-live="polite" aria-busy={activity.active}>
      <div className="scan-progress__clock" aria-hidden="true"><i /><i /><b /></div>
      <div className="scan-progress__body">
        <div className="scan-progress__top">
          <span>{phaseLabel}</span>
          <strong>{percent}%</strong>
        </div>
        <p>{activity.current}</p>
        <small className="scan-progress__detail">{activity.detail || "Local processing continues inside Paperclock"}</small>
        <div className={`file-progress ${hasFileProgress ? "" : "file-progress--idle"}`} aria-hidden={!hasFileProgress}>
          <i style={{ width: `${hasFileProgress ? activity.filePercent : 0}%` }} />
        </div>
        <div className="progress-track"><i style={{ width: `${percent}%` }} /></div>
        <div className="scan-progress__facts">
          <span><b>{result.files_processed.toLocaleString()}</b> of {result.total.toLocaleString()} files</span>
          <span><b>{result.commitments.length}</b> commitments found</span>
          {result.files_cached > 0 && <span><b>{result.files_cached}</b> unchanged</span>}
          {result.rate > 0 && activity.active && <span>{result.rate.toFixed(1)} files/sec</span>}
          {result.eta_seconds !== null && result.eta_seconds > 2 && activity.active && <span>about {formatEta(result.eta_seconds)} left</span>}
        </div>
      </div>
      {activity.active ? (
        <button className="scan-control" onClick={cancelScan}>Pause</button>
      ) : activity.phase === "cancelled" ? (
        <button className="scan-control scan-control--resume" onClick={resumeScan}>Resume</button>
      ) : null}
    </section>
  );
}

type DocumentBundle = {
  source: string;
  items: Commitment[];
  lead: Commitment;
};

function DocumentCard({
  document, today, selected, toggleSelected, dismiss, downloadOne, openSource,
}: {
  document: DocumentBundle;
  today: Date;
  selected: Set<string>;
  toggleSelected: (id: string) => void;
  dismiss: (id: string) => void;
  downloadOne: (item: Commitment) => void;
  openSource: (item: { source: string; page?: number | null }) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const leadDate = new Date(`${document.lead.date}T12:00:00`);
  const dateParts = new Intl.DateTimeFormat("en", { month: "short", day: "2-digit", year: "numeric" }).formatToParts(leadDate);
  const month = dateParts.find((part) => part.type === "month")?.value;
  const day = dateParts.find((part) => part.type === "day")?.value;

  return (
    <article className={`document-card document-card--${document.lead.category}`}>
      <div className="document-card__head">
        <div className="date-tile"><span>{month}</span><strong>{day}</strong><small>{leadDate.getFullYear()}</small></div>
        <button className="document-card__toggle" onClick={() => setExpanded((current) => !current)} aria-expanded={expanded}>
          <span className="document-card__eyebrow">Document · {document.items.length} {document.items.length === 1 ? "milestone" : "milestones"}</span>
          <strong>{documentLabel(document.source)}</strong>
          <small>{document.source}</small>
          <i aria-hidden="true">{expanded ? "−" : "+"}</i>
        </button>
      </div>
      {expanded && (
        <div className="milestone-list">
          {document.items.map((item) => {
            const itemDate = new Date(`${item.date}T12:00:00`);
            const days = dayDifference(today, itemDate);
            return (
              <section className={`milestone milestone--${item.category}`} key={item.id}>
                <label className="milestone__check">
                  <input
                    type="checkbox"
                    checked={selected.has(item.id)}
                    onChange={() => toggleSelected(item.id)}
                    aria-label={`Select ${item.title} for calendar export`}
                  />
                  <span aria-hidden="true" />
                </label>
                <time dateTime={item.date}>{shortDate(item.date)}</time>
                <div className="milestone__body">
                  <div className="commitment__meta">
                    <span className="category">{categoryNames[item.category] ?? "Action"}</span>
                    <span className={days < 0 ? "relative relative--late" : "relative"}>{relativeLabel(item.date, today)}</span>
                    {item.ambiguous && <span className="ambiguous">Check date order</span>}
                  </div>
                  <h3>{item.title}</h3>

                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", margin: "4px 0" }}>
                    {item.notice_days && item.action_date && (
                      <span className="fact-badge fact-badge--notice">
                        ⚠️ Requires {item.notice_days}d notice • Action by {shortDate(item.action_date)}
                      </span>
                    )}
                  </div>

                  {item.summary && (
                    <p className="summary-sentence">{item.summary}</p>
                  )}

                  <p className="source">
                    <span aria-hidden="true">⌁</span> {item.page ? `page ${item.page}` : `line ${item.line}`}
                  </p>
                  <div className="milestone__actions">
                    <button onClick={() => downloadOne(item)} aria-label={`Download ${item.title} calendar event`}>Add date <span>↓</span></button>
                    <button onClick={() => openSource(item)}>{item.page ? `Open page ${item.page}` : "Open source"} <span>↗</span></button>
                  </div>
                  <details>
                    <summary>Why Paperclock read this <span>+</span></summary>
                    <blockquote>“{item.snippet}”</blockquote>
                    <p>{item.reason}. Confidence {item.confidence}%.</p>
                  </details>
                </div>
                <button className="dismiss" onClick={() => dismiss(item.id)} title="Hide this milestone" aria-label={`Hide ${item.title}`}>×</button>
              </section>
            );
          })}
        </div>
      )}
    </article>
  );
}

function BankStatementCard({
  data, openSource,
}: {
  data: BankSummaryData;
  openSource: (item: { source: string }) => void;
}) {
  return (
    <article className="statement-card">
      <header className="statement-card__head">
        <div>
          <span className="document-card__eyebrow">Verified statement analysis</span>
          <h2>{documentLabel(data.source)}</h2>
          <p>{data.source}</p>
        </div>
        <button onClick={() => openSource(data)}>Open source <span>↗</span></button>
      </header>
      <BankPulse data={data} />
      <p className={`statement-card__verification statement-card__verification--${data.verification}`}>
        {data.verification === "verified"
          ? "Running balances reconcile"
          : data.verification === "discrepancy"
            ? "Balance discrepancy found—review before relying on totals"
            : "Totals parsed; running balances were not available to reconcile"}
        {data.rows_rejected > 0 ? ` · ${data.rows_rejected} uncertain ${data.rows_rejected === 1 ? "row" : "rows"} excluded` : ""}
      </p>
    </article>
  );
}

function BankPulse({ data }: { data: BankSummaryData }) {
  const sym = data.currency || "$";
  const money = (value: number) => `${sym}${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const largestExpense = data.top_expenses?.[0];

  return (
    <div className="bank-pulse" aria-label="Bank Statement Cashflow Breakdown">
      <div className="bank-pulse__header">
        <span className="bank-pulse__title">STATEMENT REVIEW · {shortDate(data.statement_start)}—{shortDate(data.statement_end)}</span>
        <span className="bank-pulse__count">{data.transaction_count} transactions</span>
      </div>

      <div className="bank-pulse__metrics">
        <div className="bank-pulse__metric">
          <span>Money in · {data.credit_count} credits</span>
          <strong className="text-income">{money(data.total_income)}</strong>
        </div>
        <div className="bank-pulse__metric">
          <span>Money out · {data.debit_count} debits</span>
          <strong className="text-expense">{money(data.total_expense)}</strong>
        </div>
        <div className="bank-pulse__metric">
          <span>Net cashflow</span>
          <strong className={data.net_cashflow >= 0 ? "text-income" : "text-expense"}>{data.net_cashflow < 0 ? "−" : "+"}{money(data.net_cashflow)}</strong>
        </div>
        <div className="bank-pulse__metric">
          <span>Average outgoing</span>
          <strong>{money(data.average_expense)}</strong>
        </div>
      </div>

      {(data.opening_balance !== null || data.closing_balance !== null) && (
        <div className="balance-journey">
          <div><span>Opening balance</span><strong>{data.opening_balance !== null ? money(data.opening_balance) : "Not stated"}</strong></div>
          <i aria-hidden="true">→</i>
          <div><span>Closing balance</span><strong>{data.closing_balance !== null ? money(data.closing_balance) : "Not stated"}</strong></div>
          {data.balance_change !== null && (
            <div className={data.balance_change >= 0 ? "balance-change balance-change--up" : "balance-change balance-change--down"}>
              <span>Change</span><strong>{data.balance_change < 0 ? "−" : "+"}{money(data.balance_change)}</strong>
            </div>
          )}
        </div>
      )}

      {(largestExpense || data.largest_credit || data.recurring_payments?.length > 0) && (
        <div className="bank-insights">
          {largestExpense && <div><span>Largest outgoing</span><strong>{largestExpense.description}</strong><small>{money(largestExpense.amount)} · {shortDate(largestExpense.date)}</small></div>}
          {data.largest_credit && <div><span>Largest incoming</span><strong>{data.largest_credit.description}</strong><small>{money(data.largest_credit.amount)} · {shortDate(data.largest_credit.date)}</small></div>}
          {data.recurring_payments?.length > 0 && <div><span>Repeated payment</span><strong>{data.recurring_payments[0].description}</strong><small>{money(data.recurring_payments[0].amount)} · {data.recurring_payments[0].count} times</small></div>}
        </div>
      )}

      {data.categories && data.categories.length > 0 && (
        <div className="bank-categories">
          <p className="bank-categories__title">Top Spending Outflows</p>
          <div className="bank-category-list">
            {data.categories.slice(0, 4).map((cat) => (
              <div className="bank-category-item" key={cat.category}>
                <div className="bank-category-item__head">
                  <span>{cat.category}</span>
                  <strong>{sym}{cat.amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ({cat.percentage}%)</strong>
                </div>
                <div className="bank-category-bar">
                  <div className="bank-category-bar__fill" style={{ width: `${Math.min(100, Math.max(8, cat.percentage))}%` }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {data.top_expenses && data.top_expenses.length > 0 && (
        <div className="bank-top-expenses">
          <p className="bank-categories__title">Largest outgoings</p>
          {data.top_expenses.slice(0, 5).map((expense) => (
            <div key={`${expense.date}-${expense.description}-${expense.amount}`}>
              <time>{shortDate(expense.date)}</time>
              <span>{expense.description}</span>
              <strong>{money(expense.amount)}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

async function prepareEntries(files: IncomingFile[], onProgress: (processed: number, total: number) => void): Promise<ScanEntry[]> {
  const entries: ScanEntry[] = [];
  for (let index = 0; index < files.length; index += 1) {
    const { file, relativePath } = files[index];
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (TEXT_TYPES.has(extension) || BINARY_TYPES.has(extension)) {
      const path = relativePath || file.webkitRelativePath || file.name;
      const modified = new Date(file.lastModified).toISOString();
      entries.push({
        path,
        size: file.size,
        modified,
        fingerprint: `${EXTRACTION_REVISION}\u0000${path}\u0000${file.size}\u0000${file.lastModified}`,
        file,
      });
    }
    if ((index + 1) % 300 === 0 || index === files.length - 1) {
      onProgress(index + 1, files.length);
      await yieldToMainThread();
    }
  }
  return entries;
}

async function snapshotInputFiles(
  fileList: FileList,
  onProgress: (processed: number) => void,
): Promise<IncomingFile[]> {
  const files: IncomingFile[] = [];
  for (let index = 0; index < fileList.length; index += 1) {
    const file = fileList.item(index);
    if (file) files.push({ file, relativePath: file.webkitRelativePath || file.name });
    if ((index + 1) % ENUMERATION_CHUNK === 0 || index === fileList.length - 1) {
      onProgress(index + 1);
      await yieldToMainThread();
    }
  }
  return files;
}

async function collectDroppedFiles(
  selection: DroppedSelection,
  onProgress: (progress: DiscoveryProgress) => void,
): Promise<DiscoveryResult> {
  const collected: IncomingFile[] = [];
  let discovered = 0;
  let supported = 0;
  let lastPaint = performance.now();

  const visitFile = async (name: string, relativePath: string, load: () => Promise<File>) => {
    discovered += 1;
    const accepted = !isIgnoredPath(relativePath) && isSupportedFileName(name);
    if (accepted) supported += 1;
    const now = performance.now();
    if (discovered === 1 || discovered % 100 === 0 || now - lastPaint > 80) {
      onProgress({ discovered, supported, currentPath: relativePath });
      lastPaint = now;
      await yieldToMainThread();
    }
    if (!accepted) return;
    try {
      const file = await load();
      collected.push({ file, relativePath: relativePath || file.name });
    } catch {
      // Ignore individual unreadable file
    }
  };

  let traversedSource = false;
  for (const source of selection.sources) {
    if (source.handlePromise) {
      try {
        const handle = await source.handlePromise;
        if (handle) {
          traversedSource = true;
          await walkHandle(handle, "", visitFile);
        } else if (source.fallbackFile) {
          traversedSource = true;
          const file = source.fallbackFile;
          await visitFile(file.name, file.name, async () => file);
        }
      } catch {
        // Source error fallback
      }
      continue;
    }
    if (source.entry) {
      traversedSource = true;
      await walkLegacyEntry(source.entry, "", visitFile);
    } else if (source.fallbackFile) {
      traversedSource = true;
      const file = source.fallbackFile;
      await visitFile(file.name, file.name, async () => file);
    }
  }

  if (!traversedSource) {
    for (const file of selection.fallbackFiles) {
      const path = file.webkitRelativePath || file.name;
      await visitFile(file.name, path, async () => file);
    }
  }
  onProgress({ discovered, supported, currentPath: "" });
  return { files: collected, discovered };
}

function captureDroppedSelection(dataTransfer: DataTransfer): DroppedSelection {
  const sources: DroppedSource[] = [];
  let hasEntryApi = false;
  for (let index = 0; index < dataTransfer.items.length; index += 1) {
    const item = dataTransfer.items[index];
    if (item.kind !== "file") continue;
    const fallbackFile = item.getAsFile();
    const getHandle = (item as DataTransferItem & { getAsFileSystemHandle?: () => Promise<FileSystemHandleLike | null> }).getAsFileSystemHandle;
    if (getHandle) {
      hasEntryApi = true;
      sources.push({ handlePromise: getHandle.call(item).catch(() => null), fallbackFile });
      continue;
    }
    const getEntry = (item as DataTransferItem & { webkitGetAsEntry?: () => LegacyEntry | null }).webkitGetAsEntry;
    const entry = getEntry ? getEntry.call(item) : null;
    if (entry) hasEntryApi = true;
    sources.push({ entry, fallbackFile });
  }
  return {
    sources,
    fallbackFiles: hasEntryApi ? [] : Array.from(dataTransfer.files),
  };
}

async function walkHandle(
  handle: FileSystemHandleLike,
  parentPath: string,
  visitFile: (name: string, relativePath: string, load: () => Promise<File>) => Promise<void>,
): Promise<void> {
  const path = parentPath ? `${parentPath}/${handle.name}` : handle.name;
  if (handle.kind === "directory" && isIgnoredDirectoryName(handle.name)) return;
  if (handle.kind === "file" && handle.getFile) {
    try {
      await visitFile(handle.name, path, () => handle.getFile!());
    } catch {
      // Handled in visitFile
    }
    return;
  }
  if (handle.kind === "directory" && handle.values) {
    try {
      const children: FileSystemHandleLike[] = [];
      for await (const child of handle.values()) {
        children.push(child);
      }
      for (let index = 0; index < children.length; index += DISCOVERY_CONCURRENCY) {
        const batch = children.slice(index, index + DISCOVERY_CONCURRENCY);
        await Promise.all(batch.map((child) => walkHandle(child, path, visitFile).catch(() => {})));
      }
    } catch {
      // Directory iteration catch
    }
  }
}

async function walkLegacyEntry(
  entry: LegacyEntry,
  parentPath: string,
  visitFile: (name: string, relativePath: string, load: () => Promise<File>) => Promise<void>,
): Promise<void> {
  const path = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  if (entry.isDirectory && isIgnoredDirectoryName(entry.name)) return;
  if (entry.isFile && entry.file) {
    try {
      await visitFile(entry.name, path, () => new Promise<File>((resolve, reject) => entry.file?.(resolve, reject)));
    } catch {
      // Handled in visitFile
    }
    return;
  }
  if (entry.isDirectory && entry.createReader) {
    const reader = entry.createReader();
    while (true) {
      try {
        const children = await new Promise<LegacyEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
        if (!children.length) break;
        for (let index = 0; index < children.length; index += DISCOVERY_CONCURRENCY) {
          await Promise.all(
            children
              .slice(index, index + DISCOVERY_CONCURRENCY)
              .map((child) => walkLegacyEntry(child, path, visitFile).catch(() => {})),
          );
        }
      } catch {
        break;
      }
    }
  }
}

function isSupportedFileName(name: string): boolean {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return TEXT_TYPES.has(extension) || BINARY_TYPES.has(extension);
}

function isIgnoredDirectoryName(name: string): boolean {
  return name.startsWith(".") || IGNORED_DIRECTORIES.has(name.toLowerCase());
}

function isIgnoredPath(path: string): boolean {
  const parts = path.split("/").filter(Boolean);
  return parts.some((part, index) => index < parts.length - 1
    ? isIgnoredDirectoryName(part)
    : part.startsWith("."));
}

function prepareDemoEntries(files: UploadFile[]): ScanEntry[] {
  return files.map((file) => ({
    path: file.path,
    size: file.size ?? file.content.length,
    modified: file.modified,
    fingerprint: `${EXTRACTION_REVISION}\u0000${file.path}\u0000${file.content.length}\u0000${file.modified}`,
    prepared: file,
  }));
}

function makeUploadBatches(entries: ScanEntry[]): ScanEntry[][] {
  const batches: ScanEntry[][] = [];
  let current: ScanEntry[] = [];
  let bytes = 0;
  // Bounded safe raw upload limit: 3.5 MB per batch so base64 payload is ~4.6 MB, well below server limits
  const MAX_BATCH_BYTES = 3.5 * 1024 * 1024;
  for (let entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
    const entry = entries[entryIndex];
    const estimated = Math.min(entry.size, 8 * 1024 * 1024);
    if (current.length >= 6 || (current.length > 0 && bytes + estimated > MAX_BATCH_BYTES)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(entry);
    bytes += estimated;
  }
  if (current.length) batches.push(current);
  return batches;
}

function chunk<T>(items: T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let index = 0; index < items.length; index += size) chunks.push(items.slice(index, index + size));
  return chunks;
}

async function makeScanKey(entries: ScanEntry[], onProgress: (processed: number, total: number) => void) {
  const root = entries[0]?.path.split("/")[0] ?? "folder";
  let checksum = 2166136261;
  let totalBytes = 0;
  for (let entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
    const entry = entries[entryIndex];
    totalBytes += entry.size;
    for (let index = 0; index < entry.fingerprint.length; index += 1) {
      checksum ^= entry.fingerprint.charCodeAt(index);
      checksum = Math.imul(checksum, 16777619);
    }
    const processed = entryIndex + 1;
    if (processed % 500 === 0 || processed === entries.length) {
      onProgress(processed, entries.length);
      await yieldToMainThread();
    }
  }
  return `${root}:${entries.length}:${totalBytes}:${checksum >>> 0}`;
}

function dayDifference(from: Date, to: Date) {
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
}

type WorkerProgress = { path: string; index: number; count: number; loaded: number; total: number };

function prepareBatchInWorker(
  entries: ScanEntry[],
  dateOrder: string,
  signal: AbortSignal,
  onProgress: (progress: WorkerProgress) => void,
): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const worker = new Worker("/file-prep-worker.js");
    const stop = () => {
      worker.terminate();
      reject(new DOMException("Scan paused", "AbortError"));
    };
    signal.addEventListener("abort", stop, { once: true });
    worker.onmessage = (event: MessageEvent<{ type: string; body?: Blob; message?: string } & WorkerProgress>) => {
      if (event.data.type === "progress") {
        onProgress(event.data);
        return;
      }
      signal.removeEventListener("abort", stop);
      worker.terminate();
      if (event.data.type === "done" && event.data.body) resolve(event.data.body);
      else reject(new Error(event.data.message ?? "A file could not be prepared."));
    };
    worker.onerror = () => {
      signal.removeEventListener("abort", stop);
      worker.terminate();
      reject(new Error("The background file reader could not start."));
    };
    worker.postMessage({ entries, dateOrder });
  });
}

function waitForFirstPaint(): Promise<void> {
  return new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
}

function yieldToMainThread(): Promise<void> {
  const scheduler = (globalThis as typeof globalThis & { scheduler?: { yield?: () => Promise<void> } }).scheduler;
  if (scheduler?.yield) return scheduler.yield();
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function relativeLabel(value: string, today: Date) {
  const days = dayDifference(today, new Date(`${value}T12:00:00`));
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days === -1) return "Yesterday";
  if (days < 0) return `${Math.abs(days)} days overdue`;
  return `In ${days} days`;
}

function groupDocuments(items: Commitment[], today: Date) {
  const documents = new Map<string, Commitment[]>();
  for (const item of items) {
    const milestones = documents.get(item.source) ?? [];
    milestones.push(item);
    documents.set(item.source, milestones);
  }

  const buckets = [
    { label: "Overdue", documents: [] as DocumentBundle[] },
    { label: "Next 30 days", documents: [] as DocumentBundle[] },
    { label: "Later", documents: [] as DocumentBundle[] },
  ];
  for (const [source, milestones] of documents) {
    milestones.sort((first, second) => first.date.localeCompare(second.date));
    const lead = milestones.find((item) => new Date(`${item.date}T12:00:00`) >= today) ?? milestones[0];
    const days = dayDifference(today, new Date(`${lead.date}T12:00:00`));
    (days < 0 ? buckets[0] : days <= 30 ? buckets[1] : buckets[2]).documents.push({ source, items: milestones, lead });
  }
  for (const bucket of buckets) bucket.documents.sort((first, second) => first.lead.date.localeCompare(second.lead.date));
  return buckets.filter((bucket) => bucket.documents.length);
}

function documentLabel(path: string): string {
  const filename = path.split("/").pop() ?? path;
  const stem = filename.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return stem.replace(/\b\w/g, (letter) => letter.toUpperCase()) || "Untitled document";
}

function shortDate(value: string): string {
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(new Date(`${value}T12:00:00`));
}

function calendarFilename(title: string): string {
  const safe = title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  return safe || "paperclock-event";
}

function formatEta(seconds: number) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sec`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return `${hours} hr ${minutes} min`;
}
