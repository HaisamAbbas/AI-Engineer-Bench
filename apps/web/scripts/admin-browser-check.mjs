// Invoked by scripts/check_admin_browser.py. Local fixture IdP; real API writes.
import assert from 'node:assert/strict';
import { createServer } from 'node:https';
import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdtempSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const artifacts = path.resolve('.cache/admin-browser');
const fixture = JSON.parse(readFileSync(path.join(artifacts, 'fixture.json'), 'utf8'));
const web = 'http://127.0.0.1:5176';
const issuer = 'https://127.0.0.1:8446';
const codes = new Map();
let subject = 'browser-viewer';
let exchanges = 0;
const idp = createServer({ key: readFileSync(path.join(artifacts, 'key.pem')), cert: readFileSync(path.join(artifacts, 'cert.pem')) }, async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', web);
  const url = new URL(req.url, issuer);
  try {
    if (url.pathname === '/.well-known/openid-configuration') {
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token` }));
    } else if (url.pathname === '/authorize') {
      assert.equal(url.searchParams.get('client_id'), 'browser-fixture');
      assert.equal(url.searchParams.get('redirect_uri'), `${web}/auth/callback`);
      assert.equal(url.searchParams.get('response_type'), 'code');
      assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
      assert.ok(url.searchParams.get('state'));
      assert.ok(url.searchParams.get('code_challenge'));
      const code = randomUUID();
      codes.set(code, { challenge: url.searchParams.get('code_challenge'), subject });
      res.writeHead(302, { Location: `${web}/auth/callback?code=${code}&state=${url.searchParams.get('state')}` });
      res.end();
    } else if (url.pathname === '/token') {
      let raw = '';
      for await (const chunk of req) raw += chunk;
      const body = new URLSearchParams(raw);
      const pending = codes.get(body.get('code'));
      codes.delete(body.get('code'));
      assert.ok(pending);
      assert.equal(body.get('grant_type'), 'authorization_code');
      assert.equal(body.get('client_id'), 'browser-fixture');
      assert.equal(body.get('redirect_uri'), `${web}/auth/callback`);
      assert.equal(body.has('client_secret'), false);
      assert.equal(req.headers.authorization, undefined);
      assert.equal(createHash('sha256').update(body.get('code_verifier')).digest('base64url'), pending.challenge);
      exchanges++;
      res.setHeader('Content-Type', 'application/json');
      res.end(JSON.stringify({ access_token: fixture.tokens[pending.subject], token_type: 'Bearer', expires_in: 600 }));
    } else { res.writeHead(404); res.end(); }
  } catch (error) { res.writeHead(400); res.end(String(error)); }
});
await new Promise((resolve, reject) => idp.once('error', reject).listen(8446, '127.0.0.1', resolve));
const executable = process.env.AIEB_BROWSER_EXECUTABLE ?? [
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', '/usr/bin/google-chrome', '/usr/bin/chromium',
].find(existsSync);
assert.ok(executable, 'Set AIEB_BROWSER_EXECUTABLE');
const profile = mkdtempSync(path.join(tmpdir(), 'aieb-admin-browser-'));
const browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  // Trust only the ephemeral local fixture certificate (not arbitrary certificates).
  `--ignore-certificate-errors-spki-list=${createHash('sha256').update((await import('node:crypto')).createPublicKey(readFileSync(path.join(artifacts, 'cert.pem'))).export({ type: 'spki', format: 'der' })).digest('base64')}`,
  '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws;
const requests = [];
let discarded;
let eventError;
try {
  let port;
  for (let i = 0; i < 150; i++) {
    try { port = Number(readFileSync(path.join(profile, 'DevToolsActivePort'), 'utf8').split('\n')[0]); break; } catch { await sleep(100); }
  }
  assert.ok(port, 'browser startup');
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  ws = new WebSocket(targets.find(x => x.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve); ws.addEventListener('error', reject); });
  let serial = 0;
  const pending = new Map();
  function send(method, params = {}) {
    const id = ++serial;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timed out ${method}`)); }, 15000);
      pending.set(id, { resolve, reject, timer });
      ws.send(JSON.stringify({ id, method, params }));
    });
  }
  ws.addEventListener('message', event => {
    const message = JSON.parse(String(event.data));
    if (message.id) {
      const item = pending.get(message.id);
      if (!item) return;
      clearTimeout(item.timer); pending.delete(message.id);
      if (message.error) item.reject(new Error(message.error.message)); else item.resolve(message.result);
    } else if (message.method === 'Fetch.requestPaused') {
      void (async () => {
        const p = message.params;
        const request = p.request;
        if (request.method === 'POST' && new URL(request.url).pathname === '/v1/campaigns') {
          assert.equal(p.responseStatusCode, 201);
          const body = await send('Fetch.getResponseBody', { requestId: p.requestId });
          const response = JSON.parse(body.base64Encoded ? Buffer.from(body.body, 'base64').toString() : body.body);
          const key = Object.entries(request.headers).find(([name]) => name.toLowerCase() === 'idempotency-key')?.[1];
          requests.push({ key, campaignId: response.id, status: p.responseStatusCode });
          if (!discarded) {
            discarded = response.id;
            await send('Fetch.failRequest', { requestId: p.requestId, errorReason: 'ConnectionClosed' });
            return;
          }
        }
        await send('Fetch.continueRequest', { requestId: p.requestId });
      })().catch(error => { eventError = error; });
    }
  });
  async function evaluate(expression) {
    if (eventError) throw eventError;
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, userGesture: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text);
    return result.result?.value;
  }
  async function wait(expression, label = expression) {
    for (let i = 0; i < 150; i++) {
      try { if (await evaluate(expression)) return; } catch (error) {
        if (!String(error).includes('context')) throw error;
      }
      await sleep(100);
    }
    throw new Error(`Timed out ${label}: ${await evaluate('document.body.innerText')}`);
  }
  const button = name => `[...document.querySelectorAll('button')].find(x => x.textContent === ${JSON.stringify(name)} && !x.disabled)`;
  async function click(name) { await wait(`!!(${button(name)})`); await evaluate(`${button(name)}.click()`); }
  async function fill(label, value) {
    await evaluate(`(() => { const label = [...document.querySelectorAll('label')].find(x => x.textContent.startsWith(${JSON.stringify(label)})); const input = label?.querySelector('input,textarea'); if (!input) throw new Error('missing field'); Object.getOwnPropertyDescriptor(input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)}); input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
  }
  await send('Page.enable'); await send('Runtime.enable');
  await send('Fetch.enable', { patterns: [{ urlPattern: 'http://127.0.0.1:8026/v1/campaigns', requestStage: 'Response' }] });
  await send('Page.navigate', { url: web });
  await click('Sign in');
  await wait("document.body.innerText.includes('No roles assigned')");
  assert.equal(await evaluate("!!document.querySelector('nav a[href=\"/admin/campaigns\"]')"), false);
  await click('Sign out');
  subject = 'browser-operator';
  await click('Sign in');
  await wait("document.body.innerText.includes('Roles: operator')");
  await evaluate("document.querySelector('nav a[href=\"/admin/campaigns\"]').click()");
  await wait("document.body.innerText.includes('Create draft')");
  const name = 'Browser acceptance fixture';
  await fill('Name', name); await fill('Draft JSON', JSON.stringify(fixture.draft));
  await click('Create campaign');
  await wait("document.body.innerText.includes('Failed to fetch') || document.body.innerText.includes('NetworkError')");
  assert.ok(discarded, 'create committed before its response was discarded');
  await send('Page.reload');
  await wait("document.body.innerText.includes('Create draft')");
  await fill('Name', name); await fill('Draft JSON', JSON.stringify(fixture.draft));
  await click('Create campaign');
  await wait("document.body.innerText.includes('Edit draft')");
  assert.equal(requests.length, 2);
  assert.ok(requests[0].key);
  assert.equal(requests[0].key, requests[1].key);
  assert.equal(requests[0].campaignId, requests[1].campaignId);
  await fill('Replacement draft JSON', JSON.stringify({ ...fixture.draft, repetitions: 2 }));
  await click('Save draft');
  await wait("document.body.innerText.includes('Saving uses revision 1')");
  await fill('Registry JSON', JSON.stringify(fixture.registry));
  await click('Preview exact matrix');
  await wait("document.body.innerText.includes('Server preview: 2 trials')");
  await evaluate("[...document.querySelectorAll('label')].find(x => x.textContent.includes('I understand freezing')).querySelector('input').click()");
  await click('Freeze campaign');
  await click('Start campaign');
  await wait("document.querySelector('dd[aria-live]')?.textContent === 'running'");
  assert.equal(exchanges, 2);
  assert.equal(await evaluate('localStorage.length'), 0);
  assert.equal(await evaluate("sessionStorage.getItem('aieb.oidc.pkce')"), null);
  const screenshot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(path.join(artifacts, 'running.png'), Buffer.from(screenshot.data, 'base64'));
  writeFileSync(path.join(artifacts, 'report.json'), JSON.stringify({ generatedAt: new Date().toISOString(),
    login: 'local HTTPS fixture IdP, PKCE S256; test-only API verifier; server-side roles',
    roleChecks: ['viewer: no admin nav', 'operator: admin nav'], exchanges, requests,
    steps: ['create response lost after commit', 'page reload', 'same-key retry', 'edit', 'preview 2 trials', 'freeze', 'start'],
    state: 'running', persistentTokenStorage: false }, null, 2));
  console.log('PASS: real browser/API admin flow');
} finally {
  ws?.close(); browser.kill(); idp.close();
  try { rmSync(profile, { recursive: true, force: true }); } catch { /* temporary Edge profile locks */ }
}
