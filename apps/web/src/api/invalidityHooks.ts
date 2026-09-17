import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { replayableWrite } from "./mutationReplay";
import type { components } from "./schema";

export type InvalidityReviewInput = components["schemas"]["InvalidityReviewRequest"];

export function useInvalidAttemptEvidence(campaignId: string, attemptId: string) {
  return useQuery({
    queryKey: ["invalid-attempt-evidence", campaignId, attemptId],
    queryFn: () => unwrap(api.GET("/v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}", {
      params: { path: { campaign_id: campaignId, attempt_id: attemptId } },
    })),
    retry: false,
  });
}

export function useReviewInvalidAttempt(campaignId: string, attemptId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ body }: { body: InvalidityReviewInput }) =>
      replayableWrite(`invalidity:${campaignId}:${attemptId}`, body, headers =>
        unwrap(api.POST("/v1/campaigns/{campaign_id}/invalid-attempts/{attempt_id}/reviews", {
          params: { path: { campaign_id: campaignId, attempt_id: attemptId } },
          headers, body,
        }))),
    retry: false,
    onSuccess: () => client.invalidateQueries({ queryKey: ["invalid-attempt-evidence", campaignId, attemptId] }),
  });
}
