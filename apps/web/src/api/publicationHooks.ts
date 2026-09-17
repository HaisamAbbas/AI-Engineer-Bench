import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import { replayableWrite } from "./mutationReplay";
import type { components } from "./schema";

export type PublicationPreparation = components["schemas"]["PublicationPreparationSummary"];
export type PublicationPrepareInput = components["schemas"]["PublicationPrepareRequest"];
export type PublicationReviewInput = components["schemas"]["PublicationReviewRequest"];
export type PublicationRegradeInput = components["schemas"]["RegradeRequest"];
export type PublicationExportData = components["schemas"]["PublicationExport"];

function useInvalidatePublicationWrites() {
  const client = useQueryClient();
  return () => client.invalidateQueries({
    predicate: ({ queryKey }) => {
      const key = String(queryKey[0]);
      return key.startsWith("publication") || key.startsWith("correction") ||
        key.startsWith("campaign") || ["releases", "comparison", "entrant-results", "trial"].includes(key);
    },
  });
}

export function usePreparePublication(campaignId: string) {
  const invalidate = useInvalidatePublicationWrites();
  return useMutation({
    mutationFn: (body: PublicationPrepareInput) => replayableWrite(`prepare:${campaignId}`, body, headers => unwrap(api.POST("/v1/campaigns/{campaign_id}/publications/prepare", {
      params: { path: { campaign_id: campaignId } }, body, headers,
    }))),
    retry: false,
    onSuccess: invalidate,
  });
}

export function useReviewPublication() {
  const invalidate = useInvalidatePublicationWrites();
  return useMutation({
    mutationFn: ({ preparationId, body }: { preparationId: string; body: PublicationReviewInput }) =>
      replayableWrite(`review:${preparationId}`, body, headers => unwrap(api.POST("/v1/publications/preparations/{preparation_id}/review", {
        params: { path: { preparation_id: preparationId } }, body, headers,
      }))),
    retry: false,
    onSuccess: invalidate,
  });
}

export function useWithdrawPublication() {
  const invalidate = useInvalidatePublicationWrites();
  return useMutation({
    mutationFn: ({ publicationId, reason }: { publicationId: string; reason: string }) =>
      replayableWrite(`withdraw:${publicationId}`, { reason }, headers => unwrap(api.POST("/v1/publications/{publication_id}/withdraw", {
        params: { path: { publication_id: publicationId } }, body: { reason }, headers,
      }))),
    retry: false,
    onSuccess: invalidate,
  });
}

export function useRegradePublication(campaignId: string) {
  const invalidate = useInvalidatePublicationWrites();
  return useMutation({
    mutationFn: (body: PublicationRegradeInput) => replayableWrite(`regrade:${campaignId}`, body, headers => unwrap(api.POST("/v1/campaigns/{campaign_id}/regrade", {
      params: { path: { campaign_id: campaignId } }, body, headers,
    }))),
    retry: false,
    onSuccess: invalidate,
  });
}

export function usePublicationScoringBundle(campaignId: string | undefined) {
  return useQuery({
    queryKey: ["publication-scoring-bundle", campaignId],
    queryFn: () => unwrap(api.GET("/v1/campaigns/{campaign_id}/scoring-bundle", {
      params: { path: { campaign_id: campaignId! } },
    })),
    enabled: Boolean(campaignId),
    retry: false,
  });
}

export function usePublicationCorrectionRun(runId: string | undefined) {
  return useQuery({
    queryKey: ["correction-run", runId],
    queryFn: () => unwrap(api.GET("/v1/correction-runs/{run_id}", {
      params: { path: { run_id: runId! } },
    })),
    enabled: Boolean(runId),
    retry: false,
    refetchInterval: (query) => query.state.status === "error" ? false :
      query.state.data?.status === "running" ? 2000 : false,
  });
}

export function usePublicationPreparation(preparationId: string | undefined) {
  return useQuery({
    queryKey: ["publication-preparation", preparationId],
    queryFn: () => unwrap(api.GET("/v1/publications/preparations/{preparation_id}", {
      params: { path: { preparation_id: preparationId! } },
    })),
    enabled: Boolean(preparationId),
    retry: false,
  });
}

export function usePublicationExport(publicationId: string | undefined) {
  return useQuery({
    queryKey: ["publication-export", publicationId],
    queryFn: () => unwrap(api.GET("/v1/publications/{publication_id}/export", {
      params: { path: { publication_id: publicationId! } },
    })),
    enabled: Boolean(publicationId),
    retry: false,
  });
}
