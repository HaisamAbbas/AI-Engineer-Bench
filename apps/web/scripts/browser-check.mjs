import { spawn, execFileSync } from "node:child_process";
import { createServer } from "node:net";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixturePath = path.resolve(projectRoot, "../../.cache/eng016-browser/browser-fixture.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf8"));
const webBase = process.env.AIEB_WEB_URL ?? "http://127.0.0.1:5173";
const artifactDir = path.resolve(projectRoot, "../../.cache/eng016-browser");
const axeSource = readFileSync(path.join(projectRoot, "node_modules/axe-core/axe.min.js"), "utf8");

function browserExecutable() {
  if (process.env.AIEB_BROWSER_EXECUTABLE) return process.env.AIEB_BROWSER_EXECUTABLE;
  const candidates = process.platform === "win32"
    ? [
        path.join(process.env["ProgramFiles(x86)"] ?? "C:/Program Files (x86)", "Microsoft/Edge/Application/msedge.exe"),
        path.join(process.env.ProgramFiles ?? "C:/Program Files", "Microsoft/Edge/Application/msedge.exe"),
      ]
    : ["google-chrome", "chromium", "chromium-browser"];
  if (process.platform === "win32") return candidates.find((candidate) => existsSync(candidate));
  for (const candidate of candidates) {
    try { return execFileSync("which", [candidate], { encoding: "utf8" }).trim(); }
    catch { /* try the next installed browser name */ }
  }
  return undefined;
}

