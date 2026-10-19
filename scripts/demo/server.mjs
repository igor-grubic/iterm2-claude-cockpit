import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { parseArgs } from "node:util";

const directory = dirname(fileURLToPath(import.meta.url));
const webview = resolve(directory, "../../iterm2_claude_cockpit/webview");
const outputs = {
  "api-claude": [
    "$ claude",
    "",
    "> Add a health endpoint and cover the edge cases.",
    "",
    "  Read src/server.ts",
    "  Updated src/routes/health.ts",
    "  Updated tests/health.test.ts",
    "",
    "  [ok] Health endpoint responds with service status",
    "  [ok] Tests cover healthy and unavailable states",
    "",
    "  Ready for your review.",
  ],
  "api-tests": [
    "$ npm test",
    "",
    "  PASS  tests/health.test.ts",
    "  PASS  tests/routes.test.ts",
    "  PASS  tests/config.test.ts",
    "",
    "  All tests passed.",
    "",
    "$ git status --short",
    " M src/routes/health.ts",
    " M tests/health.test.ts",
  ],
  "web-claude": [
    "$ claude",
    "",
    "> Make the settings panel keyboard accessible.",
    "",
    "  Read src/components/Settings.tsx",
    "  Updated keyboard navigation and focus states",
    "  Added interaction coverage",
    "",
    "  [ok] Tab order follows the visible controls",
    "  [ok] Escape closes the panel",
    "",
    "  Changes are ready to try in the preview.",
  ],
  "web-dev": [
    "$ npm run dev",
    "",
    "  VITE  ready",
    "",
    "  Local: http://localhost:5173/",
    "",
    "  [vite] settings panel updated",
    "  [vite] keyboard interaction updated",
    "",
    "  Watching for file changes...",
  ],
  "sandbox-shell": ["$ pwd", "/Users/demo/code/playground", "", "$ git status", "On branch main", "Working tree clean."],
};

function pane(id, name, cwd, job, active = false) {
  return {
    kind: "session", id, title: cwd.split("/").at(-1), session_name: name, active,
    cwd, job, last_line: outputs[id].at(-1), tty: "", claude: job === "claude",
  };
}

function initialSnapshot() {
  return {
    windows: [{
      kind: "window", id: "demo-window", title: "Window 1 \u00b7 3 tabs", active: true,
      tabs: [
        {
          kind: "tab", id: "api", title: "API service", active: true, color: 1, collapsed: false, sleeping: false,
          links: [],
          panes: [
            pane("api-claude", "Claude \u00b7 health endpoint", "/Users/demo/code/api-service", "claude", true),
            pane("api-tests", "Tests \u00b7 api-service", "/Users/demo/code/api-service", "node"),
          ],
        },
        {
          kind: "tab", id: "web", title: "Web app", active: false, color: 4, collapsed: false, sleeping: false,
          links: [],
          panes: [
            pane("web-claude", "Claude \u00b7 accessible settings", "/Users/demo/code/web-app", "claude"),
            pane("web-dev", "Dev server \u00b7 web-app", "/Users/demo/code/web-app", "node"),
          ],
        },
        {
          kind: "tab", id: "sandbox", title: "Playground", active: false, color: null, collapsed: true, sleeping: false,
          links: [],
          panes: [pane("sandbox-shell", "Shell \u00b7 playground", "/Users/demo/code/playground", "zsh")],
        },
      ],
    }],
  };
}

function summarize(snapshot) {
  const tabs = snapshot.windows.flatMap(window => window.tabs);
  return { windows: snapshot.windows.length, tabs: tabs.length, panes: tabs.flatMap(tab => tab.panes).length };
}

