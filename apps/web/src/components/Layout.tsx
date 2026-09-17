import { NavLink, Outlet } from "react-router-dom";

// Admin access is enforced by the API; navigation does not imply authorization.
const PRIMARY_NAV = [
  { to: "/results", label: "Results" },
  { to: "/compare", label: "Compare" },
  { to: "/tasks", label: "Tasks" },
  { to: "/methodology", label: "Methodology" },
  { to: "/docs", label: "Run locally" },
];

const SECONDARY_NAV = [
  { to: "/releases", label: "Releases" },
  { to: "/corrections", label: "Corrections" },
  { to: "/admin/campaigns", label: "Campaign admin" },
];

export function Layout() {
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <header>
        <NavLink to="/" className="brand" end>
          AI Engineer Bench
        </NavLink>
        <nav aria-label="Primary">
          <ul>
            {PRIMARY_NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to}>{item.label}</NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <nav aria-label="Secondary">
          <ul>
            {SECONDARY_NAV.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to}>{item.label}</NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main id="main-content">
        <Outlet />
      </main>
      <footer>
        <p>
          Development-phase evidence platform. No official campaign has been published yet - see{" "}
          <NavLink to="/methodology">Methodology</NavLink> for evaluated scope before drawing conclusions.
        </p>
      </footer>
    </div>
  );
}
