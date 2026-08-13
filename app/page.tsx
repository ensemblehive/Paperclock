"use client";

import { ChangeEvent, DragEvent, useEffect, useMemo, useRef, useState } from "react";

const ENGINE = "http://127.0.0.1:4312";
const TEXT_TYPES = new Set([
  "txt", "md", "markdown", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml",
  "ini", "cfg", "html", "htm", "xml", "eml", "ics", "rtf", "log",
]);
const BINARY_TYPES = new Set(["pdf", "docx"]);

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
  today: string;
  files_scanned: number;
  files_skipped: number;
  dates_reviewed: number;
  noise_removed: number;
  commitments: Commitment[];
  warnings: string[];
};

type UploadFile = {
  path: string;
  content: string;
  encoding: "text" | "base64";
  modified: string;
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
  },
  {
    path: "Work/vendor-notes.md",
    content: `## Atlas rollout\n- Security review must be submitted by ${isoOffset(21)}.\n- Production certificate expires ${isoOffset(67)}.\n- Kickoff completed on ${isoOffset(-40)}.`,
    encoding: "text",
    modified: new Date().toISOString(),
  },
  {
    path: "Receipts/camera-warranty.txt",
    content: `Purchase date: ${isoOffset(-280)}\nExtended coverage is valid until ${isoOffset(45)}. File any repair claim before that date.`,
    encoding: "text",
    modified: new Date().toISOString(),
  },
];

export default function Home() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [engineOnline, setEngineOnline] = useState<boolean | null>(null);
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
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

  async function runScan(files: UploadFile[]) {
    if (!files.length) {
      setError("No supported files found. Try PDF, DOCX, email, Markdown, CSV, or plain text.");
      return;
    }
    setLoading(true);
    setError("");
    setDismissed(new Set());
    try {
      const response = await fetch(`${ENGINE}/api/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files, date_order: dateOrder }),
      });
      if (!response.ok) throw new Error("The local engine could not read that folder.");
      setResult(await response.json());
      setEngineOnline(true);
    } catch {
      setEngineOnline(false);
      setError("Paperclock’s local engine isn’t running. Start it with ./run.sh, then try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleFiles(fileList: FileList | File[]) {
    setLoading(true);
    setError("");
    try {
      const files = await serializeFiles(Array.from(fileList));
      await runScan(files);
    } catch (caught) {
      setLoading(false);
      setError(caught instanceof Error ? caught.message : "Those files could not be prepared.");
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    void handleFiles(event.dataTransfer.files);
  }

  function onInput(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void handleFiles(event.target.files);
    event.target.value = "";
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
          <a href="https://github.com" className="source-link">Open source <span aria-hidden="true">↗</span></a>
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
            className={`dropzone ${dragging ? "dropzone--active" : ""}`}
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
              accept=".pdf,.docx,.txt,.md,.csv,.json,.yaml,.yml,.html,.eml,.ics,.rtf,.log"
            />
            <div className="dropzone__icon" aria-hidden="true"><span>↓</span></div>
            <div>
              <strong>{loading ? "Listening to the paper trail…" : "Drop a folder here"}</strong>
              <span>or <button onClick={() => inputRef.current?.click()}>choose one</button> · up to 500 files</span>
            </div>
            <div className="format-row"><span>PDF</span><span>DOCX</span><span>EMAIL</span><span>TEXT</span><span>CALENDAR</span></div>
          </div>
          <div className="landing__footer">
            <button className="demo-button" disabled={loading} onClick={() => void runScan(demoFiles())}>
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
        <div className="timeline-head">
          <div>
            <p className="section-kicker">Your horizon</p>
            <h1>What needs attention</h1>
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
        {visible.length === 0 ? (
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

async function serializeFiles(files: File[]): Promise<UploadFile[]> {
  const supported = files.filter((file) => {
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    return TEXT_TYPES.has(extension) || BINARY_TYPES.has(extension);
  }).slice(0, 500);
  const total = supported.reduce((sum, file) => sum + file.size, 0);
  if (total > 24 * 1024 * 1024) throw new Error("That selection is over 24 MB. Try a smaller folder.");

  return Promise.all(supported.map(async (file) => {
    const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
    const binary = BINARY_TYPES.has(extension);
    return {
      path: file.webkitRelativePath || file.name,
      content: binary ? await fileToBase64(file) : await file.text(),
      encoding: binary ? "base64" as const : "text" as const,
      modified: new Date(file.lastModified).toISOString(),
    };
  }));
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error(`Could not read ${file.name}`));
    reader.readAsDataURL(file);
  });
}

function dayDifference(from: Date, to: Date) {
  return Math.round((to.getTime() - from.getTime()) / 86_400_000);
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
