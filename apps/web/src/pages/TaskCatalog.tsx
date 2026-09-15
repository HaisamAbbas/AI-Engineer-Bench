import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useTaskCatalog } from "../api/hooks";
import { Loading, ErrorState, EmptyState } from "../components/QueryStates";

/** "/tasks" - category/activity/project-family filters, empty search state,
 * "next_cursor" pagination (review finding: list endpoints exposed no
 * pagination controls at all). There is no retired/deprecated status
 * tracked yet (see registry.py's own docstring); this page does not
 * fabricate that column. */
export function TaskCatalog() {
  const [params, setParams] = useSearchParams();
  const category = params.get("category") ?? undefined;
  const cursor = params.get("cursor") ?? undefined;
  const [search, setSearch] = useState("");
  const tasks = useTaskCatalog(category, cursor);

  if (tasks.isPending) return <Loading label="task catalog" />;
  if (tasks.isError) return <ErrorState error={tasks.error} onRetry={() => tasks.refetch()} />;

  const items = tasks.data.items;
  const filtered = search ? items.filter((t) => t.slug.includes(search) || t.family_id.includes(search)) : items;

  function setCategory(next: string) {
    const nextParams: Record<string, string> = {};
    if (next) nextParams.category = next;
    setParams(nextParams);
  }

  function goToNextPage() {
    if (!tasks.data?.next_cursor) return;
    const nextParams: Record<string, string> = { cursor: tasks.data.next_cursor };
    if (category) nextParams.category = category;
    setParams(nextParams);
  }

  return (
    <section>
      <h1>Tasks</h1>
      <label htmlFor="category-filter">Category</label>
      <select id="category-filter" value={category ?? ""} onChange={(event) => setCategory(event.target.value)}>
        <option value="">All categories</option>
        <option value="rag">rag</option>
        <option value="extraction">extraction</option>
        <option value="tool_app">tool_app</option>
      </select>
      <label htmlFor="task-search">Search</label>
      <input
        id="task-search"
        type="search"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder="Filter by slug or project family"
        aria-describedby="task-search-hint"
      />
      <p id="task-search-hint">Filters the current page only; it does not search across other pages.</p>
      {items.length === 0 && <EmptyState title="No tasks are catalogued yet." />}
      {items.length > 0 && filtered.length === 0 && <EmptyState title="No tasks match this search." />}
      {filtered.length > 0 && (
        <table>
          <caption>Public task catalog</caption>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Version</th>
              <th scope="col">Category</th>
              <th scope="col">Activity</th>
              <th scope="col">Project family</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((task) => (
              <tr key={task.id}>
                <th scope="row">
                  <Link to={`/tasks/${task.slug}/${task.version}`}>{task.slug}</Link>
                </th>
                <td>{task.version}</td>
                <td>{task.category}</td>
                <td>{task.activity ?? "Unknown"}</td>
                <td>{task.family_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tasks.data.next_cursor && (
        <button type="button" onClick={goToNextPage}>
          Next page
        </button>
      )}
    </section>
  );
}
