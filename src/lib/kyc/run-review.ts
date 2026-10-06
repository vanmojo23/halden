import { spawn } from "node:child_process";
import { parseApplication } from "./parse";
import { reviewWithPolicy } from "./policy";
import type { Application, Review } from "./types";

function isReview(value: unknown): value is Review {
  if (!value || typeof value !== "object") return false;
  const raw = value as Review;
  return (
    typeof raw.risk_score === "number" &&
    (raw.recommendation === "approve" || raw.recommendation === "escalate" || raw.recommendation === "reject") &&
    Array.isArray(raw.findings) &&
    Array.isArray(raw.tool_log)
  );
}

function runPython(application: Application): Promise<Review> {
  return new Promise((resolve, reject) => {
    const child = spawn("python3", ["-u", "-m", "kyc_agent", "--engine", "auto"], {
      cwd: process.cwd(),
      env: { ...process.env, PYTHONPATH: process.cwd() },
    });
    let out = "";
    let err = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("Review timed out"));
    }, 45_000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      out += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      err += chunk;
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        reject(new Error(err.trim() || `Review process exited ${code}`));
        return;
      }
      try {
        const parsed = JSON.parse(out) as unknown;
        if (!isReview(parsed)) {
          reject(new Error("Review process returned an unexpected payload"));
          return;
        }
        resolve(parsed);
      } catch (error) {
        reject(error instanceof Error ? error : new Error("Review process returned invalid JSON"));
      }
    });
    child.stdin.write(JSON.stringify(application));
    child.stdin.end();
  });
}

export async function runReview(input: unknown): Promise<Review> {
  const application = parseApplication(input);
  try {
    return await runPython(application);
  } catch (error) {
    console.error("Falling back to the in-process rule.", error);
    return reviewWithPolicy(application);
  }
}
