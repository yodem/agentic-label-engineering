#!/usr/bin/env node
// Real-browser layout gate for the ALE web board.
//
// Launches headless Chrome via the DevTools protocol (no dependencies —
// built-in `fetch`/`WebSocket` only) and drives four viewport/theme cases
// against a running `ale board` server (or a static fixture page passed via
// --url). Each case is checked for:
//   (a) overlapping visible text
//   (b) horizontal overflow
//   (c) zero-size icons inside state badges (selector: `.state svg`)
//   (d) console errors / exceptions / CSP violations
//
// Fixture contract: a "ready" element matching --ready-selector (default
// `.task`) must exist once the page has something to check. State badges
// use class `state` (see ale/board.html); icons inside them must be `svg`.
// Real board task rows additionally carry a `data-id` attribute (set by
// board.html's client script), matched by --task-row-selector (default
// `.task[data-id]`) to count rendered rows independently of the ready wait.
//
// Two extra checks guard against a page that renders emptily (no overlap,
// no overflow, no console noise — because nothing rendered at all):
//   [not-ready]      the ready selector never matched within the timeout.
//   [tasks-missing]  the page rendered fewer task rows than the run's SSE
//                     snapshot reports, after clicking any "Show all N done"
//                     control to reveal collapsed rows. The expected count
//                     is read by fetching `<url>events` and parsing the
//                     first `snapshot` event; override with
//                     --expect-min-tasks N.
//
// Usage:
//   node scripts/board-check.mjs --out <dir> [--run-dir <dir>] [--url <url>]
//                                 [--python <path>] [--roster <path>]
//                                 [--ready-selector <css>]
//                                 [--task-row-selector <css>]
//                                 [--expect-min-tasks <n>]
//
// Exit code 0 only if all 4 cases pass.

import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, cpSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const REPO_ROOT = path.resolve(__dirname, "..");

const DEFAULT_PYTHON = process.env.ALE_PYTHON || "python3";
const CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const READY_TIMEOUT_MS = 10_000;
const POLL_INTERVAL_MS = 150;

const CASES = [
  { name: "desktop-light", width: 1400, height: 1000, mobile: false, scheme: "light" },
  { name: "desktop-dark", width: 1400, height: 1000, mobile: false, scheme: "dark" },
  { name: "mobile-light", width: 390, height: 844, mobile: true, scheme: "light" },
  { name: "mobile-dark", width: 390, height: 844, mobile: true, scheme: "dark" },
];

function parseArgs(argv) {
  const out = {
    readySelector: ".task",
    stateSvgSelector: ".state svg",
    taskRowSelector: ".task[data-id]",
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--out") out.out = argv[++i];
    else if (a === "--run-dir") out.runDir = argv[++i];
    else if (a === "--url") out.url = argv[++i];
    else if (a === "--python") out.python = argv[++i];
    else if (a === "--roster") out.roster = argv[++i];
    else if (a === "--ready-selector") out.readySelector = argv[++i];
    else if (a === "--state-svg-selector") out.stateSvgSelector = argv[++i];
    else if (a === "--task-row-selector") out.taskRowSelector = argv[++i];
    else if (a === "--expect-min-tasks") out.expectMinTasks = Number(argv[++i]);
    else throw new Error("unknown argument: " + a);
  }
  if (!out.out) throw new Error("--out <dir> is required");
  out.python = out.python || DEFAULT_PYTHON;
  out.roster = out.roster || path.join(REPO_ROOT, "examples", "roster.json");
  return out;
}

// ---------------------------------------------------------------------------
// Process lifecycle. Every process we launch is tracked here and killed by
// process-group on cleanup. We never touch a pid we did not start.
// ---------------------------------------------------------------------------

const cleanupFns = [];
let cleaningUp = false;

async function cleanup() {
  if (cleaningUp) return;
  cleaningUp = true;
  for (const fn of cleanupFns.slice().reverse()) {
    try {
      await fn();
    } catch (_err) {
      // best effort
    }
  }
}

