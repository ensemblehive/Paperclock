"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

const ENGINE = "http://127.0.0.1:4312";
const TEXT_TYPES = new Set([
  "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml",
  "ini", "cfg", "html", "htm", "xml", "eml", "ics", "rtf", "log",
]);
const BINARY_TYPES = new Set(["pdf", "docx"]);
const DISCOVERY_CONCURRENCY = 8;
const ENUMERATION_CHUNK = 250;

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
};

const categoryNames: Record<string, string> = {
  expiry: "Expiry",
  renewal: "Renewal",
  cancellation: "Cancel window",
  money: "Money",
  submission: "Submission",
  appointment: "Appointment",
  delivery: "Delivery",
  warranty: "Coverage",
  action: "Action",
};

function isoOffset(days: number) {
  const value = new Date();
  value.setHours(12, 0, 0, 0);
  value.setDate(value.getDate() + days);
  return value.toISOString().slice(0, 10);
}

const demoFiles = (): UploadFile[] => [
  {
    path: "Household/energy-plan.txt",
    content: `Your fixed-rate energy plan renews automatically on ${isoOffset(38)}. Cancel before ${isoOffset(10)} to avoid the new variable rate.\nStatement date: ${isoOffset(-12)}.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 140,
  },
  {
    path: "Work/vendor-notes.md",
    content: `## Atlas rollout\n- Security review must be submitted by ${isoOffset(21)}.\n- Production certificate expires ${isoOffset(67)}.\n- Kickoff completed on ${isoOffset(-40)}.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 190,
  },
  {
    path: "Receipts/camera-warranty.txt",
    content: `Purchase date: ${isoOffset(-280)}\nExtended coverage is valid until ${isoOffset(45)}. File any repair claim before that date.`,
    encoding: "text",
    modified: new Date().toISOString(),
    size: 130,
  },
];

