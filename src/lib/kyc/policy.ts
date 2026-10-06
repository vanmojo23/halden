/**
 * Same written rule as kyc_agent/policy.py, for when the Python runner
 * is not available. Reject only a sanctions match whose date of birth agrees.
 */
import mediaList from "../../../kyc_agent/data/adverse_media.json" with { type: "json" };
import sanctionsList from "../../../kyc_agent/data/sanctions.json" with { type: "json" };
import type { Application, Finding, Review, ToolCall } from "./types";

const HIGH_RISK = new Set(["IR", "KP", "SY", "CU", "RU", "BY", "MM", "VE", "YE", "AF", "SS"]);

const FLOORS = {
  sanctions_exact: 10,
  sanctions_alias: 9,
  sanctions_unconfirmed: 7,
  sanctions_dob_mismatch: 6,
  media_high: 8,
  media_medium: 5,
  media_low: 2,
  structuring: 7,
  high_risk_country: 8,
  expired_id: 5,
  name_mismatch: 6,
} as const;

const MATCH_RANK: Record<string, number> = { exact_name: 3, alias: 2, partial_name: 1 };

type SanctionEntry = (typeof sanctionsList)[number];
type MediaEntry = (typeof mediaList)[number];

export function normalizeName(value: string | null | undefined): string {
  const text = (value ?? "").normalize("NFKD").replace(/\p{M}/gu, "");
  return text
    .toLowerCase()
    .replaceAll("&", " and ")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function compareDob(applicant: string | null | undefined, listed: string | null | undefined): string {
  const left = (applicant ?? "").trim();
  const right = (listed ?? "").trim();
  if (!left && !right) return "unknown";
  if (!left) return "not_provided";
  if (!right) return "not_on_list";
  return left === right ? "match" : "mismatch";
}

function partialKind(query: string, listed: string): string | null {
  const qTokens = query.split(" ");
  const lTokens = listed.split(" ");
  if (qTokens.length < 2 || lTokens.length < 2) return null;
  if (qTokens[qTokens.length - 1]!.length < 4 || qTokens[qTokens.length - 1] !== lTokens[lTokens.length - 1]) {
    return null;
  }
  const qFirst = qTokens[0]!;
  const lFirst = lTokens[0]!;
  if (qFirst === lFirst) return "partial_name";
  const sameInitial = qFirst.slice(0, 1) === lFirst.slice(0, 1);
  const initial = qFirst.length === 1 || lFirst.length === 1;
  const prefix = qFirst.length >= 3 && lFirst.length >= 3 && qFirst.slice(0, 3) === lFirst.slice(0, 3);
  if (sameInitial && (initial || prefix)) return "partial_name";
  return null;
}

function classify(query: string, listed: string, primary: boolean): string | null {
  if (query && query === listed) return primary ? "exact_name" : "alias";
  return partialKind(query, listed);
}

function addressOverlap(queryAddress: string | null | undefined, listedAddress: string | null | undefined): boolean {
  const query = normalizeName(queryAddress);
  const listed = normalizeName(listedAddress);
  if (!query || !listed) return false;
  const queryTokens = new Set(query.split(" "));
  return listed.split(" ").some((token) => token.length >= 4 && queryTokens.has(token));
}

function sanctionsLookup(name: string, dateOfBirth: string | null, address: string | null) {
  const queryName = normalizeName(name);
  const matches = [];
  if (queryName) {
    for (const entry of sanctionsList as SanctionEntry[]) {
      const names = [entry.name, ...(entry.aliases ?? [])];
      let bestType: string | null = null;
      let bestOn: string | null = null;
      names.forEach((listed, index) => {
        const kind = classify(queryName, normalizeName(listed), index === 0);
        if (kind && (MATCH_RANK[kind] ?? 0) > (MATCH_RANK[bestType ?? ""] ?? 0)) {
          bestType = kind;
          bestOn = listed;
        }
      });
      if (!bestType) continue;
      matches.push({
        list_id: entry.list_id,
        listed_name: entry.name,
        matched_on: bestOn,
        match_type: bestType,
        date_of_birth: entry.date_of_birth,
        dob_status: compareDob(dateOfBirth, entry.date_of_birth),
        program: entry.program,
        list_address: entry.address,
        address_overlap: addressOverlap(address, entry.address),
        remarks: entry.remarks,
      });
    }
  }
  return {
    query: { name, date_of_birth: dateOfBirth, address },
    hit_count: matches.length,
    matches,
  };
}

function adverseMediaLookup(name: string, dateOfBirth: string | null) {
  const queryName = normalizeName(name);
  const articles = [];
  if (queryName) {
    for (const article of mediaList as MediaEntry[]) {
      const names = [article.subject, ...(article.aliases ?? [])];
      if (!names.some((item) => normalizeName(item) === queryName)) continue;
      const dobStatus = compareDob(dateOfBirth, article.date_of_birth);
      articles.push({
        id: article.id,
        headline: article.headline,
        source: article.source,
        published: article.published,
        severity: article.severity,
        category: article.category,
        summary: article.summary,
        subject: article.subject,
        date_of_birth: article.date_of_birth,
        dob_status: dobStatus,
        relevance: dobStatus === "mismatch" ? "name_only_dob_conflict" : "subject_match",
      });
    }
  }
  return {
    query: { name, date_of_birth: dateOfBirth },
    hit_count: articles.length,
    articles,
  };
}

function money(amount: number, currency: string): string {
  return `${amount.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency}`;
}

function finding(summary: string, severity: Finding["severity"], citations: Finding["citations"]): Finding {
  return { summary, severity, citations };
}

function daysBetween(start: string, end: string): number {
  const ms = Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`);
  return Math.round(ms / 86_400_000);
}

function structuringIndexes(transactions: Application["transactions"]): number[] {
  const cash: { index: number; date: string }[] = [];
  transactions.forEach((txn, index) => {
    if (txn.channel === "cash" && txn.direction === "credit" && txn.amount >= 9000 && txn.amount < 10000) {
      cash.push({ index, date: txn.date });
    }
  });
  cash.sort((a, b) => a.date.localeCompare(b.date));
  const flagged = new Set<number>();
  for (let start = 0; start < cash.length; start += 1) {
    const cluster = [cash[start]!.index];
    for (let cursor = start + 1; cursor < cash.length; cursor += 1) {
      if (daysBetween(cash[start]!.date, cash[cursor]!.date) <= 14) cluster.push(cash[cursor]!.index);
      else break;
    }
    if (cluster.length >= 2) cluster.forEach((index) => flagged.add(index));
  }
  return [...flagged].sort((a, b) => a - b);
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export function reviewWithPolicy(application: Application, asOf: string = todayIso()): Review {
  const log: ToolCall[] = [];
  const sanctionInputs = {
    name: application.name,
    date_of_birth: application.date_of_birth,
    address: application.address,
  };
  const sanctions = sanctionsLookup(application.name, application.date_of_birth, application.address);
  log.push({ tool: "sanctions_lookup", inputs: sanctionInputs, outputs: sanctions });

  const mediaInputs = {
    name: application.name,
    date_of_birth: application.date_of_birth,
  };
  const media = adverseMediaLookup(application.name, application.date_of_birth);
  log.push({ tool: "adverse_media_lookup", inputs: mediaInputs, outputs: media });

  const findings: Finding[] = [];
  const floors: number[] = [];
  let blocking = 0;
  let reject = false;

  sanctions.matches.forEach((match, index) => {
    const cite = [
      { source: "application.name", detail: application.name },
      { source: "application.date_of_birth", detail: application.date_of_birth },
      {
        source: `sanctions_lookup.matches[${index}]`,
        detail: `${match.list_id} · ${match.match_type} · listed ${match.listed_name} · dob ${match.dob_status} · ${match.program}`,
      },
    ];
    const confirmed = (match.match_type === "exact_name" || match.match_type === "alias") && match.dob_status === "match";
    if (confirmed) {
      reject = true;
      blocking += 1;
      floors.push(match.match_type === "exact_name" ? FLOORS.sanctions_exact : FLOORS.sanctions_alias);
      findings.push(
        finding(
          `${application.name} and date of birth ${application.date_of_birth} match ${match.list_id} (${match.listed_name}) on ${match.program}.`,
          "high",
          cite,
        ),
      );
    } else if (match.dob_status === "mismatch") {
      blocking += 1;
      floors.push(FLOORS.sanctions_dob_mismatch);
      findings.push(
        finding(
          `The name matches ${match.matched_on} on ${match.list_id} (${match.listed_name}), but the dates of birth do not (${application.date_of_birth} vs ${match.date_of_birth}). This may be a different person.`,
          "medium",
          cite,
        ),
      );
    } else {
      blocking += 1;
      floors.push(FLOORS.sanctions_unconfirmed);
      findings.push(
        finding(
          `The name matches ${match.list_id} (${match.listed_name}) as ${String(match.match_type).replaceAll("_", " ")}, and the date of birth does not clear it (${match.dob_status}).`,
          "high",
          cite,
        ),
      );
    }
  });

  for (const article of media.articles) {
    const cite = [
      { source: "application.name", detail: application.name },
      { source: "application.date_of_birth", detail: application.date_of_birth },
      {
        source: `adverse_media.${article.id}`,
        detail: `${article.headline} · ${article.source} · ${article.published} · dob ${article.dob_status}`,
      },
    ];
    if (article.relevance === "name_only_dob_conflict") {
      floors.push(FLOORS.media_low);
      findings.push(
        finding(
          `Adverse media names ${article.subject} (${article.headline}, ${article.source}, ${article.published}), but the article date of birth ${article.date_of_birth} does not match the applicant. Not treated as the same person.`,
          "low",
          cite,
        ),
      );
      continue;
    }
    blocking += 1;
    let severity: Finding["severity"] = "low";
    if (article.severity === "high") {
      floors.push(FLOORS.media_high);
      severity = "high";
    } else if (article.severity === "medium") {
      floors.push(FLOORS.media_medium);
      severity = "medium";
    } else {
      floors.push(FLOORS.media_low);
    }
    findings.push(
      finding(
        `Adverse media: ${article.headline} (${article.source}, ${article.published}). ${article.summary}`,
        severity,
        cite,
      ),
    );
  }

  const risky = application.transactions
    .map((txn, index) => ({ txn, index }))
    .filter(({ txn }) => HIGH_RISK.has(txn.country.toUpperCase()));
  if (risky.length) {
    blocking += 1;
    floors.push(FLOORS.high_risk_country);
    const listed = risky
      .map(({ txn }) => `${txn.date} ${money(txn.amount, txn.currency)} to ${txn.counterparty} (${txn.country})`)
      .join("; ");
    findings.push(
      finding(
        `Transaction history includes a high-risk jurisdiction: ${listed}.`,
        "high",
        risky.map(({ txn, index }) => ({
          source: `application.transactions[${index}]`,
          detail: `${txn.date} · ${txn.direction} · ${money(txn.amount, txn.currency)} · ${txn.counterparty} · ${txn.country} · ${txn.channel}`,
        })),
      ),
    );
  }

  const cluster = structuringIndexes(application.transactions);
  if (cluster.length) {
    blocking += 1;
    floors.push(FLOORS.structuring);
    const deposits = cluster.map((index) => application.transactions[index]!);
    findings.push(
      finding(
        `${cluster.length} cash deposits between 9,000 and 10,000 (${deposits.map((txn) => money(txn.amount, txn.currency)).join(", ")}) posted from ${deposits[0]!.date} to ${deposits[deposits.length - 1]!.date}, each just under the 10,000 reporting threshold.`,
        "high",
        cluster.map((index) => {
          const txn = application.transactions[index]!;
          return {
            source: `application.transactions[${index}]`,
            detail: `${txn.date} · cash credit · ${money(txn.amount, txn.currency)} · ${txn.counterparty}`,
          };
        }),
      ),
    );
  }

  const document = application.id_document;
  const expiry = document.expiry_date;
  const expired = !/^\d{4}-\d{2}-\d{2}$/.test(expiry) || expiry < asOf;
  if (expired) {
    blocking += 1;
    floors.push(FLOORS.expired_id);
    findings.push(
      finding(
        `The ${document.document_type || "identity document"} ${document.document_number} expired on ${expiry || "an unknown date"}.`,
        "medium",
        [
          { source: "application.id_document.expiry_date", detail: expiry || "missing" },
          { source: "application.id_document.document_number", detail: document.document_number },
        ],
      ),
    );
  }

  if (normalizeName(application.name) !== normalizeName(document.name_on_document)) {
    blocking += 1;
    floors.push(FLOORS.name_mismatch);
    findings.push(
      finding(
        `The name on the identity document is ${document.name_on_document || "blank"}, which does not match the application name ${application.name || "blank"}.`,
        "medium",
        [
          { source: "application.name", detail: application.name },
          { source: "application.id_document.name_on_document", detail: document.name_on_document },
        ],
      ),
    );
  }

  if (!findings.length) {
    return {
      risk_score: 1,
      recommendation: "approve",
      findings: [
        finding(
          "No sanctions match and no adverse-media match. The name on the identity document matches the application, and the document is unexpired.",
          "low",
          [
            { source: "sanctions_lookup", detail: "hit_count 0" },
            { source: "adverse_media_lookup", detail: "hit_count 0" },
            { source: "application.name", detail: application.name },
            { source: "application.id_document.name_on_document", detail: document.name_on_document },
            { source: "application.id_document.expiry_date", detail: expiry },
          ],
        ),
      ],
      tool_log: log,
      engine: "policy",
    };
  }

  let score = Math.min(10, Math.max(...floors) + Math.max(0, blocking - 1));
  let recommendation: Review["recommendation"];
  if (reject) recommendation = "reject";
  else if (blocking) recommendation = "escalate";
  else {
    recommendation = "approve";
    score = Math.min(score, 3);
  }

  return {
    risk_score: score,
    recommendation,
    findings,
    tool_log: log,
    engine: "policy",
  };
}