process.on("exit", () => {
  // Synchronous best-effort kill in case async cleanup() never ran.
  for (const fn of cleanupFns.slice().reverse()) {
    try {
      fn();
    } catch (_err) {
      // ignore
    }
  }
});
process.on("SIGINT", async () => {
  await cleanup();
  process.exit(130);
});
process.on("SIGTERM", async () => {
  await cleanup();
  process.exit(143);
});

function killGroup(child) {
  if (!child || child.killed || child.exitCode !== null) return;
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (_err) {
    try {
      child.kill("SIGKILL");
    } catch (_err2) {
      // ignore
    }
  }
}

// ---------------------------------------------------------------------------
// Start `ale board` and read its printed URL.
// ---------------------------------------------------------------------------

function startBoardServer(pythonBin, runDir, rosterPath) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      pythonBin,
      ["-m", "ale", "board", "--run-dir", runDir, "--roster", rosterPath],
      {
        cwd: REPO_ROOT,
        env: { ...process.env, PYTHONPATH: REPO_ROOT },
        stdio: ["ignore", "pipe", "pipe"],
        detached: true,
      },
    );
    cleanupFns.push(() => killGroup(child));

    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("ale board did not print a URL in time. stderr: " + stderr));
    }, 15_000);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
      if (settled) return;
      const match = stdout.match(/https?:\/\/\S+/);
      if (match) {
        settled = true;
        clearTimeout(timer);
        resolve({ child, url: match[0].trim() });
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });
    child.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error("ale board exited early (code " + code + "). stderr: " + stderr));
    });
  });
}

// ---------------------------------------------------------------------------
// Launch headless Chrome and read the DevTools WebSocket URL from stderr.
// ---------------------------------------------------------------------------

function startChrome(userDataDir) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      CHROME_BIN,
      [
        "--headless=new",
        "--remote-debugging-port=0",
        "--user-data-dir=" + userDataDir,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--hide-scrollbars",
      ],
      { stdio: ["ignore", "ignore", "pipe"], detached: true },
    );
    cleanupFns.push(() => killGroup(child));

    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("Chrome did not print a DevTools URL in time. stderr: " + stderr));
    }, 15_000);

    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
      if (settled) return;
      const match = stderr.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) {
        settled = true;
        clearTimeout(timer);
        resolve({ child, wsUrl: match[1].trim() });
      }
    });
    child.on("error", (err) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(err);
    });
    child.on("exit", (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error("Chrome exited early (code " + code + "). stderr: " + stderr));
    });
  });
}

// ---------------------------------------------------------------------------
// Minimal CDP client over the built-in WebSocket (flattened sessions).
// ---------------------------------------------------------------------------

class CDP {
  constructor(wsUrl) {
    this.wsUrl = wsUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map(); // event name -> Set<fn>
  }

  async connect() {
    this.ws = new WebSocket(this.wsUrl);
    await new Promise((resolve, reject) => {
      this.ws.addEventListener("open", () => resolve(), { once: true });
      this.ws.addEventListener("error", (e) => reject(new Error("CDP connect failed: " + e.message)), { once: true });
    });
    this.ws.addEventListener("message", (ev) => this._onMessage(ev));
  }

  _onMessage(ev) {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (_err) {
      return;
    }
    if (msg.id != null && this.pending.has(msg.id)) {
      const { resolve, reject } = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message || "CDP error"));
      else resolve(msg.result);
      return;
    }
    if (msg.method) {
      const set = this.listeners.get(msg.method);
      if (set) for (const fn of set) fn(msg.params || {}, msg.sessionId);
    }
  }

  on(method, fn) {
    if (!this.listeners.has(method)) this.listeners.set(method, new Set());
    this.listeners.get(method).add(fn);
    return () => this.listeners.get(method).delete(fn);
  }

  send(method, params = {}, sessionId) {
    const id = this.nextId++;
    const payload = { id, method, params };
    if (sessionId) payload.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify(payload));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error("CDP timeout: " + method));
        }
      }, 20_000);
    });
  }

  close() {
    try {
      this.ws.close();
    } catch (_err) {
      // ignore
    }
  }
}

