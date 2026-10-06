import { createServerFn } from "@tanstack/react-start";
import { parseApplication } from "./parse";

export const reviewApplication = createServerFn({ method: "POST" })
  .validator((input: unknown) => parseApplication(input))
  .handler(async ({ data }) => {
    const { runReview } = await import("./run-review");
    return runReview(data);
  });
