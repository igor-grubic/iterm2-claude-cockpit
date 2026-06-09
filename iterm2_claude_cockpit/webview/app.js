window.PaneTreeExt = window.PaneTreeExt || {
  paneRowDecorators: [],
  paneTitleDecorators: [],
  shouldShowJob: [],
};

(() => {
  const treeEl = document.getElementById("tree");
  const statusEl = document.getElementById("status");
  const collapsed = new Set();
  const ext = window.PaneTreeExt;

  const IDLE_JOBS = new Set(["zsh", "-zsh", "bash", "-bash", "sh", "-sh", "fish", "-fish"]);
  function isIdle(job) { return !job || IDLE_JOBS.has(job); }

  let _measurePill = null;
  function pillWidth(text) {
    if (!_measurePill) {
      _measurePill = document.createElement("span");
      _measurePill.className = "pane-folder-pill";
      _measurePill.style.position = "absolute";
      _measurePill.style.visibility = "hidden";
      _measurePill.style.whiteSpace = "nowrap";
      _measurePill.style.left = "-9999px";
      _measurePill.style.top = "0";
      document.body.appendChild(_measurePill);
    }
    _measurePill.textContent = text;
    return _measurePill.offsetWidth;
  }

  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) { /* fall through to execCommand */ }
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch (_) {
      return false;
    }
  }

  let toastTimer = null;
  function toast(text, ok = true) {
    let el = document.querySelector(".toast");
    if (!el) {
      el = document.createElement("div");
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.background = ok ? "#2c4a2c" : "#5a2929";
    el.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 1500);
  }

  function setStatus(text, cls) {
    if (cls === "ok") {
      statusEl.classList.add("hidden");
    } else {
      statusEl.textContent = text;
      statusEl.className = "status " + (cls || "");
    }
  }

  function nodeKey(node) {
    return `${node.kind}:${node.id}`;
  }

  function renderTree(snapshot) {
    treeEl.innerHTML = "";
    if (!snapshot.windows || snapshot.windows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "node";
      empty.style.color = "var(--muted)";
      empty.textContent = "(no windows open)";
      treeEl.appendChild(empty);
    } else {
      for (const w of snapshot.windows) {
        treeEl.appendChild(renderWindow(w));
      }
    }
  }

  function renderWindow(w) {
    const wrap = document.createElement("div");
    const wKey = nodeKey(w);
    const wIsCollapsed = collapsed.has(wKey);
    const row = nodeRow(w, "window", wIsCollapsed);
    row.querySelector(".caret").addEventListener("click", (ev) => {
      ev.stopPropagation();
      toggle(wKey);
    });
    wrap.appendChild(row);
    if (!wIsCollapsed) {
      for (const t of w.tabs || []) {
        wrap.appendChild(renderTab(t));
      }
    }
    return wrap;
  }

  function startTabEdit(row, t) {
    const labelEl = row.querySelector(".node-label");
    const editBtn = row.querySelector(".tab-edit-btn");
    if (!labelEl) return;
    const input = document.createElement("input");
    input.className = "tab-edit-input";
    input.value = t.title;
    labelEl.replaceWith(input);
    if (editBtn) editBtn.style.display = "none";
    input.focus();
    input.select();

    let committed = false;
    function commit() {
      if (committed) return;
      committed = true;
      postAction("/api/rename-tab", { id: t.id, name: input.value.trim() });
      input.replaceWith(labelEl);
      if (editBtn) editBtn.style.display = "";
    }
    function cancel() {
      if (committed) return;
      committed = true;
      input.replaceWith(labelEl);
      if (editBtn) editBtn.style.display = "";
    }

    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.stopPropagation(); commit(); }
      if (ev.key === "Escape") { ev.stopPropagation(); cancel(); }
    });
    input.addEventListener("blur", commit);
    input.addEventListener("click", (ev) => ev.stopPropagation());
  }

  function renderTab(t) {
    const wrap = document.createElement("div");
    const tKey = nodeKey(t);
    const tIsCollapsed = collapsed.has(tKey);
    const row = nodeRow(t, "tab", tIsCollapsed, true);
    const caret = row.querySelector(".caret");
    if (caret) {
      caret.addEventListener("click", (ev) => {
        ev.stopPropagation();
        toggle(tKey);
      });
    }
    const editBtn = document.createElement("span");
    editBtn.className = "tab-edit-btn";
    editBtn.textContent = "✎";
    editBtn.title = "Rename tab";
    editBtn.addEventListener("click", (ev) => { ev.stopPropagation(); startTabEdit(row, t); });
    row.appendChild(editBtn);

    row.draggable = true;
    row.addEventListener("dragstart", (ev) => {
      dragTabId = t.id;
      dragWindowId = (findWindowForTab(t.id) || {}).id || null;
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", t.id);
      row.classList.add("dragging");
      isDragging = true;
    });
    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      document.querySelectorAll(".node.tab.drag-over").forEach(el => el.classList.remove("drag-over"));
      isDragging = false;
      dragTabId = null;
      dragWindowId = null;
    });
    row.addEventListener("dragover", (ev) => {
      if (!dragTabId || dragTabId === t.id) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      row.classList.add("drag-over");
    });
    row.addEventListener("dragleave", (ev) => {
      if (row.contains(ev.relatedTarget)) return;
      row.classList.remove("drag-over");
    });
    row.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      row.classList.remove("drag-over");
      if (!dragTabId || dragTabId === t.id) return;
      const srcTabId = dragTabId;
      const srcWindowId = dragWindowId;
      const targetWin = findWindowForTab(t.id);
      if (!targetWin || srcWindowId !== targetWin.id) return;
      const position = (targetWin.tabs || []).findIndex(tab => tab.id === t.id);
      if (position === -1) return;
      await postAction("/api/move-tab", { tab_id: srcTabId, window_id: srcWindowId, position });
    });

    wrap.appendChild(row);
    if (!tIsCollapsed && (t.panes || []).length > 0) {
      for (const p of t.panes) {
        wrap.appendChild(renderPane(p, t.id));
      }
    }
    return wrap;
  }

  function renderPane(p, tabId) {
    const row = document.createElement("div");
    row.className = "node pane" + (p.active ? " active" : "") + (p.buried ? " buried" : "");
    if (p.last_line) row.title = p.last_line;

    // Left action buttons — fixed at window-level left edge, always visible
    const leftActions = document.createElement("span");
    leftActions.className = "pane-left-actions";

    const idle = isIdle(p.job);
    const statusBtn = makeActionBtn("ℹ", (ev) => { ev.stopPropagation(); showStatusPopup(p, statusBtn); });
    statusBtn.setAttribute("title", idle ? "Status — idle" : `Status — ${p.job}`);
    statusBtn.classList.add(idle ? "action-idle" : "action-running");

    if (p.buried) {
      const unburyBtn = makeActionBtn("↑", (ev) => { ev.stopPropagation(); postAction("/api/unbury-session", { id: p.id }); });
      unburyBtn.setAttribute("title", "Restore pane");
      leftActions.append(statusBtn, unburyBtn);
    } else {
      const buryBtn = makeActionBtn("⊟", (ev) => { ev.stopPropagation(); postAction("/api/bury-session", { id: p.id, tab_id: tabId }); });
      buryBtn.setAttribute("title", "Bury — removes pane from tab, keeps running");
      leftActions.append(statusBtn, buryBtn);
    }
    row.appendChild(leftActions);


    const label = document.createElement("span");
    label.className = "pane-title";
    label.textContent = p.session_name || p.title || p.id;
    row.appendChild(label);
    for (const fn of ext.paneTitleDecorators) {
      try { fn(label, p); } catch (e) { console.error("paneTitleDecorator error", e); }
    }

    const showJob = p.job && ext.shouldShowJob.every((fn) => {
      try { return fn(p); } catch (e) { console.error("shouldShowJob error", e); return true; }
    });
    if (showJob) {
      const job = document.createElement("span");
      job.className = "node-job";
      job.textContent = p.job;
      row.appendChild(job);
    }

    if (p.title) {
      const pill = document.createElement("span");
      pill.className = "pane-folder-pill";
      pill.textContent = p.title;
      if (p.cwd) {
        pill.title = p.cwd;
        if (p.active) {
          pill.classList.add("pill-copyable");
          const w = Math.max(pillWidth(p.title), pillWidth("copy"));
          pill.style.minWidth = w + "px";
          pill.addEventListener("mouseenter", () => { pill.textContent = "copy"; });
          pill.addEventListener("mouseleave", () => { pill.textContent = p.title; });
        }
        pill.addEventListener("click", (ev) => {
          ev.stopPropagation();
          if (p.active) {
            copyToClipboard(p.cwd);
          } else {
            focusNode(p.kind, p.id);
          }
        });
      }
      row.appendChild(pill);
    }

    if (p.buried) {
      const badge = document.createElement("span");
      badge.className = "node-job buried-badge";
      badge.textContent = "buried";
      row.appendChild(badge);
    } else {
      const closeBtn = makeActionBtn("×", (ev) => {
        ev.stopPropagation();
        showConfirmPopup(closeBtn, () => postAction("/api/close-session", { id: p.id }));
      });
      closeBtn.setAttribute("title", "Close session");
      closeBtn.classList.add("pane-close-btn");
      row.appendChild(closeBtn);
    }

    row.addEventListener("click", () => focusNode(p.kind, p.id));
    for (const fn of ext.paneRowDecorators) {
      try { fn(row, p); } catch (e) { console.error("paneRowDecorator error", e); }
    }
    return row;
  }

  function nodeRow(node, kindCls, isCollapsed, hasChildren = true) {
    const row = document.createElement("div");
    row.className = "node " + kindCls + (node.active ? " active" : "");
    if (hasChildren) {
      const caret = document.createElement("span");
      caret.className = "caret";
      caret.textContent = isCollapsed ? "▸" : "▾";
      row.appendChild(caret);
    } else {
      const spacer = document.createElement("span");
      spacer.className = "caret";
      spacer.textContent = "•";
      row.appendChild(spacer);
    }
    const label = document.createElement("span");
    label.className = "node-label";
    label.textContent = node.title || node.id;
    row.appendChild(label);
    row.addEventListener("click", () => focusNode(node.kind, node.id));
    return row;
  }

  function makeActionBtn(text, handler) {
    const btn = document.createElement("span");
    btn.className = "pane-action-btn";
    btn.textContent = text;
    btn.addEventListener("click", handler);
    return btn;
  }

  async function postAction(path, body) {
    try {
      const res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!data.ok) toast(data.error || path + " failed", false);
    } catch (e) {
      toast(path + " error: " + e, false);
    }
  }

  async function showStatusPopup(node, anchor) {
    dismissPopup();
    const popup = document.createElement("div");
    popup.className = "lines-popup status-popup";
    popup.addEventListener("click", (ev) => ev.stopPropagation());

    for (const [label, value] of [
      ["job", node.job  || "(none)"],
      ["cwd", node.cwd  || "(unknown)"],
    ]) {
      const row = document.createElement("div");
      row.className = "status-row";
      const lbl = document.createElement("span");
      lbl.className = "status-label";
      lbl.textContent = label;
      const val = document.createElement("span");
      val.className = "status-value";
      val.textContent = value;
      row.append(lbl, val);
      popup.appendChild(row);
    }

    const divider = document.createElement("div");
    divider.className = "status-divider";
    popup.appendChild(divider);

    const rect = anchor.getBoundingClientRect();
    popup.style.top = (rect.bottom + 4) + "px";
    popup.style.left = "8px";
    popup.style.right = "8px";
    document.body.appendChild(popup);
    activePopup = popup;

    try {
      const res = await fetch(`/api/session-lines?id=${encodeURIComponent(node.id)}`);
      const data = await res.json();
      if (data.ok && data.lines && data.lines.length > 0) {
        for (const line of data.lines) {
          const el = document.createElement("div");
          el.className = "lines-line";
          el.textContent = line;
          popup.appendChild(el);
        }
      } else {
        const empty = document.createElement("div");
        empty.className = "lines-empty";
        empty.textContent = "(no output)";
        popup.appendChild(empty);
      }
    } catch (e) {
      const err = document.createElement("div");
      err.className = "lines-empty";
      err.textContent = "error fetching lines";
      popup.appendChild(err);
    }
  }

  let activePopup = null;
  function dismissPopup() {
    if (activePopup) { activePopup.remove(); activePopup = null; }
  }

  function showConfirmPopup(anchor, onConfirm) {
    dismissPopup();
    const popup = document.createElement("div");
    popup.className = "confirm-popup";
    popup.addEventListener("click", (ev) => ev.stopPropagation());

    const label = document.createElement("span");
    label.className = "confirm-label";
    label.textContent = "sure?";

    const yes = document.createElement("button");
    yes.className = "confirm-yes";
    yes.textContent = "yes";
    yes.addEventListener("click", () => { dismissPopup(); onConfirm(); });

    const no = document.createElement("button");
    no.className = "confirm-no";
    no.textContent = "no";
    no.addEventListener("click", () => dismissPopup());

    popup.append(label, yes, no);

    const rect = anchor.getBoundingClientRect();
    popup.style.top = (rect.bottom + 4) + "px";
    popup.style.right = (window.innerWidth - rect.right) + "px";

    document.body.appendChild(popup);
    activePopup = popup;
    yes.focus();
  }

  // Larger confirm shown ABOVE the button, summarizing what Restore will recreate.
  function showRestoreConfirmPopup(anchor, summary, onConfirm) {
    dismissPopup();
    const popup = document.createElement("div");
    popup.className = "confirm-popup restore-confirm-popup";
    popup.addEventListener("click", (ev) => ev.stopPropagation());

    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

    const title = document.createElement("div");
    title.className = "restore-confirm-title";
    title.textContent = "Restore workspace?";

    const counts = document.createElement("div");
    counts.className = "restore-confirm-counts";
    counts.textContent = `${plural(summary.windows || 0, "window")} · ${plural(summary.tabs || 0, "tab")} · ${plural(summary.panes || 0, "pane")}`;

    popup.append(title, counts);

    if (summary.claude) {
      const note = document.createElement("div");
      note.className = "restore-confirm-note";
      note.textContent = `includes ${plural(summary.claude, "Claude pane")}`;
      popup.append(note);
    }

    const actions = document.createElement("div");
    actions.className = "restore-confirm-actions";
    const yes = document.createElement("button");
    yes.className = "confirm-yes";
    yes.textContent = "yes";
    yes.addEventListener("click", () => { dismissPopup(); onConfirm(); });
    const no = document.createElement("button");
    no.className = "confirm-no";
    no.textContent = "no";
    no.addEventListener("click", () => dismissPopup());
    actions.append(yes, no);
    popup.append(actions);

    document.body.appendChild(popup);
    const rect = anchor.getBoundingClientRect();
    popup.style.bottom = (window.innerHeight - rect.top + 6) + "px";
    popup.style.right = (window.innerWidth - rect.right) + "px";
    activePopup = popup;
    yes.focus();
  }
  document.addEventListener("click", dismissPopup);
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") dismissPopup(); });

  function toggle(key) {
    if (collapsed.has(key)) collapsed.delete(key);
    else collapsed.add(key);
    if (lastSnapshot) renderTree(lastSnapshot);
  }

  async function focusNode(kind, id) {
    try {
      const res = await fetch("/api/focus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, id }),
      });
      const data = await res.json();
      if (!data.ok) toast(data.error || "focus failed", false);
    } catch (e) {
      toast("focus error: " + e, false);
    }
  }

  function activeSessionId() {
    for (const w of lastSnapshot?.windows || []) {
      for (const t of w.tabs || []) {
        for (const p of t.panes || []) {
          if (p.active) return p.id;
        }
      }
    }
    return null;
  }

  async function openModal(title, loadFn) {
    if (activePopup) { dismissPopup(); return; }
    const popup = document.createElement("div");
    popup.className = "iterm-cheatsheet-popup";
    popup.addEventListener("click", (ev) => ev.stopPropagation());

    const header = document.createElement("div");
    header.className = "iterm-cheatsheet-header";
    const titleEl = document.createElement("span");
    titleEl.className = "iterm-cheatsheet-title";
    titleEl.textContent = title;
    const close = document.createElement("button");
    close.type = "button";
    close.className = "iterm-cheatsheet-close";
    close.textContent = "×";
    close.setAttribute("aria-label", "Close");
    close.addEventListener("click", (ev) => { ev.stopPropagation(); dismissPopup(); });
    header.append(titleEl, close);

    const body = document.createElement("div");
    body.className = "iterm-cheatsheet-body";
    body.textContent = "Loading…";

    popup.append(header, body);
    document.body.appendChild(popup);
    activePopup = popup;

    try {
      await loadFn(body);
    } catch (err) {
      body.textContent = "Failed to load: " + err.message;
    }
  }

  let itermCheatsheetCache = null;

  async function openItermCheatsheet() {
    await openModal("iTerm2 cheatsheet", async (body) => {
      if (itermCheatsheetCache === null) {
        const res = await fetch("/static/iterm_cheatsheet.html", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        itermCheatsheetCache = await res.text();
      }
      body.innerHTML = itermCheatsheetCache;
    });
  }

  async function openSettings() {
    await openModal("Settings", async (body) => {
      const res = await fetch("/api/about", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const dl = document.createElement("dl");
      dl.className = "settings-list";

      const addRow = (term, value) => {
        const dt = document.createElement("dt");
        dt.textContent = term;
        const dd = document.createElement("dd");
        dd.textContent = value;
        dl.append(dt, dd);
      };

      addRow("Version", data.version || "(unknown)");
      const enabled = data.extensions?.enabled || [];
      addRow("Enabled extensions", enabled.length ? enabled.join(", ") : "(none)");
      const available = data.extensions?.available || [];
      const disabled = available.filter((n) => !enabled.includes(n));
      addRow("Available (disabled)", disabled.length ? disabled.join(", ") : "(none)");

      body.innerHTML = "";
      body.appendChild(dl);
    });
  }

  document.getElementById("btn-cheatsheet").addEventListener("click", (ev) => {
    ev.stopPropagation();
    openItermCheatsheet();
  });

  document.getElementById("btn-settings").addEventListener("click", (ev) => {
    ev.stopPropagation();
    openSettings();
  });

  async function runRestore(anchor) {
    if (anchor.disabled) return;              // a restore is already in flight
    anchor.disabled = true;                   // block duplicate restores until done
    toast("restoring…");
    try {
      const res = await fetch("/api/restore", { method: "POST" });
      const data = await res.json();
      if (!data.ok) { toast(data.error || "restore failed", false); return; }
      const n = data.restored || 0;
      let msg = `restored ${n} pane${n === 1 ? "" : "s"}`;
      if (data.resumed) msg += `, resumed ${data.resumed}`;
      if (data.skipped) msg += `, skipped ${data.skipped}`;
      toast(msg);
    } catch (e) {
      toast("restore error: " + e, false);
    } finally {
      anchor.disabled = false;
    }
  }

  document.getElementById("btn-restore").addEventListener("click", async (ev) => {
    ev.stopPropagation();
    const anchor = ev.currentTarget;
    // Fetch what would be restored (from the saved pre-close layout), then confirm.
    let summary;
    try {
      const res = await fetch("/api/restore-preview", { cache: "no-store" });
      summary = await res.json();
    } catch (e) {
      toast("restore preview error: " + e, false);
      return;
    }
    if (!summary.ok) { toast(summary.error || "no saved workspace", false); return; }
    showRestoreConfirmPopup(anchor, summary, () => runRestore(anchor));
  });

  document.getElementById("btn-split-vertical").addEventListener("click", async () => {
    const id = activeSessionId();
    if (!id) { toast("no active pane", false); return; }
    try {
      const res = await fetch("/api/split-pane", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, vertical: true }),
      });
      const data = await res.json();
      if (!data.ok) toast(data.error || "split failed", false);
    } catch (e) { toast("split error: " + e, false); }
  });

  document.getElementById("btn-split-horizontal").addEventListener("click", async () => {
    const id = activeSessionId();
    if (!id) { toast("no active pane", false); return; }
    try {
      const res = await fetch("/api/split-pane", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, vertical: false }),
      });
      const data = await res.json();
      if (!data.ok) toast(data.error || "split failed", false);
    } catch (e) { toast("split error: " + e, false); }
  });

  document.getElementById("btn-new-tab").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/new-tab", { method: "POST" });
      const data = await res.json();
      if (!data.ok) toast(data.error || "new tab failed", false);
    } catch (e) { toast("new tab error: " + e, false); }
  });

  document.getElementById("btn-new-window").addEventListener("click", async () => {
    try {
      const res = await fetch("/api/new-window", { method: "POST" });
      const data = await res.json();
      if (!data.ok) toast(data.error || "new window failed", false);
    } catch (e) { toast("new window error: " + e, false); }
  });

  let lastSnapshot = null;
  let lastSnapshotJson = "";
  let consecutiveFailures = 0;
  let isDragging = false;
  let dragTabId = null;
  let dragWindowId = null;

  function findWindowForTab(tabId) {
    for (const w of lastSnapshot?.windows || []) {
      if ((w.tabs || []).some(t => t.id === tabId)) return w;
    }
    return null;
  }

  async function pollOnce() {
    try {
      const res = await fetch("/api/tree", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      consecutiveFailures = 0;
      setStatus("live", "ok");
      const json = JSON.stringify(data);
      if (json !== lastSnapshotJson) {
        lastSnapshotJson = json;
        lastSnapshot = data;
        if (!document.querySelector(".tab-edit-input") && !isDragging) {
          renderTree(lastSnapshot);
        }
      }
    } catch (e) {
      consecutiveFailures++;
      if (consecutiveFailures >= 2) {
        setStatus("disconnected — retrying", "error");
      }
    }
  }

  setStatus("connecting…");
  pollOnce();
  setInterval(pollOnce, 500);
})();