// ---------------------------------------------------------------------------
// In-page checker. Serialized to a string and evaluated via Runtime.evaluate.
// ---------------------------------------------------------------------------

function inPageChecker(viewportWidth, stateSvgSelector, taskRowSelector) {
  // Reveal any collapsed "Show all N done" rows before counting/checking,
  // so a task-count comparison against the SSE snapshot isn't fooled by
  // the board's own collapse-by-default behavior (threshold: 5 done rows).
  for (const btn of document.querySelectorAll("button")) {
    if (/^Show all \d+ done$/.test((btn.textContent || "").trim())) btn.click();
  }

  function describe(el) {
    if (!el || !el.tagName) return "unknown";
    const cls = typeof el.className === "string" && el.className.trim()
      ? "." + el.className.trim().split(/\s+/).join(".")
      : "";
    return el.tagName.toLowerCase() + cls;
  }

  function depthOf(el) {
    let d = 0;
    let cur = el;
    while (cur && cur !== document.body) {
      d++;
      cur = cur.parentElement;
    }
    return d;
  }

  // (a) overlapping visible text
  const owners = [];
  const skipTags = new Set(["SCRIPT", "STYLE", "TEMPLATE", "SELECT", "OPTION", "SVG"]);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const p = node.parentElement;
      if (!p || skipTags.has(p.tagName)) return NodeFilter.FILTER_REJECT;
      const cs = getComputedStyle(p);
      if (cs.visibility === "hidden" || cs.display === "none" || parseFloat(cs.opacity) === 0) {
        return NodeFilter.FILTER_REJECT;
      }
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  let node;
  while ((node = walker.nextNode())) {
    const range = document.createRange();
    range.selectNodeContents(node);
    // Clip each text rect to every ancestor that hides overflow: text cut off by an
    // ellipsis is not painted, so it cannot collide with a neighbour.
    let clip = null;
    for (let a = node.parentElement; a && a !== document.body; a = a.parentElement) {
      const acs = getComputedStyle(a);
      if (acs.overflowX !== "visible" || acs.overflowY !== "visible") {
        const b = a.getBoundingClientRect();
        clip = clip ? { left: Math.max(clip.left, b.left), right: Math.min(clip.right, b.right),
                        top: Math.max(clip.top, b.top), bottom: Math.min(clip.bottom, b.bottom) }
                    : { left: b.left, right: b.right, top: b.top, bottom: b.bottom };
      }
    }
    for (const raw of Array.from(range.getClientRects())) {
      const r = clip ? { left: Math.max(raw.left, clip.left), right: Math.min(raw.right, clip.right),
                         top: Math.max(raw.top, clip.top), bottom: Math.min(raw.bottom, clip.bottom) }
                     : { left: raw.left, right: raw.right, top: raw.top, bottom: raw.bottom };
      r.width = r.right - r.left; r.height = r.bottom - r.top;
      if (r.width <= 0 || r.height <= 0) continue;
      owners.push({ el: node.parentElement, rect: r, text: node.nodeValue.trim().slice(0, 40) });
    }
  }
  const overlaps = [];
  for (let i = 0; i < owners.length; i++) {
    for (let j = i + 1; j < owners.length; j++) {
      const A = owners[i];
      const B = owners[j];
      if (A.el === B.el) continue;
      if (A.el.contains(B.el) || B.el.contains(A.el)) continue;
      const left = Math.max(A.rect.left, B.rect.left);
      const right = Math.min(A.rect.right, B.rect.right);
      const top = Math.max(A.rect.top, B.rect.top);
      const bottom = Math.min(A.rect.bottom, B.rect.bottom);
      const w = right - left;
      const h = bottom - top;
      if (w > 1 && h > 1) {
        overlaps.push({
          a: describe(A.el) + " \"" + A.text + "\"",
          b: describe(B.el) + " \"" + B.text + "\"",
        });
      }
    }
  }

  // (b) horizontal overflow
  const overflowOffenders = [];
  for (const el of document.querySelectorAll("body *")) {
    if (el.scrollWidth > el.clientWidth + 1) {
      const cs = getComputedStyle(el);
      const overflowX = cs.overflowX;
      const clipped = ["hidden", "auto", "scroll", "clip"].includes(overflowX) || cs.textOverflow === "ellipsis";
      if (!clipped) {
        overflowOffenders.push({
          tag: describe(el),
          scrollWidth: el.scrollWidth,
          clientWidth: el.clientWidth,
          depth: depthOf(el),
        });
      }
    }
  }
  overflowOffenders.sort((x, y) => y.depth - x.depth);
  const docScrollWidth = document.documentElement.scrollWidth;
  const docOverflow = docScrollWidth > viewportWidth;

  // (c) icons
  const iconOffenders = [];
  for (const svg of document.querySelectorAll(stateSvgSelector)) {
    const r = svg.getBoundingClientRect();
    if (!(r.width > 0) || !(r.height > 0)) {
      iconOffenders.push({ tag: describe(svg.parentElement || svg), width: r.width, height: r.height });
    }
  }

  const renderedTaskCount = document.querySelectorAll(taskRowSelector).length;

  // (e) elements marked hidden must not be displayed (a class rule like
  // .btn{display:inline-flex} silently overrides [hidden]{display:none}).
  const hiddenShown = Array.from(document.querySelectorAll("[hidden]"))
    .filter((el) => !el.closest("svg") && getComputedStyle(el).display !== "none")
    .map((el) => el.tagName.toLowerCase() + (el.id ? "#" + el.id : "") + (el.className && typeof el.className === "string" ? "." + el.className.trim().split(/\s+/).join(".") : "") + " \"" + (el.textContent || "").trim().slice(0, 30) + "\"");

  return {
    hiddenShown,
    overlaps,
    overflowOffenders,
    docOverflow,
    docScrollWidth,
    iconOffenders,
    renderedTaskCount,
  };
}