const files = new Map([
  ["/", [join(webview, "index.html"), "text/html"]],
  ["/demo", [join(directory, "index.html"), "text/html"]],
  ["/demo/scenes.json", [join(directory, "scenes.json"), "application/json"]],
  ...["app.js", "styles.css", "iterm_cheatsheet.html", "claude_cheatsheet.html"].map(name => [
    `/static/${name}`, [join(webview, name), name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : "text/html"],
  ]),
  ...["jetbrains-mono-variable.woff2", "space-grotesk-variable.woff2", "ibm-plex-mono-400.woff2", "ibm-plex-mono-500.woff2"].map(name => [
    `/static/fonts/${name}`, [join(webview, "fonts", name), "font/woff2"],
  ]),
]);

function json(response, body, status = 200) {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  response.end(JSON.stringify(body));
}

async function readBody(request) {
  request.setEncoding("utf8");
  let body = "";
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 16_384) throw new RangeError("Request body exceeds the demo limit");
  }
  const value = body ? JSON.parse(body) : {};
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new SyntaxError("Expected a JSON object");
  return value;
}

export async function createDemoServer({ port = 0 } = {}) {
  let snapshot = initialSnapshot();
  const savedSnapshot = initialSnapshot();
  let terminalOutputs = new Map(Object.entries(outputs));
  let closedPane = null;
  let restoreRuns = 0;
  let restored = 0;
  let theme = "1a";
  const allTabs = () => snapshot.windows.flatMap(window => window.tabs);
  function focusSession(id) {
    const targetTab = allTabs().find(tab => tab.panes.some(session => session.id === id));
    if (!targetTab) return false;
    for (const window of snapshot.windows) {
      window.active = window.tabs.includes(targetTab);
      for (const tab of window.tabs) {
        tab.active = tab === targetTab;
        for (const session of tab.panes) session.active = session.id === id;
      }
    }
    return true;
  }
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      const path = url.pathname;
      if (request.method === "GET") {
        if (path === "/favicon.ico") {
          response.writeHead(204);
          return response.end();
        }
        if (path === "/api/tree") return json(response, snapshot);
        if (path === "/api/settings") return json(response, { theme });
        if (path === "/api/about") return json(response, { version: "demo" });
        if (path === "/api/restore-preview") return json(response, {
          ok: true, ...summarize(savedSnapshot),
          missing_cwds: savedSnapshot.windows.flatMap(window => window.tabs).flatMap(tab => tab.panes).filter(pane => !pane.cwd).length,
        });
        if (path === "/demo/state") {
          const active = allTabs().flatMap(tab => tab.panes).find(session => session.active) ?? null;
          return json(response, {
            active, lines: active ? terminalOutputs.get(active.id) : [], theme,
            restore: { closedPane, restored, saved: summarize(savedSnapshot), current: summarize(snapshot) },
          });
        }
        const file = files.get(path);
        if (file) {
          const content = await readFile(file[0]);
          response.writeHead(200, { "Content-Type": file[1], "Cache-Control": "no-store" });
          return response.end(content);
        }
        return json(response, { ok: false, error: "Unknown demo endpoint" }, 404);
      }
      if (request.method !== "POST") return json(response, { ok: false, error: "Method not allowed" }, 405);
      const origin = request.headers.origin;
      if (origin && origin !== `http://127.0.0.1:${server.address().port}`) {
        return json(response, { ok: false, error: "Cross-origin demo request rejected" }, 403);
      }
      const body = await readBody(request);
      if (path === "/demo/reset") {
        snapshot = initialSnapshot();
        terminalOutputs = new Map(Object.entries(outputs));
        closedPane = null;
        restoreRuns = 0;
        restored = 0;
        theme = "1a";
        return json(response, { ok: true });
      }
      if (path === "/api/close-session") {
        const tab = allTabs().find(item => item.panes.some(session => session.id === body.id));
        if (!tab) return json(response, { ok: false, error: "Unknown demo pane" }, 400);
        const session = tab.panes.find(item => item.id === body.id);
        closedPane = session.session_name;
        restored = 0;
        tab.panes = tab.panes.filter(item => item !== session);
        terminalOutputs.delete(session.id);
        for (const window of snapshot.windows) {
          window.tabs = window.tabs.filter(item => item.panes.length > 0);
          window.title = `${window.title.split(" \u00b7 ")[0]} \u00b7 ${window.tabs.length} tabs`;
        }
        snapshot.windows = snapshot.windows.filter(window => window.tabs.length > 0);
        const remaining = allTabs().flatMap(item => item.panes);
        if (session.active && remaining.length) focusSession(remaining[0].id);
        return json(response, { ok: true });
      }
      if (path === "/api/restore") {
        restoreRuns += 1;
        const windows = structuredClone(savedSnapshot.windows);
        for (const window of windows) {
          window.id = `restored-${restoreRuns}-${window.id}`;
          window.title = `Window ${restoreRuns + 1} \u00b7 ${window.tabs.length} tabs`;
          for (const tab of window.tabs) {
            tab.id = `restored-${restoreRuns}-${tab.id}`;
            for (const session of tab.panes) {
              session.id = `restored-${restoreRuns}-${session.id}`;
              session.session_name = `Shell \u00b7 ${session.title}`;
              session.job = "zsh";
              session.claude = false;
              session.last_line = "$ ";
              terminalOutputs.set(session.id, ["$ pwd", session.cwd, "", "$ "]);
            }
          }
        }
        snapshot.windows.unshift(...windows);
        restored = summarize({ windows }).panes;
        focusSession(windows[0].tabs[0].panes[0].id);
        return json(response, { ok: true, restored });
      }
      if (path === "/api/settings") {
        if (!["1a", "2a"].includes(body.theme)) return json(response, { ok: false, error: "Unknown demo theme" }, 400);
        theme = body.theme;
        return json(response, { ok: true, theme });
      }
      if (path === "/api/focus") {
        if (body.kind !== "session" || !focusSession(body.id)) return json(response, { ok: false, error: "Unknown demo pane" }, 400);
        return json(response, { ok: true });
      }
      if (["/api/set-tab-color", "/api/set-tab-collapsed", "/api/set-tab-sleeping", "/api/rename-tab"].includes(path)) {
        const tab = allTabs().find(item => item.id === body.id);
        if (!tab) return json(response, { ok: false, error: "Unknown demo tab" }, 400);
        if (path === "/api/set-tab-sleeping") {
          if (typeof body.sleeping !== "boolean") return json(response, { ok: false, error: "Expected a sleep boolean" }, 400);
          tab.sleeping = body.sleeping;
          tab.collapsed = body.sleeping;
        } else if (path === "/api/rename-tab") {
          if (typeof body.name !== "string") return json(response, { ok: false, error: "Expected a tab name" }, 400);
          tab.title = body.name.trim() || `Tab ${allTabs().indexOf(tab) + 1}`;
        } else {
          if (tab.sleeping) return json(response, { ok: false, error: "tab is sleeping; wake it first" });
          if (path === "/api/set-tab-color") {
            if (body.color !== null && (!Number.isInteger(body.color) || body.color < 0 || body.color > 5)) {
              return json(response, { ok: false, error: "Expected a color index or null" }, 400);
            }
            tab.color = body.color;
          } else {
            if (typeof body.collapsed !== "boolean") return json(response, { ok: false, error: "Expected a collapse boolean" }, 400);
            tab.collapsed = body.collapsed;
          }
        }
        return json(response, { ok: true });
      }
      return json(response, { ok: false, error: "This action is not available in the demo. No terminal was changed." }, 400);
    } catch (error) {
      const badInput = error instanceof SyntaxError || error instanceof RangeError;
      if (!badInput) console.error("Demo request failed:", error);
      json(response, { ok: false, error: error.message }, badInput ? 400 : 500);
    }
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve())),
  };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { values } = parseArgs({ options: { port: { type: "string", default: "9877" } } });
  const port = Number(values.port);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error("Port must be between 0 and 65535");
  const demo = await createDemoServer({ port });
  console.log(`Synthetic cockpit demo: ${demo.url}/demo`);
  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.once(signal, async () => {
      await demo.close();
      process.exit(0);
    });
  }
}
