import type { components } from "./schema";

// Convenience aliases onto the generated schema types (review finding: "generated
// types are bypassed for the important result contracts") - pages import these
// instead of writing their own `interface` guesses or `as unknown as X` casts.
export type PublicationSummary = components["schemas"]["PublicationSummary"];
export type PublicationResultsResponse = components["schemas"]["PublicationResultsResponse"];
export type AnalysisSnapshot = components["schemas"]["AnalysisSnapshot"];
export type TaskCellStats = components["schemas"]["TaskCellStats"];
export type ComparisonResponse = components["schemas"]["ComparisonResponse"];
export type TaskPairedDifference = components["schemas"]["TaskPairedDifference"];
export type TaskCatalogEntry = components["schemas"]["TaskCatalogEntry"];
export type CorrectionEntry = components["schemas"]["CorrectionEntry"];
export type TaskRevisionResponse = components["schemas"]["TaskRevisionResponse"];
export type EntrantRevisionResponse = components["schemas"]["EntrantRevisionResponse"];