export default function Home() {
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const lastEntriesRef = useRef<ScanEntry[]>([]);
  const [engineOnline, setEngineOnline] = useState<boolean | null>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activity, setActivity] = useState<ScanActivity | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const [dateOrder, setDateOrder] = useState("day-first");
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetch(`${ENGINE}/api/health`)
      .then((response) => setEngineOnline(response.ok))
      .catch(() => setEngineOnline(false));
  }, []);

  const visible = useMemo(() => {
    if (!result) return [];
    const today = new Date(`${result.today}T12:00:00`);
    return result.commitments.filter((item) => {
      if (dismissed.has(item.id)) return false;
      const days = dayDifference(today, new Date(`${item.date}T12:00:00`));
      if (filter === "soon") return days >= 0 && days <= 30;
      if (filter === "expiry") return ["expiry", "renewal", "warranty"].includes(item.category);
      if (filter === "money") return item.category === "money";
      return true;
    });
  }, [dismissed, filter, result]);

  async function runScan(entries: ScanEntry[]) {
    if (!entries.length) {
      setError("No supported files found. Try PDF, DOCX, email, Markdown, CSV, or plain text.");
      return;
    }
    lastEntriesRef.current = entries;
    setLoading(true);
    setError("");
    setDismissed(new Set());
    setActivity({
      active: true,
      phase: "indexing",
      current: `Indexing ${entries.length.toLocaleString()} supported ${entries.length === 1 ? "file" : "files"}`,
      detail: "Checking what has changed before reading file contents",
      filePercent: null,
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
        });
      });
      if (!entries.length) {
        setLoading(false);
        setActivity(null);
        setError(
          discoveredCount
            ? `Paperclock found ${discoveredCount.toLocaleString()} ${discoveredCount === 1 ? "file" : "files"}, but none had a supported extension. Try PDF, DOCX, EML, ICS, Markdown, CSV, JSON, or plain text.`
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

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    // Capture protected drag handles inside the drop event, but never flatten a
    // modern directory's FileList before the first frame can be painted.
    const selection = captureDroppedSelection(event.dataTransfer);
    setLoading(true);
    setError("");
    setActivity({
      active: true,
      phase: "preparing",
      current: "Discovering files in the dropped folder",
      detail: "0 files found so far",
      filePercent: null,
    });
    void (async () => {
      try {
        await waitForFirstPaint();
        const discovery = await collectDroppedFiles(selection, ({ discovered, supported, currentPath }) => {
          setActivity({
            active: true,
            phase: "preparing",
            current: "Discovering files in the dropped folder",
            detail: `${discovered.toLocaleString()} found · ${supported.toLocaleString()} supported${currentPath ? ` · ${currentPath}` : ""}`,
            filePercent: null,
          });
        });
        await handleFiles(discovery.files, true, discovery.discovered);
      } catch {
        setLoading(false);
        setActivity(null);
        setError("That folder could not be read. Use “choose a folder” to select it through the folder picker.");
      }
    })();
  }

  function onInput(event: ChangeEvent<HTMLInputElement>) {
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
      // The active request was already stopped; the next selection will reuse cached work.
    }
  }

  async function downloadCalendar() {
    if (!result) return;
    const commitments = result.commitments.filter((item) => !dismissed.has(item.id));
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
      anchor.download = "paperclock.ics";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch {
      setError("The calendar could not be created. Is the local engine still running?");
    }
  }

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
          <span className="source-link">MIT · open source</span>
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
            onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
            onDragOver={(event) => event.preventDefault()}
            onDragLeave={() => setDragging(false)}
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
              <span>{dragging ? "Paperclock is ready" : <>or <button onClick={() => inputRef.current?.click()}>choose a folder</button> · any number of files</>}</span>
            </div>
            <div className="format-row"><span>PDF</span><span>DOCX</span><span>EMAIL</span><span>TEXT</span><span>CALENDAR</span></div>
          </div>
          {loading && !result && activity && (
            <div className="preflight" aria-live="polite">
              <span className="preflight__pulse" aria-hidden="true"><i /><i /><i /></span>
              <div>
                <strong>{activity.current}</strong>
                <small>{activity.detail}</small>
              </div>
              <b>{activity.filePercent === null || activity.filePercent === undefined ? "…" : `${activity.filePercent}%`}</b>
              <div className={`preflight__track ${activity.filePercent === null || activity.filePercent === undefined ? "preflight__track--indeterminate" : ""}`}>
                <i style={activity.filePercent === null || activity.filePercent === undefined ? undefined : { width: `${activity.filePercent}%` }} />
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
          dismiss={(id) => setDismissed((current) => new Set(current).add(id))}
          rescan={() => inputRef.current?.click()}
          downloadCalendar={() => void downloadCalendar()}
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
  result, visible, filter, setFilter, dismiss, rescan, downloadCalendar, loading, error,
  activity, cancelScan, resumeScan,
}: {
  result: ScanResult;
  visible: Commitment[];
  filter: string;
  setFilter: (value: string) => void;
  dismiss: (id: string) => void;
  rescan: () => void;
  downloadCalendar: () => void;
  loading: boolean;
  error: string;
  activity: ScanActivity | null;
  cancelScan: () => void;
  resumeScan: () => void;
}) {
  const today = new Date(`${result.today}T12:00:00`);
  const nearest = result.commitments.find((item) => new Date(`${item.date}T12:00:00`) >= today);
  const groups = groupCommitments(visible, today);

  return (
    <section className="results-shell">
      <aside className="summary">
        <div>
          <p className="section-kicker">Folder pulse</p>
          <h2>{result.commitments.length}<span> commitments<br />worth seeing</span></h2>
          <div className="summary__stats">
            <div><strong>{result.files_scanned}</strong><span>files read</span></div>
            <div><strong>{result.noise_removed}</strong><span>dates ignored</span></div>
          </div>
          <div className="nearest">
            <span>Next on the clock</span>
            <strong>{nearest ? relativeLabel(nearest.date, today) : "Nothing upcoming"}</strong>
            <p>{nearest?.title ?? "Your timeline is clear."}</p>
          </div>
        </div>
        <div className="summary__actions">
          <button className="primary" onClick={downloadCalendar}>Add all to calendar <span>↓</span></button>
          <button className="secondary" disabled={loading} onClick={rescan}>{loading ? "Scanning…" : "Scan another folder"}</button>
          <p>Calendar export contains titles and source paths, never your file contents.</p>
        </div>
      </aside>

      <div className="timeline-panel">
        {activity && activity.phase !== "complete" && (
          <ScanProgress result={result} activity={activity} cancelScan={cancelScan} resumeScan={resumeScan} />
        )}
        <div className="timeline-head">
          <div>
            <p className="section-kicker">Your horizon</p>
            <h1>{activity?.active ? "Reading your horizon" : "What needs attention"}</h1>
          </div>
          <div className="filters" aria-label="Filter commitments">
            {[["all", "All"], ["soon", "Next 30 days"], ["expiry", "Expiries"], ["money", "Money"]].map(([value, label]) => (
              <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>
            ))}
          </div>
        </div>
        {error && <div className="notice notice--results" role="alert">{error}</div>}
        {result.warnings.length > 0 && (
          <details className="warnings"><summary>{result.files_skipped} files skipped</summary>{result.warnings.map((warning) => <p key={warning}>{warning}</p>)}</details>
        )}
        {visible.length === 0 && activity?.active ? (
          <div className="waiting-state">
            <div className="waiting-papers" aria-hidden="true"><i /><i /><i /></div>
            <h3>Dates will appear here as they’re found</h3>
            <p>Paperclock is reading in small batches, so the interface stays responsive.</p>
          </div>
        ) : visible.length === 0 ? (
          <div className="empty-state"><span>○</span><h3>No commitments in this view</h3><p>Try another filter or scan a different folder.</p></div>
        ) : (
          <div className="groups">
            {groups.map((group) => (
              <div className="time-group" key={group.label}>
                <div className="group-label"><span>{group.label}</span><i /></div>
                {group.items.map((item) => (
                  <CommitmentCard key={item.id} item={item} today={today} dismiss={() => dismiss(item.id)} />
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
  return (
    <section className={`scan-progress scan-progress--${activity.phase}`} aria-live="polite">
      <div className="scan-progress__clock" aria-hidden="true"><i /><i /><b /></div>
      <div className="scan-progress__body">
        <div className="scan-progress__top">
          <span>{phaseLabel}</span>
          <strong>{percent}%</strong>
        </div>
        <p>{activity.current}</p>
        {activity.detail && <small className="scan-progress__detail">{activity.detail}</small>}
        {activity.phase === "preparing" && activity.filePercent !== null && activity.filePercent !== undefined && (
          <div className="file-progress"><i style={{ width: `${activity.filePercent}%` }} /></div>
        )}
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

function CommitmentCard({ item, today, dismiss }: { item: Commitment; today: Date; dismiss: () => void }) {
  const itemDate = new Date(`${item.date}T12:00:00`);
  const days = dayDifference(today, itemDate);
  const dateParts = new Intl.DateTimeFormat("en", { month: "short", day: "2-digit", year: "numeric" }).formatToParts(itemDate);
  const month = dateParts.find((part) => part.type === "month")?.value;
  const day = dateParts.find((part) => part.type === "day")?.value;

  return (
    <article className={`commitment commitment--${item.category}`}>
      <div className="date-tile"><span>{month}</span><strong>{day}</strong><small>{itemDate.getFullYear()}</small></div>
      <div className="commitment__body">
        <div className="commitment__meta">
          <span className="category">{categoryNames[item.category] ?? "Action"}</span>
          <span className={days < 0 ? "relative relative--late" : "relative"}>{relativeLabel(item.date, today)}</span>
          {item.ambiguous && <span className="ambiguous">Check date order</span>}
        </div>
        <h3>{item.title}</h3>
        <p className="source"><span aria-hidden="true">⌁</span> {item.source} <b>·</b> line {item.line}</p>
        <details>
          <summary>Why Paperclock kept this <span>+</span></summary>
          <blockquote>“{item.snippet}”</blockquote>
          <p>{item.reason}. Confidence {item.confidence}%.</p>
        </details>
      </div>
      <button className="dismiss" onClick={dismiss} title="Hide this commitment" aria-label={`Hide ${item.title}`}>×</button>
    </article>
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
        fingerprint: `${path}\u0000${file.size}\u0000${file.lastModified}`,
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
    const accepted = isSupportedFileName(name);
    if (accepted) supported += 1;
    const now = performance.now();
    if (discovered === 1 || discovered % 100 === 0 || now - lastPaint > 80) {
      onProgress({ discovered, supported, currentPath: relativePath });
      lastPaint = now;
      await yieldToMainThread();
    }
    if (!accepted) return;
    const file = await load();
    collected.push({ file, relativePath: relativePath || file.name });
  };

  let traversedSource = false;
  for (const source of selection.sources) {
    if (source.handlePromise) {
      const handle = await source.handlePromise;
      if (handle) {
        traversedSource = true;
        await walkHandle(handle, "", visitFile);
      } else if (source.fallbackFile) {
        traversedSource = true;
        const file = source.fallbackFile;
        await visitFile(file.name, file.name, async () => file);
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
    // This synchronous compatibility path is used only by browsers without a
    // directory-entry API. Modern folder drops avoid this potentially large copy.
    fallbackFiles: hasEntryApi ? [] : Array.from(dataTransfer.files),
  };
}

async function walkHandle(
  handle: FileSystemHandleLike,
  parentPath: string,
  visitFile: (name: string, relativePath: string, load: () => Promise<File>) => Promise<void>,
): Promise<void> {
  const path = parentPath ? `${parentPath}/${handle.name}` : handle.name;
  if (handle.kind === "file" && handle.getFile) {
    await visitFile(handle.name, path, () => handle.getFile!());
    return;
  }
  if (handle.kind === "directory" && handle.values) {
    let pending: Promise<void>[] = [];
    for await (const child of handle.values()) {
      pending.push(walkHandle(child, path, visitFile));
      if (pending.length === DISCOVERY_CONCURRENCY) {
        await Promise.all(pending);
        pending = [];
      }
    }
    await Promise.all(pending);
  }
}

async function walkLegacyEntry(
  entry: LegacyEntry,
  parentPath: string,
  visitFile: (name: string, relativePath: string, load: () => Promise<File>) => Promise<void>,
): Promise<void> {
  const path = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  if (entry.isFile && entry.file) {
    await visitFile(entry.name, path, () => new Promise<File>((resolve, reject) => entry.file?.(resolve, reject)));
    return;
  }
  if (entry.isDirectory && entry.createReader) {
    const reader = entry.createReader();
    while (true) {
      const children = await new Promise<LegacyEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
      if (!children.length) break;
      for (let index = 0; index < children.length; index += DISCOVERY_CONCURRENCY) {
        await Promise.all(
          children
            .slice(index, index + DISCOVERY_CONCURRENCY)
            .map((child) => walkLegacyEntry(child, path, visitFile)),
        );
      }
    }
  }
}

function isSupportedFileName(name: string): boolean {
  const extension = name.split(".").pop()?.toLowerCase() ?? "";
  return TEXT_TYPES.has(extension) || BINARY_TYPES.has(extension);
}

function prepareDemoEntries(files: UploadFile[]): ScanEntry[] {
  return files.map((file) => ({
    path: file.path,
    size: file.size ?? file.content.length,
    modified: file.modified,
    fingerprint: `${file.path}\u0000${file.content.length}\u0000${file.modified}`,
    prepared: file,
  }));
}

function makeUploadBatches(entries: ScanEntry[]): ScanEntry[][] {
  const batches: ScanEntry[][] = [];
  let current: ScanEntry[] = [];
  let bytes = 0;
  for (let entryIndex = 0; entryIndex < entries.length; entryIndex += 1) {
    const entry = entries[entryIndex];
    const estimated = Math.min(entry.size, 8 * 1024 * 1024);
    if (current.length >= 6 || (current.length > 0 && bytes + estimated > 6 * 1024 * 1024)) {
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

function groupCommitments(items: Commitment[], today: Date) {
  const buckets = [
    { label: "Overdue", items: [] as Commitment[] },
    { label: "Next 30 days", items: [] as Commitment[] },
    { label: "Later", items: [] as Commitment[] },
  ];
  for (const item of items) {
    const days = dayDifference(today, new Date(`${item.date}T12:00:00`));
    (days < 0 ? buckets[0] : days <= 30 ? buckets[1] : buckets[2]).items.push(item);
  }
  return buckets.filter((bucket) => bucket.items.length);
}

function formatEta(seconds: number) {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} sec`;
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.ceil((seconds % 3600) / 60);
  return `${hours} hr ${minutes} min`;
}
