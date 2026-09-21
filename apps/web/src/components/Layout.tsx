import { NavLink, Outlet } from "react-router-dom";

const NAV = [["/results", "Results"], ["/compare", "Compare"], ["/tasks", "Tasks"], ["/releases", "Releases"], ["/methodology", "Methodology"], ["/corrections", "Corrections"], ["/docs", "Run locally"]] as const;

export function Layout() {
  return <div className="app-shell"><a href="#main-content" className="skip-link">Skip to content</a>
    <header><NavLink to="/" className="brand" end>AI Engineer Bench</NavLink>
      <nav aria-label="Public benchmark navigation"><ul>{NAV.map(([to, label]) => <li key={to}><NavLink to={to}>{label}</NavLink></li>)}</ul></nav>
    </header><main id="main-content"><Outlet /></main>
    <footer><p>Read-only benchmark explorer. Metrics come from immutable published snapshots; development and self-reported results are never presented as official.</p></footer>
  </div>;
}
