import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { TaskDetail } from "./TaskDetail";
import { renderWithProviders } from "../test/renderWithProviders";
import { mockApi } from "../test/mockApi";

const MALICIOUS_DESCRIPTION = '<img src=x onerror="window.__pwned = true">\x1b[31mred text\x1b[0m';

describe("TaskDetail", () => {
  it("renders requirement text as escaped text, never executes embedded HTML or ANSI (spec section 5)", async () => {
    mockApi({
      "/v1/tasks/{slug}/revisions/{version}": {
        data: {
          id: "task-id",
          ticket_text: "Updated documents can return stale content. Repair the ingestion path.",
          manifest: {
            id: "rag.document-freshness",
            version: "0.1.0",
            family_id: "knowledge-service-a",
            category: "rag",
            activity: "repair",
            source: { commit: "abc", license: "Apache-2.0" },
            environment: { official_image: "x@sha256:0", engineer_cpu: 1, engineer_memory_mb: 512, egress_policy: "none" },
            application: { dependency_mode: "fixture", entrypoint: ["python", "-m", "x"], model_profile_id: "p" },
            submission: { include: [], protected: [], max_artifact_bytes: 1 },
            requirements: [{ id: "req-1", severity: "mandatory", description: MALICIOUS_DESCRIPTION }],
            profile_compatibility: ["cpu-fixture-standard-v1"],
          },
        },
      },
    });
    (window as unknown as { __pwned?: boolean }).__pwned = false;
    renderWithProviders(<TaskDetail />, { route: "/tasks/rag.document-freshness/0.1.0", path: "/tasks/:slug/:version" });
    await waitFor(() => expect(screen.getByText(MALICIOUS_DESCRIPTION)).toBeInTheDocument());
    expect(screen.getByText("Updated documents can return stale content. Repair the ingestion path.")).toBeInTheDocument();
    // If this had been rendered as HTML instead of text, the onerror handler would have fired.
    expect((window as unknown as { __pwned?: boolean }).__pwned).toBe(false);
    expect(document.querySelector("img")).toBeNull();
  });
});
