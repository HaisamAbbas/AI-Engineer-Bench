import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./client";
import type { components } from "./schema";
import { ApiRequestError } from "./client";

export type PublicRun = components["schemas"]["PublicRunEvidence"];
export type PrivateRun = components["schemas"]["PrivateRunEvidence"];
export type RunEvidenceResult = { visibility: "public"; data: PublicRun } | { visibility: "private"; data: PrivateRun };

// TanStack Query keys include publication/cohort/filter values (spec 35), so
// two different cohorts/filters never collide in the cache. Read-only
// published-publication data can be cached indefinitely by digest; queries
// here use the default staleTime (0) since nothing mutable is polled yet -
// admin/mutation polling is Prompt 14's (ENG-018) scope, not this one's.

export function useReleases(cursor?: string) {
  return useQuery({
    queryKey: ["releases", cursor ?? null],
    queryFn: () => unwrap(api.GET("/v1/releases", { params: { query: { cursor } } })),
  });
}

export function usePublicationResults(publicationId: string | undefined) {
  return useQuery({
    queryKey: ["publication-results", publicationId],
    queryFn: () => unwrap(api.GET("/v1/publications/{publication_id}/results", { params: { path: { publication_id: publicationId! } } })),
    enabled: publicationId !== undefined,
    staleTime: Infinity, // immutable publication snapshot, addressed by ID
  });
}

export function useComparison(publicationId: string | undefined, entrantIds: string[], entrantPublicationIds?: string[]) {
  return useQuery({
    queryKey: ["comparison", publicationId, entrantIds, entrantPublicationIds ?? null],
    queryFn: () =>
      unwrap(
        api.GET("/v1/comparisons", {
          params: {
            query: {
              publication_id: publicationId,
              entrant_ids: entrantIds,
              entrant_publication_ids: entrantPublicationIds,
            },
          },
        }),
      ),
    enabled: entrantIds.length >= 2 && (publicationId !== undefined || entrantPublicationIds !== undefined),
  });
}

export function useTaskCatalog(category?: string, cursor?: string) {
  return useQuery({
    queryKey: ["tasks", category ?? null, cursor ?? null],
    queryFn: () => unwrap(api.GET("/v1/tasks", { params: { query: { category, cursor } } })),
  });
}

export function useTaskRevision(slug: string | undefined, version: string | undefined) {
  return useQuery({
    queryKey: ["task-revision", slug, version],
    queryFn: () =>
      unwrap(api.GET("/v1/tasks/{slug}/revisions/{version}", { params: { path: { slug: slug!, version: version! } } })),
    enabled: slug !== undefined && version !== undefined,
    staleTime: Infinity, // task revisions never mutate
  });
}

export function useEntrantRevisionBySlug(slug: string | undefined) {
  return useQuery({
    queryKey: ["entrant-revision-by-slug", slug],
    queryFn: () => unwrap(api.GET("/v1/entrants/by-slug/{slug}", { params: { path: { slug: slug! } } })),
    enabled: slug !== undefined,
    staleTime: Infinity,
  });
}

// The EXACT entrant configuration one publication's frozen campaign used for
// a slug - not useEntrantRevisionBySlug's "whichever revision is newest now"
// (review finding #2, second pass). Compare must use this, not the by-slug
// route, so a historical/cross-release panel never combines one
// publication's metrics with a different, newer entrant revision's config.
export function usePublicationEntrantConfiguration(publicationId: string | undefined, slug: string | undefined) {
  return useQuery({
    queryKey: ["publication-entrant-configuration", publicationId, slug],
    queryFn: () =>
      unwrap(
        api.GET("/v1/publications/{publication_id}/entrants/{slug}", {
          params: { path: { publication_id: publicationId!, slug: slug! } },
        }),
      ),
    enabled: publicationId !== undefined && slug !== undefined,
    staleTime: Infinity,
  });
}

export function useEntrantResults(slug: string | undefined) {
  return useQuery({
    queryKey: ["entrant-results", slug],
    queryFn: () => unwrap(api.GET("/v1/entrants/by-slug/{slug}/results", { params: { path: { slug: slug! } } })),
    enabled: slug !== undefined,
  });
}

