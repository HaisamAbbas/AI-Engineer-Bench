import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { accessToken, beginSignIn, clearSession, configuredOidc } from "../api/oidc";

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
  const location = useLocation();
  const oidc = configuredOidc();
  const signedIn = accessToken() !== undefined;
  const [authError, setAuthError] = useState<string>();
  const [signingIn, setSigningIn] = useState(false);
  async function signIn() {
    setAuthError(undefined);
    setSigningIn(true);
    try {
      await beginSignIn(location.pathname + location.search);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Sign-in failed");
    } finally {
      setSigningIn(false);
    }
  }
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <header>
        <NavLink to="/" className="brand" end>
          AI Engineer Bench
        </NavLink>
        {oidc !== undefined &&
          (signedIn ? (
            <button type="button" onClick={() => { clearSession(); window.location.reload(); }}>
              Sign out
            </button>
          ) : (
            <button type="button" disabled={signingIn} onClick={() => void signIn()}>
              Sign in
            </button>
          ))}
        {authError && <p role="alert">{authError}</p>}
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