async function freePort() {
  const server = createServer();
  await new Promise((resolve, reject) => server.once("error", reject).listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

async function waitForJson(url, timeoutMs = 15000) {
  const until = Date.now() + timeoutMs;
  while (Date.now() < until) {
    try {
      const response = await fetch(url);
      if (response.ok) return response.json();
    } catch { /* browser is still starting */ }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function connectCdp(url) {
  const ws = new WebSocket(url);
  const pending = new Map();
  let nextId = 0;
  ws.addEventListener("message", (event) => {
    const message = JSON.parse(String(event.data));
    if (message.id && pending.has(message.id)) {
      const { resolve, reject, timer } = pending.get(message.id);
      clearTimeout(timer);
      pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else resolve(message.result);
    }
  });
  const opened = new Promise((resolve, reject) => {
    ws.addEventListener("open", resolve, { once: true });
    ws.addEventListener("error", reject, { once: true });
  });
  return {
    ws,
    opened,
    send(method, params = {}) {
      const id = ++nextId;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP ${method} timed out`)); }, 15000);
        pending.set(id, { resolve, reject, timer });
        ws.send(JSON.stringify({ id, method, params }));
      });
    },
  };
}

async function evaluate(cdp, expression, { awaitPromise = false, returnByValue = true } = {}) {
  const response = await cdp.send("Runtime.evaluate", {
    expression, awaitPromise, returnByValue, userGesture: true,
  });
  if (response.exceptionDetails) {
    throw new Error(response.exceptionDetails.exception?.description ?? response.exceptionDetails.text);
  }
  return response.result?.value;
}

const routes = [
  { path: "/", heading: "AI Engineer Bench" },
  { path: `/results?publication=${fixture.publication_id}`, heading: "Results" },
  { path: `/compare?publication=${fixture.publication_id}`, heading: "Compare" },
  { path: `/entrants/${fixture.entrant_id}`, heading: fixture.entrant_id },
  { path: "/tasks", heading: "Tasks" },
  { path: `/tasks/${fixture.task_id}/${fixture.task_version}`, heading: `${fixture.task_id} v${fixture.task_version}` },
  { path: `/runs/${fixture.trial_id}`, heading: `Run ${fixture.trial_id.slice(0, 8)}` },
  { path: `/methodology/${fixture.protocol_id}`, heading: "Methodology" },
  { path: "/releases", heading: "Releases" },
  { path: `/releases/${fixture.publication_id}`, heading: `Release ${fixture.publication_id}` },
  { path: "/corrections", heading: "Corrections" },
  { path: "/docs", heading: "Run locally" },
  { path: "/route-not-found", heading: "Page not found" },
];

const executable = browserExecutable();
if (!executable) throw new Error("Set AIEB_BROWSER_EXECUTABLE to a local Chrome, Chromium, or Edge executable.");
const port = await freePort();
const profile = mkdtempSync(path.join(tmpdir(), "aieb-eng016-browser-"));
const browser = spawn(executable, [
  "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
  "--disable-background-networking", "--remote-allow-origins=*", `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, "--window-size=1440,1000", "about:blank",
], { stdio: "ignore", windowsHide: true });

const report = [];
mkdirSync(artifactDir, { recursive: true });
let browserVersion;
try {
  browserVersion = await waitForJson(`http://127.0.0.1:${port}/json/version`);
  const targets = await waitForJson(`http://127.0.0.1:${port}/json/list`);
  const target = targets.find((item) => item.type === "page");
  if (!target) throw new Error("Browser did not create a page target");
  const cdp = connectCdp(target.webSocketDebuggerUrl);
  await cdp.opened;
  await cdp.send("Page.enable");
  await cdp.send("Runtime.enable");
  await cdp.send("Page.addScriptToEvaluateOnNewDocument", { source: axeSource });

  for (const route of routes) {
    const name = route.path.replace(/[^a-z0-9]+/gi, "_").replace(/^_|_$/g, "") || "home";
    for (const viewport of [{ name: "desktop", width: 1440, height: 1000 }, { name: "mobile", width: 390, height: 844 }]) {
      await evaluate(cdp, "performance.clearResourceTimings()");
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: viewport.width, height: viewport.height, deviceScaleFactor: 1, mobile: viewport.name === "mobile",
      });
      await cdp.send("Page.navigate", { url: new URL(route.path, webBase).href });
      const until = Date.now() + 15000;
      let pageState;
      while (Date.now() < until) {
        pageState = await evaluate(cdp, `(() => ({ready:document.readyState, heading:document.querySelector('h1')?.innerText ?? '', body:document.body?.innerText ?? ''}))()`);
        if (pageState.ready === "complete" && pageState.heading === route.heading) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      if (!pageState || pageState.heading !== route.heading) {
        throw new Error(`${viewport.name} ${route.path}: expected heading ${JSON.stringify(route.heading)}, got ${JSON.stringify(pageState?.heading)}`);
      }
      if (route.path.startsWith("/tasks/") && !pageState.body.includes("Updated documents can return old passages")) {
        throw new Error("Task detail did not render persisted ticket narrative");
      }
      if (route.path.startsWith("/runs/") && (!pageState.body.includes("Public redacted evidence") || pageState.body.includes("browser fixture completed"))) {
        throw new Error("Run page did not serve the redacted published evidence projection");
      }
      const needsApi = !["/docs", "/route-not-found"].includes(route.path);
      let apiRequests = [];
      if (needsApi) {
        const apiPrefix = process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8015";
        const apiWaitUntil = Date.now() + 8000;
        while (Date.now() < apiWaitUntil && apiRequests.length === 0) {
          apiRequests = await evaluate(cdp, `performance.getEntriesByType('resource').filter(x => x.name.startsWith(${JSON.stringify(apiPrefix)})).map(x => x.name)`);
          if (apiRequests.length === 0) await new Promise((resolve) => setTimeout(resolve, 100));
        }
      }
      if (needsApi && apiRequests.length === 0) throw new Error(`${route.path} did not make a real browser fetch to the API`);
      const dimensions = await evaluate(cdp, `({width:innerWidth, scrollWidth:Math.max(document.documentElement.scrollWidth, document.body.scrollWidth), height:document.documentElement.scrollHeight})`);
      if (dimensions.scrollWidth > dimensions.width + 1) {
        throw new Error(`${viewport.name} ${route.path}: page overflows horizontally (${dimensions.scrollWidth}px > ${dimensions.width}px)`);
      }
      const axe = await evaluate(cdp, `axe.run(document).then(r => r.violations.map(v => ({id:v.id, impact:v.impact, nodes:v.nodes.map(n => n.target)})))`, { awaitPromise: true });
      if (axe.length > 0) throw new Error(`${viewport.name} ${route.path}: axe violations ${JSON.stringify(axe)}`);
      const screenshot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, fromSurface: true });
      const screenshotName = `${name}-${viewport.name}.png`;
      writeFileSync(path.join(artifactDir, screenshotName), Buffer.from(screenshot.data, "base64"));
      report.push({ path: route.path, viewport: viewport.name, screenshot: screenshotName, apiRequests: apiRequests.length, ...dimensions, axeViolations: axe.length });
    }
  }
  cdp.ws.close();
} finally {
  browser.kill();
  const profilePath = path.resolve(profile);
  const tempPath = path.resolve(tmpdir());
  if (profilePath.startsWith(`${tempPath}${path.sep}`)) {
    try { rmSync(profilePath, { recursive: true, force: true }); }
    catch { /* Windows may retain a browser lock briefly; the profile is in the OS temp directory. */ }
  }
}

const evidence = {
  generatedAt: new Date().toISOString(),
  browser: browserVersion?.Browser ?? "unknown",
  webBase,
  apiBase: process.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8015",
  checks: report,
};
writeFileSync(path.join(artifactDir, "browser-check.json"), `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify({ routes: routes.length, viewportRuns: report.length, browser: evidence.browser, screenshots: artifactDir, report: path.join(artifactDir, "browser-check.json") }, null, 2));
