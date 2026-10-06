import type { Application, Transaction } from "./types";

const CHANNELS = new Set(["card", "cash", "wire", "crypto"]);
const DIRECTIONS = new Set(["credit", "debit"]);

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function asString(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${label} is required`);
  return value.trim();
}

function asTransaction(value: unknown, index: number): Transaction {
  const raw = asRecord(value, `Transaction ${index + 1}`);
  const channel = asString(raw.channel, `Transaction ${index + 1} channel`);
  const direction = asString(raw.direction, `Transaction ${index + 1} direction`);
  if (!CHANNELS.has(channel) || !DIRECTIONS.has(direction)) {
    throw new Error(`Transaction ${index + 1} has an unknown channel or direction`);
  }
  const amount = raw.amount;
  if (typeof amount !== "number" || !Number.isFinite(amount) || amount < 0) {
    throw new Error(`Transaction ${index + 1} needs an amount`);
  }
  return {
    id: typeof raw.id === "string" && raw.id ? raw.id : `t${index + 1}`,
    date: asString(raw.date, `Transaction ${index + 1} date`),
    amount,
    currency: asString(raw.currency, `Transaction ${index + 1} currency`),
    direction: direction as Transaction["direction"],
    counterparty: asString(raw.counterparty, `Transaction ${index + 1} counterparty`),
    country: asString(raw.country, `Transaction ${index + 1} country`).toUpperCase(),
    channel: channel as Transaction["channel"],
    description: asString(raw.description, `Transaction ${index + 1} description`),
  };
}

export function parseApplication(input: unknown): Application {
  const raw = asRecord(input, "Application");
  const document = asRecord(raw.id_document, "Identity document");
  if (!Array.isArray(raw.transactions) || raw.transactions.length === 0) {
    throw new Error("Transaction history is required");
  }
  if (raw.transactions.length > 40) throw new Error("Transaction history is too long");
  return {
    name: asString(raw.name, "Name"),
    date_of_birth: asString(raw.date_of_birth, "Date of birth"),
    address: asString(raw.address, "Address"),
    id_document: {
      document_type: asString(document.document_type, "Document type"),
      document_number: asString(document.document_number, "Document number"),
      issuing_country: asString(document.issuing_country, "Issuing country"),
      expiry_date: asString(document.expiry_date, "Expiry date"),
      name_on_document: asString(document.name_on_document, "Name on document"),
    },
    transactions: raw.transactions.map((txn, index) => asTransaction(txn, index)),
  };
}
