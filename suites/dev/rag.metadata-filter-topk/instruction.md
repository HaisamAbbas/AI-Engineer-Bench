# RAG-02: Apply metadata filters before ranking

Repair the search service. Metadata filters must restrict the candidate set before `top_k` is applied; relevant matching records below the unfiltered cutoff must remain discoverable.
