import { useState } from "react";
import { useServerFn } from "@tanstack/react-start";
import sampleFiles from "../../kyc_agent/data/samples.json";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { reviewApplication } from "@/lib/kyc/review-fn";
import type { Application, CaseFile, Finding, Recommendation, Review, ToolCall } from "@/lib/kyc/types";
import { cn } from "@/lib/utils";

const CASES = sampleFiles as CaseFile[];

const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  approve: "Approve",
  escalate: "Escalate",
  reject: "Reject",
};

const RECOMMENDATION_TONE: Record<Recommendation, string> = {
  approve: "text-approve",
  escalate: "text-escalate",
  reject: "text-reject",
};

function fingerprint(application: Application): string {
  return JSON.stringify(application);
}

function documentLabel(type: string): string {
  if (type === "passport") return "Passport";
  if (type === "driver_license") return "Driver license";
  if (type === "national_id") return "National ID";
  return type;
}

function formatMoney(amount: number, currency: string): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount);
  } catch {
    return `${amount.toFixed(2)} ${currency}`;
  }
}

function fieldFor(source: string): string {
  const transaction = source.match(/application\.transactions\[\d+\]/);
  if (transaction) return transaction[0];
  if (source.startsWith("application.id_document.")) return source;
  if (source.startsWith("application.")) return source;
  if (source.startsWith("sanctions")) return "sanctions_lookup";
  if (source.startsWith("adverse_media")) return "adverse_media_lookup";
  return source;
}

function cites(finding: Finding | undefined, field: string): boolean {
  if (!finding) return false;
  return finding.citations.some((citation) => fieldFor(citation.source) === field);
}

function summarizeCall(call: ToolCall): string {
  const outputs = call.outputs;
  if (outputs.error) return outputs.error;
  if (call.tool === "sanctions_lookup") {
    if (!outputs.hit_count) return "No match";
    return (outputs.matches ?? [])
      .map((match) => `${match.list_id} · ${match.listed_name} · date of birth ${(match.dob_status ?? "").replaceAll("_", " ")}`)
      .join("; ");
  }
  if (call.tool === "adverse_media_lookup") {
    if (!outputs.hit_count) return "No articles";
    return (outputs.articles ?? []).map((article) => article.headline).join("; ");
  }
  if (call.tool === "submit_review") return "Disposition submitted";
  return call.tool;
}

function toolLabel(tool: string): string {
  if (tool === "sanctions_lookup") return "Sanctions lookup";
  if (tool === "adverse_media_lookup") return "Adverse media lookup";
  if (tool === "submit_review") return "Submit review";
  return tool;
}