export function useTrial(trialId: string | undefined) {
  return useQuery({
    queryKey: ["trial", trialId],
    queryFn: async (): Promise<RunEvidenceResult> => {
      try {
        const data = await unwrap(api.GET("/v1/trials/{trial_id}", { params: { path: { trial_id: trialId! } } }));
        return { visibility: "private", data };
      } catch (error) {
        if (!(error instanceof ApiRequestError) || (error.status !== 401 && error.status !== 403)) throw error;
        const data = await unwrap(api.GET("/v1/public/trials/{trial_id}", { params: { path: { trial_id: trialId! } } }));
        return { visibility: "public", data };
      }
    },
    enabled: trialId !== undefined,
    retry: false, // an unauthorized/missing trial should surface immediately, not retry-loop
  });
}

export function useMethodologyRevisions() {
  return useQuery({
    queryKey: ["methodology-revisions"],
    queryFn: () => unwrap(api.GET("/v1/methodology")),
    staleTime: Infinity,
  });
}

export function useMethodologyRevision(version: string | undefined) {
  return useQuery({
    queryKey: ["methodology-revision", version],
    queryFn: () => unwrap(api.GET("/v1/methodology/{version}", { params: { path: { version: version! } } })),
    enabled: version !== undefined,
    staleTime: Infinity,
  });
}

export function useCorrections(cursor?: string) {
  return useQuery({
    queryKey: ["corrections", cursor ?? null],
    queryFn: () => unwrap(api.GET("/v1/corrections", { params: { query: { cursor } } })),
  });
}

export * from "./publicationHooks";

// Admin writes never optimistically invent lifecycle state. Poll/refetch the
// authoritative response, including after conflicts from another operator.
export function useCampaign(id: string | undefined) {
  return useQuery({ queryKey: ["campaign", id], enabled: !!id, retry: false,
    queryFn: () => unwrap(api.GET("/v1/campaigns/{campaign_id}", { params: { path: { campaign_id: id! } } })),
    refetchInterval: 5000 });
}
export function useCampaignProgress(id: string | undefined) {
  return useQuery({ queryKey: ["campaign-progress", id], enabled: !!id, retry: false,
    queryFn: () => unwrap(api.GET("/v1/campaigns/{campaign_id}/progress", { params: { path: { campaign_id: id! } } })),
    refetchInterval: 5000 });
}
export function useInvalidAttempts(id: string | undefined) {
  return useQuery({ queryKey: ["invalid-attempts", id], enabled: !!id, retry: false,
    queryFn: () => unwrap(api.GET("/v1/campaigns/{campaign_id}/invalid-attempts", { params: { path: { campaign_id: id! } } })),
    refetchInterval: 5000 });
}
export type CampaignWrite =
  | { action: "create"; body: components["schemas"]["CampaignCreateRequest"] }
  | { action: "patch"; id: string; revision: number; body: components["schemas"]["CampaignPatchRequest"] }
  | { action: "freeze"; id: string; body: components["schemas"]["FreezeRegistry"] }
  | { action: "start" | "pause" | "resume" | "cancel"; id: string };
export function useCampaignWrite() {
  const client = useQueryClient();
  return useMutation({ retry: false,
    mutationFn: async (input: CampaignWrite) => {
      if (input.action === "create") return unwrap(api.POST("/v1/campaigns", { body: input.body }));
      const params = { path: { campaign_id: input.id } };
      if (input.action === "patch") return unwrap(api.PATCH("/v1/campaigns/{campaign_id}", { params, body: input.body, headers: { "If-Match": String(input.revision) } }));
      if (input.action === "freeze") return unwrap(api.POST("/v1/campaigns/{campaign_id}/freeze", { params, body: input.body }));
      const result = await unwrap(api.POST(`/v1/campaigns/{campaign_id}/${input.action}`, { params }));
      client.setQueryData(["campaign", input.id], result);
      return result.campaign;
    },
    onSettled: async () => {
      await Promise.all(["campaign", "campaign-progress", "invalid-attempts"].map(key => client.invalidateQueries({ queryKey: [key] })));
    },
  });
}
export function useMatrixPreview(id: string) {
  return useMutation({ retry: false, mutationFn: (body: components["schemas"]["FreezeRegistry"]) =>
    unwrap(api.POST("/v1/campaigns/{campaign_id}/preview", { params: { path: { campaign_id: id } }, body })) });
}
