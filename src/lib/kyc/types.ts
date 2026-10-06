export type Transaction = {
  id: string;
  date: string;
  amount: number;
  currency: string;
  direction: "credit" | "debit";
  counterparty: string;
  country: string;
  channel: "card" | "cash" | "wire" | "crypto";
  description: string;
};

export type IdDocument = {
  document_type: string;
  document_number: string;
  issuing_country: string;
  expiry_date: string;
  name_on_document: string;
};

export type Application = {
  name: string;
  date_of_birth: string;
  address: string;
  id_document: IdDocument;
  transactions: Transaction[];
};

export type Citation = { source: string; detail: string };

export type Finding = {
  summary: string;
  severity: "low" | "medium" | "high";
  citations: Citation[];
};

export type Recommendation = "approve" | "escalate" | "reject";

export type ToolCall = {
  tool: string;
  inputs: {
    name?: string;
    date_of_birth?: string | null;
    address?: string | null;
  };
  outputs: {
    query?: {
      name?: string | null;
      date_of_birth?: string | null;
      address?: string | null;
    };
    hit_count?: number;
    matches?: {
      list_id?: string;
      listed_name?: string;
      matched_on?: string | null;
      match_type?: string;
      date_of_birth?: string | null;
      dob_status?: string;
      program?: string;
      list_address?: string | null;
      address_overlap?: boolean;
      remarks?: string | null;
    }[];
    articles?: {
      id?: string;
      headline?: string;
      source?: string;
      published?: string;
      severity?: string;
      category?: string;
      summary?: string;
      subject?: string;
      date_of_birth?: string | null;
      dob_status?: string;
      relevance?: string;
    }[];
    accepted?: boolean;
    error?: string;
  };
};

export type Review = {
  risk_score: number;
  recommendation: Recommendation;
  findings: Finding[];
  tool_log: ToolCall[];
  engine: "claude" | "policy";
};

export type CaseFile = {
  id: string;
  blurb: string;
  application: Application;
};