export function CaseDesk() {
  const reviewFn = useServerFn(reviewApplication);
  const [files, setFiles] = useState<CaseFile[]>(() => structuredClone(CASES));
  const [activeId, setActiveId] = useState(CASES[0]?.id ?? "");
  const [reviews, setReviews] = useState<Record<string, { review: Review; fingerprint: string }>>({});
  const [selectedFinding, setSelectedFinding] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = files.find((file) => file.id === activeId) ?? files[0];
  const stored = active ? reviews[active.id] : undefined;
  const stale = Boolean(stored && active && stored.fingerprint !== fingerprint(active.application));
  const review = stored?.review;
  const finding = review?.findings[selectedFinding];

  function patch(update: (application: Application) => Application) {
    if (!active) return;
    setFiles((current) =>
      current.map((file) => (file.id === active.id ? { ...file, application: update(file.application) } : file)),
    );
  }

  async function onReview() {
    if (!active) return;
    setPending(true);
    setError(null);
    try {
      const next = await reviewFn({ data: active.application });
      setReviews((current) => ({
        ...current,
        [active.id]: { review: next, fingerprint: fingerprint(active.application) },
      }));
      setSelectedFinding(0);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "The review did not finish.");
    } finally {
      setPending(false);
    }
  }

  function openCitation(source: string) {
    const target = document.querySelector(`[data-field="${CSS.escape(fieldFor(source))}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  if (!active) return null;

  return (
    <main className="mx-auto min-h-screen max-w-6xl px-4 pt-6 pb-12">
      <header className="mb-6 max-w-2xl">
        <p className="font-serif text-lg leading-snug">Halden</p>
        <h1 className="mt-1 text-base font-medium">Case desk</h1>
        <p className="mt-2 text-sm text-muted">
          Each review starts from the file in front of you. Sanctions and adverse media are separate lookups, and both calls are logged.
        </p>
      </header>

      <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-[15rem_minmax(0,1fr)_22rem]">
        <section className="order-1 min-w-0 rounded-xl border border-line bg-elevated p-4" aria-label="Files">
          <h2 className="mb-3 text-sm font-medium text-muted">Files</h2>
          <div className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
            {files.map((file) => {
              const saved = reviews[file.id];
              const current = saved && saved.fingerprint === fingerprint(file.application) ? saved.review : undefined;
              const selected = file.id === active.id;
              return (
                <button
                  key={file.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => {
                    setActiveId(file.id);
                    setSelectedFinding(0);
                    setError(null);
                  }}
                  className={cn(
                    "press flex min-h-11 w-44 shrink-0 flex-col items-start rounded-sm px-3 py-2 text-left lg:w-full",
                    selected ? "bg-subtle" : "hover:bg-subtle",
                  )}
                >
                  <span className="font-medium">{file.application.name}</span>
                  <span className="text-sm text-muted">{file.blurb}</span>
                  {current ? (
                    <span className={cn("mt-1 text-sm font-medium", RECOMMENDATION_TONE[current.recommendation])}>
                      {RECOMMENDATION_LABEL[current.recommendation]} · {current.risk_score}
                    </span>
                  ) : null}
                </button>
              );
            })}
          </div>
        </section>

        <section className="order-3 min-w-0 rounded-xl border border-line bg-elevated p-4 lg:order-2" aria-label="Application">
          <h2 className="font-serif text-display">{active.application.name}</h2>
          <p className="mt-1 text-sm text-muted">{active.blurb}</p>

          <div className="mt-6 space-y-6">
            <div>
              <h3 className="text-sm font-medium text-muted">Subject</h3>
              <div className="mt-3 space-y-3">
                <Field
                  label="Name"
                  field="application.name"
                  highlighted={cites(finding, "application.name")}
                  value={active.application.name}
                  onChange={(value) => patch((application) => ({ ...application, name: value }))}
                />
                <Field
                  label="Date of birth"
                  field="application.date_of_birth"
                  type="date"
                  highlighted={cites(finding, "application.date_of_birth")}
                  value={active.application.date_of_birth}
                  onChange={(value) => patch((application) => ({ ...application, date_of_birth: value }))}
                />
                <Field
                  label="Address"
                  field="application.address"
                  highlighted={cites(finding, "application.address")}
                  value={active.application.address}
                  onChange={(value) => patch((application) => ({ ...application, address: value }))}
                />
              </div>
            </div>

            <div>
              <h3 className="text-sm font-medium text-muted">Identity document</h3>
              <p className="mt-3 text-sm text-faint">
                {documentLabel(active.application.id_document.document_type)} · {active.application.id_document.issuing_country}
              </p>
              <div
                data-field="application.id_document.document_number"
                className={cn(
                  "field-shell mt-2 rounded-lg px-3 py-2",
                  cites(finding, "application.id_document.document_number") && "bg-subtle",
                )}
              >
                <p className="text-sm text-muted">Number</p>
                <p className="font-mono text-sm">{active.application.id_document.document_number}</p>
              </div>
              <div className="mt-3 space-y-3">
                <Field
                  label="Name on document"
                  field="application.id_document.name_on_document"
                  highlighted={cites(finding, "application.id_document.name_on_document")}
                  value={active.application.id_document.name_on_document}
                  onChange={(value) =>
                    patch((application) => ({
                      ...application,
                      id_document: { ...application.id_document, name_on_document: value },
                    }))
                  }
                />
                <Field
                  label="Expiry"
                  field="application.id_document.expiry_date"
                  type="date"
                  highlighted={cites(finding, "application.id_document.expiry_date")}
                  value={active.application.id_document.expiry_date}
                  onChange={(value) =>
                    patch((application) => ({
                      ...application,
                      id_document: { ...application.id_document, expiry_date: value },
                    }))
                  }
                />
              </div>
            </div>

            <div>
              <h3 className="text-sm font-medium text-muted">Transactions</h3>
              <ul className="mt-3 space-y-2">
                {active.application.transactions.map((txn, index) => {
                  const field = `application.transactions[${index}]`;
                  return (
                    <li
                      key={txn.id}
                      data-field={field}
                      className={cn("field-shell rounded-lg px-3 py-3", cites(finding, field) && "bg-subtle")}
                    >
                      <div className="flex items-baseline justify-between gap-3">
                        <p className="min-w-0 font-medium">{txn.counterparty}</p>
                        <p className="shrink-0 tabular-nums">{formatMoney(txn.amount, txn.currency)}</p>
                      </div>
                      <p className="text-sm text-muted">
                        {txn.date} · {txn.direction === "credit" ? "In" : "Out"} · {txn.channel} · {txn.country}
                      </p>
                      <p className="text-sm text-faint">{txn.description}</p>
                    </li>
                  );
                })}
              </ul>
            </div>
          </div>
        </section>

        <section className="order-2 min-w-0 rounded-xl border border-line bg-elevated p-4 lg:order-3" aria-live="polite">
          <h2 className="text-sm font-medium text-muted">Disposition</h2>
          <Button className="mt-3 w-full" onClick={onReview} disabled={pending}>
            {pending ? "Reviewing" : review ? "Review again" : "Review case"}
          </Button>
          {error ? <p className="mt-3 text-sm text-reject">{error}</p> : null}
          {stale && review ? (
            <p className="mt-3 text-sm text-muted">The file changed after this review. Run it again.</p>
          ) : null}

          {review ? (
            <div className="mt-5">
              <p className={cn("text-sm font-medium", RECOMMENDATION_TONE[review.recommendation])}>
                {RECOMMENDATION_LABEL[review.recommendation]}
              </p>
              <p className="font-serif text-display tabular-nums">{review.risk_score}</p>
              <p className="text-sm text-muted">out of 10</p>
              <p className="mt-3 text-sm text-muted">
                {review.engine === "claude"
                  ? "Claude called both lookups, then submitted this disposition."
                  : "Both lookups ran. A sanctions match is a rejection only when the date of birth agrees."}
              </p>

              <h3 className="mt-6 text-sm font-medium text-muted">Findings</h3>
              <ol className="mt-3 space-y-2">
                {review.findings.map((item, index) => {
                  const open = index === selectedFinding;
                  return (
                    <li key={item.summary} className={cn("rounded-lg", open && "bg-subtle")}>
                      <button
                        type="button"
                        className="press flex w-full flex-col items-start rounded-sm px-3 py-3 text-left"
                        aria-expanded={open}
                        onClick={() => setSelectedFinding(index)}
                      >
                        <span className="text-sm text-faint tabular-nums">
                          {String(index + 1).padStart(2, "0")} · {item.severity}
                        </span>
                        <span className="mt-1">{item.summary}</span>
                      </button>
                      {open ? (
                        <ul className="space-y-1 px-3 pb-3">
                          {item.citations.map((citation) => (
                            <li key={`${citation.source}-${citation.detail}`}>
                              <button
                                type="button"
                                className="press min-h-11 w-full rounded-sm px-2 text-left text-sm text-muted hover:bg-bg"
                                onClick={() => openCitation(citation.source)}
                              >
                                <span className="block text-faint">{citation.source}</span>
                                {citation.detail}
                              </button>
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </li>
                  );
                })}
              </ol>

              <h3 className="mt-6 text-sm font-medium text-muted">Tool log</h3>
              <ul className="mt-3 space-y-2">
                {review.tool_log.map((call, index) => (
                  <li
                    key={`${call.tool}-${index}`}
                    data-field={call.tool === "submit_review" ? undefined : call.tool}
                    className={cn(
                      "field-shell rounded-lg border border-line",
                      cites(finding, call.tool) && "bg-subtle",
                    )}
                  >
                    <details className="group">
                      <summary className="min-h-11 list-none px-3 py-3 [&::-webkit-details-marker]:hidden">
                        <span className="block font-medium">{toolLabel(call.tool)}</span>
                        <span className="mt-1 block text-sm text-muted">{summarizeCall(call)}</span>
                      </summary>
                      <pre className="px-3 pb-3 font-mono text-xs leading-relaxed break-all whitespace-pre-wrap text-muted">
                        {JSON.stringify({ inputs: call.inputs, outputs: call.outputs }, null, 2)}
                      </pre>
                    </details>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="mt-4 text-sm text-muted">
              Nothing is carried over from the last file. The review calls the sanctions list and the adverse-media list, then returns a score, findings, and a recommendation.
            </p>
          )}
        </section>
      </div>
    </main>
  );
}

function Field({
  label,
  field,
  value,
  onChange,
  highlighted,
  type = "text",
}: {
  label: string;
  field: string;
  value: string;
  onChange: (value: string) => void;
  highlighted: boolean;
  type?: "text" | "date";
}) {
  return (
    <label data-field={field} className={cn("field-shell block rounded-lg p-2", highlighted && "bg-subtle")}>
      <span className="mb-1 block text-sm text-muted">{label}</span>
      <Input type={type} value={value} onChange={(event) => onChange(event.target.value)} aria-label={label} />
    </label>
  );
}
