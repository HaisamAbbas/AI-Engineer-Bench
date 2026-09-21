import { useQuery } from "@tanstack/react-query";
import { api, unwrap, ApiRequestError } from "./client";
import type { components } from "./schema";

export type PublicRun = components["schemas"]["PublicRunEvidence"];
export type PrivateRun = components["schemas"]["PrivateRunEvidence"];
export type RunEvidenceResult = { visibility: "public"; data: PublicRun } | { visibility: "private"; data: PrivateRun };

export function useReleases(cursor?: string) { return useQuery({ queryKey: ["releases", cursor ?? null], queryFn: () => unwrap(api.GET("/v1/releases", { params: { query: { cursor } } })) }); }
export function usePublicationResults(id: string | undefined) { return useQuery({ queryKey: ["publication-results", id], enabled: !!id, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/publications/{publication_id}/results", { params: { path: { publication_id: id! } } })) }); }
export function useComparison(publicationId: string | undefined, entrantIds: string[], entrantPublicationIds?: string[]) { return useQuery({ queryKey: ["comparison", publicationId, entrantIds, entrantPublicationIds], enabled: entrantIds.length >= 2, queryFn: () => unwrap(api.GET("/v1/comparisons", { params: { query: { publication_id: publicationId, entrant_ids: entrantIds, entrant_publication_ids: entrantPublicationIds } } })) }); }
export function useTaskCatalog(category?: string, cursor?: string) { return useQuery({ queryKey: ["tasks", category, cursor], queryFn: () => unwrap(api.GET("/v1/tasks", { params: { query: { category, cursor } } })) }); }
export function useTaskRevision(slug: string | undefined, version: string | undefined) { return useQuery({ queryKey: ["task-revision", slug, version], enabled: !!slug && !!version, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/tasks/{slug}/revisions/{version}", { params: { path: { slug: slug!, version: version! } } })) }); }
export function useEntrantRevisionBySlug(slug: string | undefined) { return useQuery({ queryKey: ["entrant", slug], enabled: !!slug, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/entrants/by-slug/{slug}", { params: { path: { slug: slug! } } })) }); }
export function usePublicationEntrantConfiguration(publicationId: string | undefined, slug: string | undefined) { return useQuery({ queryKey: ["publication-entrant", publicationId, slug], enabled: !!publicationId && !!slug, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/publications/{publication_id}/entrants/{slug}", { params: { path: { publication_id: publicationId!, slug: slug! } } })) }); }
export function useEntrantResults(slug: string | undefined) { return useQuery({ queryKey: ["entrant-results", slug], enabled: !!slug, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/entrants/by-slug/{slug}/results", { params: { path: { slug: slug! } } })) }); }
export function useTrial(trialId: string | undefined) {
  return useQuery({
    queryKey: ["trial", trialId], enabled: !!trialId, retry: false,
    queryFn: async (): Promise<RunEvidenceResult> => {
      try {
        const data = await unwrap(api.GET("/v1/trials/{trial_id}", { params: { path: { trial_id: trialId! } } }));
        return { visibility: "private", data };
      } catch (error) {
        if (!(error instanceof ApiRequestError) || ![401, 403].includes(error.status)) throw error;
        const data = await unwrap(api.GET("/v1/public/trials/{trial_id}", { params: { path: { trial_id: trialId! } } }));
        return { visibility: "public", data };
      }
    },
  });
}
export function useMethodologyRevisions() { return useQuery({ queryKey: ["methodology-revisions"], staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/methodology")) }); }
export function useMethodologyRevision(version: string | undefined) { return useQuery({ queryKey: ["methodology", version], enabled: !!version, staleTime: Infinity, queryFn: () => unwrap(api.GET("/v1/methodology/{version}", { params: { path: { version: version! } } })) }); }
export function useCorrections(cursor?: string) { return useQuery({ queryKey: ["corrections", cursor ?? null], queryFn: () => unwrap(api.GET("/v1/corrections", { params: { query: { cursor } } })) }); }
