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

test("closing a pane preserves the saved layout and selects a remaining pane", async () => {
  await post("/api/focus", { kind: "session", id: "api-tests" });
  assert.equal((await post("/api/close-session", { id: "api-tests" })).ok, true);
  const state = await get("/demo/state");
  assert.equal(state.active.id, "api-claude");
  assert.equal(state.restore.closedPane, "Tests \u00b7 api-service");
  assert.equal(state.restore.current.panes, 4);
  assert.equal(state.restore.saved.panes, 5);
  assert.equal(state.restore.restored, 0);
  assert.deepEqual(await get("/api/restore-preview"), { ok: true, windows: 1, tabs: 3, panes: 5 });
  await post("/api/close-session", { id: "api-claude" });
  const snapshot = await get("/api/tree");
  assert.equal(snapshot.windows[0].tabs.length, 2);
  assert.match(snapshot.windows[0].title, /2 tabs$/);
  assert.equal((await get("/demo/state")).active.id, "web-claude");
});

test("restore creates fresh shell panes in new windows without replacing existing panes", async () => {
  const saved = await get("/api/tree");
  await post("/api/set-tab-color", { id: "web", color: 5 });
  await post("/api/close-session", { id: "api-tests" });
  const existing = (await get("/api/tree")).windows[0];
  assert.deepEqual(await post("/api/restore", {}), { ok: true, restored: 5 });
  const snapshot = await get("/api/tree");
  assert.equal(snapshot.windows.length, 2);
  existing.active = false;
  for (const tab of existing.tabs) {
    tab.active = false;
    for (const pane of tab.panes) pane.active = false;
  }
  assert.deepEqual(snapshot.windows[1], existing);
  const restoredPanes = snapshot.windows[0].tabs.flatMap(tab => tab.panes);
  assert.equal(restoredPanes.length, 5);
  assert.deepEqual(restoredPanes.map(pane => pane.cwd), saved.windows[0].tabs.flatMap(tab => tab.panes).map(pane => pane.cwd));
  assert.ok(restoredPanes.every(pane => pane.job === "zsh" && !pane.claude && pane.id.startsWith("restored-1-")));
  await post("/api/focus", { kind: "session", id: restoredPanes[1].id });
  const state = await get("/demo/state");
  assert.equal(state.active.id, restoredPanes[1].id);
  assert.deepEqual(state.lines, ["$ cd /Users/demo/code/api-service", "", "$ pwd", "/Users/demo/code/api-service", "", "$ "]);
  assert.equal(state.restore.restored, 5);
  assert.deepEqual(await post("/api/restore", {}), { ok: true, restored: 5 });
  const again = await get("/api/tree");
  const paneIds = again.windows.flatMap(window => window.tabs).flatMap(tab => tab.panes).map(pane => pane.id);
  assert.equal(again.windows.length, 3);
  assert.equal(new Set(paneIds).size, paneIds.length);
  assert.equal(paneIds.length, 14);
});

test("an empty demo workspace can be restored and reset", async () => {
  const panes = (await get("/api/tree")).windows.flatMap(window => window.tabs).flatMap(tab => tab.panes);
  for (const pane of panes) await post("/api/close-session", { id: pane.id });
  assert.deepEqual((await get("/api/tree")).windows, []);
  assert.equal((await get("/demo/state")).active, null);
  assert.deepEqual((await get("/demo/state")).lines, []);
  assert.deepEqual(await post("/api/restore", {}), { ok: true, restored: 5 });
  assert.equal((await get("/demo/state")).restore.current.panes, 5);
  await post("/demo/reset", {});
  const state = await get("/demo/state");
  assert.equal(state.active.id, "api-claude");
  assert.equal(state.restore.closedPane, null);
  assert.equal(state.restore.restored, 0);
  assert.equal(state.restore.current.windows, 1);
});

test("invalid input never mutates demo state", async () => {
  const before = await get("/api/tree");
  for (const [path, body] of [
    ["/api/focus", { kind: "session", id: "missing" }],
    ["/api/close-session", { id: "missing" }],
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
  for (const path of ["/api/new-tab", "/api/new-window", "/api/open-url"]) {
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
  assert.ok(scenes.every(scene => scene.actions.every(action => !/settings|theme/.test(action.selector))));
  const before = scenes.findIndex(scene => scene.name === "11-before-restore");
  const preview = scenes.findIndex(scene => scene.name === "12-restore-preview");
  const restored = scenes.findIndex(scene => scene.name === "13-restored");
  assert.ok(before > 0 && before < preview && preview < restored);
  assert.ok(scenes[preview].seconds >= 3);
  assert.ok(scenes[restored].seconds >= 3);
});
