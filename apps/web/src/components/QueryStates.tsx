import type { ReactNode } from "react";
import { ApiRequestError } from "../api/client";

/** Real loading/error/empty states everywhere data is fetched (spec: "real
 * loading, empty, error ... states", "no placeholder leaderboard scores").
 * Never render a skeleton table pre-filled with example scores. */

export function Loading({ label }: { label: string }) {
  return (
    <p role="status" aria-live="polite">
      Loading {label}…
    </p>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const requestId = error instanceof ApiRequestError ? error.requestId : undefined;
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="error-state">
      <p>{message}</p>
      {requestId && <p className="request-id">Request ID: {requestId}</p>}
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <p>
        <strong>{title}</strong>
      </p>
      {children}
    </div>
  );
}