async function waitForReady(cdp, sessionId, readySelector, timeoutMs) {
  // Strict: the ready selector must actually match. A page that renders
  // nothing (blank body, failed SSE connection, wrong run dir) must never
  // be treated as "ready" just because the document finished loading.
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const res = await cdp.send(
      "Runtime.evaluate",
      {
        expression: `(function(){
          try {
            return document.readyState === 'complete'
              && !!document.querySelector(${JSON.stringify(readySelector)});
          } catch (e) { return false; }
        })()`,
        returnByValue: true,
      },
      sessionId,
    );
    if (res.result && res.result.value === true) return true;
    if (Date.now() > deadline) return false;
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
  }
}

function isIgnorableUrl(url) {
  return typeof url === "string" && url.endsWith("/favicon.ico");
}

// Fetch the board's SSE stream just long enough to read the first
// `snapshot` event, to learn how many tasks the run actually has. Returns
// null (never fails the run) when the URL has no SSE endpoint (e.g. a
// static negative-control fixture) or the fetch/parse doesn't succeed.
async function fetchExpectedTaskCount(baseUrl, timeoutMs = 5000) {
  if (!baseUrl || !baseUrl.endsWith("/")) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(baseUrl.replace(/\/+$/, "/") + "events", { signal: controller.signal });
    if (!res.ok || !res.body) return null;
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buf.indexOf("\n\n")) !== -1) {
        const raw = buf.slice(0, boundary);
        buf = buf.slice(boundary + 2);
        let eventName = "message";
        const dataLines = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (eventName === "snapshot") {
          try {
            const payload = JSON.parse(dataLines.join("\n"));
            return Object.keys(payload.tasks || {}).length;
          } catch (_err) {
            return null;
          }
        }
      }
    }
    return null;
  } catch (_err) {
    return null;
  } finally {
    clearTimeout(timer);
    try {
      controller.abort();
    } catch (_err) {
      // ignore
    }
  }
}

