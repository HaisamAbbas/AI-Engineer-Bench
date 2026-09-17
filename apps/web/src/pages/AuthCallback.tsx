import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { consumeReturnTo, exchangeCode } from "../api/oidc";

/** OIDC redirect landing: exchange the code, then return the user to where
 * sign-in began. Every failure mode renders honestly - a provider error
 * redirect, a missing code, or a failed token exchange never fakes success. */
export function AuthCallback() {
  const location = useLocation();
  const [error, setError] = useState<string | undefined>();
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const params = new URLSearchParams(location.search);
    const providerError = params.get("error");
    if (providerError) {
      setError(providerError);
      return;
    }
    const code = params.get("code");
    if (!code) {
      setError("token exchange failed: no authorization code in redirect");
      return;
    }
    exchangeCode(code, params.get("state"))
      .then(() => {
        window.location.replace(consumeReturnTo());
      })
      .catch((exc: unknown) => setError(exc instanceof Error ? exc.message : "token exchange failed"));
  }, [location]);
  if (error) {
    return (
      <section>
        <h1>Sign-in failed</h1>
        <p role="alert">{error}</p>
        <Link to="/">Back to home</Link>
      </section>
    );
  }
  return <p aria-live="polite">Completing sign-in…</p>;
}
