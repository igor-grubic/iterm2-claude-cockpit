import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createDemoServer } from "../scripts/demo/server.mjs";

let demo;
beforeEach(async () => { demo = await createDemoServer(); });
afterEach(async () => { await demo.close(); });

async function get(path) {
  const response = await fetch(demo.url + path);
  assert.equal(response.status, 200);
  return response.json();
}

async function post(path, body, status = 200) {
  const response = await fetch(demo.url + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  assert.equal(response.status, status);
  return response.json();
}

test("serves the real cockpit assets and clearly labeled synthetic sessions", async () => {
  const html = await (await fetch(demo.url + "/demo")).text();
  assert.match(html, /SYNTHETIC DEMO \/ NO TERMINAL CONNECTION/);
  assert.match(html, /<iframe[^>]+src="\/"/);
  const script = await (await fetch(demo.url + "/static/app.js")).text();
  const actualScript = await readFile(new URL("../iterm2_claude_cockpit/webview/app.js", import.meta.url), "utf8");
  assert.equal(script, actualScript);
  const snapshot = await get("/api/tree");
  const tabs = snapshot.windows[0].tabs;
  assert.equal(tabs.length, 3);
  assert.equal(tabs.flatMap(tab => tab.panes).length, 5);
  assert.ok(tabs.flatMap(tab => tab.panes).every(pane => pane.cwd.startsWith("/Users/demo/")));
  assert.equal((await get("/demo/state")).active.id, "api-claude");
});

test("focus updates the active pane and the synthetic terminal preview", async () => {
  assert.equal((await post("/api/focus", { kind: "session", id: "api-tests" })).ok, true);
  const state = await get("/demo/state");
  assert.equal(state.active.id, "api-tests");
  assert.equal(state.lines[0], "$ npm test");
  const tabs = (await get("/api/tree")).windows[0].tabs;
  assert.equal(tabs.flatMap(tab => tab.panes).filter(pane => pane.active).length, 1);
  assert.equal(tabs.filter(tab => tab.active).length, 1);
});

test("sleep preserves every color and pane while blocking collapse/color changes", async () => {
  for (const color of [null, 0, 1, 2, 3, 4, 5]) {
    await post("/api/set-tab-color", { id: "web", color });
    await post("/api/set-tab-sleeping", { id: "web", sleeping: true });
    let tab = (await get("/api/tree")).windows[0].tabs[1];
    assert.equal(tab.color, color);
    assert.equal(tab.sleeping, true);
    assert.equal(tab.collapsed, true);
    assert.equal(tab.panes.length, 2);
    assert.equal((await post("/api/set-tab-color", { id: "web", color: 3 })).ok, false);
    assert.equal((await post("/api/set-tab-collapsed", { id: "web", collapsed: false })).ok, false);
    await post("/api/set-tab-sleeping", { id: "web", sleeping: false });
    tab = (await get("/api/tree")).windows[0].tabs[1];
    assert.equal(tab.color, color);
    assert.equal(tab.sleeping, false);
    assert.equal(tab.collapsed, false);
  }
});

test("collapse, theme changes, and reset are repeatable", async () => {
  await post("/api/set-tab-collapsed", { id: "api", collapsed: true });
  assert.equal((await get("/api/tree")).windows[0].tabs[0].collapsed, true);
  await post("/api/settings", { theme: "2a" });
  assert.equal((await get("/api/settings")).theme, "2a");
  await post("/api/focus", { kind: "session", id: "web-dev" });
  await post("/demo/reset", {});
  assert.equal((await get("/api/settings")).theme, "1a");
  assert.equal((await get("/api/tree")).windows[0].tabs[0].collapsed, false);
  assert.equal((await get("/demo/state")).active.id, "api-claude");
  assert.deepEqual(await get("/api/restore-preview"), { ok: true, windows: 1, tabs: 3, panes: 5 });
});

test("invalid input never mutates demo state", async () => {
  const before = await get("/api/tree");
  for (const [path, body] of [
    ["/api/focus", { kind: "session", id: "missing" }],
    ["/api/settings", { theme: "missing" }],
    ["/api/set-tab-color", { id: "api", color: true }],
    ["/api/set-tab-color", { id: "api", color: 6 }],
    ["/api/set-tab-color", { id: "api" }],
    ["/api/set-tab-collapsed", { id: "api", collapsed: "true" }],
    ["/api/set-tab-sleeping", { id: "api", sleeping: 1 }],
    ["/api/set-tab-sleeping", { id: "missing", sleeping: true }],
    ["/api/rename-tab", { id: "api", name: null }],
  ]) {
    assert.equal((await post(path, body, 400)).ok, false);
  }
  assert.deepEqual(await get("/api/tree"), before);
});

test("unsupported actions fail explicitly rather than pretending to control iTerm2", async () => {
  const before = await get("/api/tree");
  for (const path of ["/api/restore", "/api/new-tab", "/api/new-window", "/api/close-session", "/api/open-url"]) {
    const result = await post(path, { id: "api-claude" }, 400);
    assert.equal(result.ok, false);
    assert.match(result.error, /not available in the demo/);
  }
  assert.deepEqual(await get("/api/tree"), before);
});

test("rejects malformed, cross-origin, and non-whitelisted requests", async () => {
  const malformed = await fetch(demo.url + "/api/settings", { method: "POST", body: "{" });
  assert.equal(malformed.status, 400);
  const crossOrigin = await fetch(demo.url + "/demo/reset", {
    method: "POST", headers: { Origin: "https://example.com" }, body: "{}",
  });
  assert.equal(crossOrigin.status, 403);
  assert.equal((await fetch(demo.url + "/server.py")).status, 404);
  assert.equal((await fetch(demo.url + "/api/tree", { method: "DELETE" })).status, 405);
});

test("walkthrough scenes have unique filenames, captions, and supported actions", async () => {
  const scenes = JSON.parse(await readFile(new URL("../scripts/demo/scenes.json", import.meta.url), "utf8"));
  assert.ok(scenes.length > 0);
  assert.equal(new Set(scenes.map(scene => scene.name)).size, scenes.length);
  for (const [index, scene] of scenes.entries()) {
    assert.ok(scene.name.startsWith(`${String(index + 1).padStart(2, "0")}-`));
    assert.match(scene.name, /^\d+-[a-z-]+$/);
    assert.ok(scene.caption.length > 0);
    assert.ok((scene.seconds ?? 1.7) > 0);
    assert.ok(scene.actions.every(action => ["click", "press"].includes(action.type) && action.selector));
  }
});