async function runCase(cdp, browserUrl, targetUrl, testCase, outDir, opts, expectMinTasks) {
  const created = await cdp.send("Target.createTarget", { url: "about:blank" });
  const targetId = created.targetId;
  const attached = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  const sessionId = attached.sessionId;

  const consoleErrors = [];
  const offConsole = cdp.on("Runtime.consoleAPICalled", (params, sid) => {
    if (sid !== sessionId) return;
    if (params.type === "error") {
      const text = (params.args || []).map((a) => a.value ?? a.description ?? "").join(" ");
      consoleErrors.push("console.error: " + text.slice(0, 200));
    }
  });
  const offException = cdp.on("Runtime.exceptionThrown", (params, sid) => {
    if (sid !== sessionId) return;
    const d = params.exceptionDetails || {};
    consoleErrors.push("exception: " + (d.text || "") + " " + (d.exception?.description || "").slice(0, 200));
  });
  const offLog = cdp.on("Log.entryAdded", (params, sid) => {
    if (sid !== sessionId) return;
    const entry = params.entry || {};
    if (entry.level === "error") {
      if (isIgnorableUrl(entry.url)) return; // favicon 404 noise
      consoleErrors.push("log: " + (entry.text || "").slice(0, 200) + (entry.url ? " (" + entry.url + ")" : ""));
    }
  });

  try {
    await cdp.send("Page.enable", {}, sessionId);
    await cdp.send("Runtime.enable", {}, sessionId);
    await cdp.send("Log.enable", {}, sessionId);

    await cdp.send(
      "Emulation.setDeviceMetricsOverride",
      {
        width: testCase.width,
        height: testCase.height,
        deviceScaleFactor: 1,
        mobile: testCase.mobile,
      },
      sessionId,
    );
    await cdp.send(
      "Emulation.setEmulatedMedia",
      { features: [{ name: "prefers-color-scheme", value: testCase.scheme }] },
      sessionId,
    );

    await cdp.send("Page.navigate", { url: targetUrl }, sessionId);

    const ready = await waitForReady(cdp, sessionId, opts.readySelector, READY_TIMEOUT_MS);

    let result = { overlaps: [], overflowOffenders: [], docOverflow: false, iconOffenders: [], renderedTaskCount: 0 };
    const notReady = !ready;
    if (ready) {
      const evalRes = await cdp.send(
        "Runtime.evaluate",
        {
          expression: "(" + inPageChecker.toString() + ")(" + testCase.width + ", "
            + JSON.stringify(opts.stateSvgSelector) + ", " + JSON.stringify(opts.taskRowSelector) + ")",
          returnByValue: true,
        },
        sessionId,
      );
      result = evalRes.result?.value || result;
    }

    // Screenshot (full page) before we report, after the checker runs.
    const metrics = await cdp.send("Page.getLayoutMetrics", {}, sessionId);
    const contentSize = metrics.cssContentSize || { width: testCase.width, height: testCase.height };
    const shot = await cdp.send(
      "Page.captureScreenshot",
      {
        format: "png",
        clip: {
          x: 0,
          y: 0,
          width: Math.max(1, Math.ceil(contentSize.width)),
          height: Math.max(1, Math.ceil(contentSize.height)),
          scale: 1,
        },
        captureBeyondViewport: true,
      },
      sessionId,
    );
    if (shot && shot.data) {
      const file = path.join(outDir, testCase.name + ".png");
      writeFileSync(file, Buffer.from(shot.data, "base64"));
    }

    const tasksMissing = !notReady && expectMinTasks != null && result.renderedTaskCount < expectMinTasks;

    const tags = [];
    if (notReady) tags.push("not-ready");
    if (tasksMissing) tags.push("tasks-missing");
    if (result.overlaps.length) tags.push("overlap");
    if (result.overflowOffenders.length || result.docOverflow) tags.push("overflow");
    if (result.iconOffenders.length) tags.push("icons");
    if ((result.hiddenShown || []).length) tags.push("hidden-shown");
    if (consoleErrors.length) tags.push("console");

    const offenders = [];
    if (notReady) offenders.push("[not-ready] page never satisfied ready selector " + opts.readySelector + " within " + READY_TIMEOUT_MS + "ms");
    if (tasksMissing) offenders.push("[tasks-missing] rendered " + result.renderedTaskCount + " task rows (" + opts.taskRowSelector + "), expected at least " + expectMinTasks + " from the SSE snapshot");
    for (const o of result.overlaps.slice(0, 10)) offenders.push("[overlap] " + o.a + " x " + o.b);
    for (const o of result.overflowOffenders.slice(0, 10)) {
      offenders.push("[overflow] " + o.tag + " scrollWidth=" + o.scrollWidth + " clientWidth=" + o.clientWidth);
    }
    if (result.docOverflow) offenders.push("[overflow] document.documentElement scrollWidth=" + result.docScrollWidth + " > viewport " + testCase.width);
    for (const o of result.iconOffenders.slice(0, 10)) offenders.push("[icons] " + o.tag + " " + o.width + "x" + o.height);
    for (const h of (result.hiddenShown || []).slice(0, 10)) offenders.push("[hidden-shown] " + h);
    for (const e of consoleErrors.slice(0, 10)) offenders.push("[console] " + e);

    const pass = tags.length === 0;
    return { name: testCase.name, pass, tags, offenders: offenders.slice(0, 10) };
  } finally {
    offConsole();
    offException();
    offLog();
    try {
      await cdp.send("Target.closeTarget", { targetId });
    } catch (_err) {
      // ignore
    }
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  if (process.env.CODEX_SANDBOX) {
    // Chrome is killed at launch inside the Codex sandbox, and every kill pops a macOS
    // crash dialog on the user's screen. Never launch it there; the lead runs the gate.
    console.log("BOARD_CHECK_SKIPPED: Chrome cannot run inside the Codex sandbox");
    process.exit(3);
  }
  const opts = parseArgs(process.argv.slice(2));
  mkdirSync(opts.out, { recursive: true });

  let boardChild = null;
  let baseUrl = opts.url;
  let tempRunDir = null;

  if (!baseUrl) {
    let runDir = opts.runDir;
    if (!runDir) {
      tempRunDir = mkdtempSync(path.join(tmpdir(), "ale-board-check-run-"));
      cpSync(path.join(REPO_ROOT, "tests", "fixtures", "board", "check", "run-realistic"), tempRunDir, { recursive: true });
      runDir = tempRunDir;
    }
    const started = await startBoardServer(opts.python, runDir, opts.roster);
    boardChild = started.child;
    baseUrl = started.url;
  }

  const chromeUserDataDir = mkdtempSync(path.join(tmpdir(), "ale-board-check-chrome-"));
  const { wsUrl } = await startChrome(chromeUserDataDir);
  cleanupFns.push(() => rmSync(chromeUserDataDir, { recursive: true, force: true }));
  if (tempRunDir) cleanupFns.push(() => rmSync(tempRunDir, { recursive: true, force: true }));

  const cdp = new CDP(wsUrl);
  await cdp.connect();
  cleanupFns.push(() => cdp.close());

  let expectMinTasks = opts.expectMinTasks;
  if (expectMinTasks == null) {
    expectMinTasks = await fetchExpectedTaskCount(baseUrl);
  }

  const results = [];
  for (const testCase of CASES) {
    const result = await runCase(cdp, wsUrl, baseUrl, testCase, opts.out, opts, expectMinTasks);
    results.push(result);
    const status = result.pass ? "PASS" : "FAIL";
    let line = status + " " + result.name;
    if (!result.pass) {
      line += " [" + result.tags.join(",") + "]";
      if (result.offenders.length) line += " :: " + result.offenders.join(" | ");
    }
    console.log(line);
  }

  await cleanup();

  const allPass = results.every((r) => r.pass);
  process.exit(allPass ? 0 : 1);
}

main().catch(async (err) => {
  console.error("board-check failed: " + (err && err.stack ? err.stack : err));
  await cleanup();
  process.exit(1);
});
