(() => {
  const treeEl = document.getElementById("tree");
  const statusEl = document.getElementById("status");

  // A pane is Claude when the daemon's ps-based check flags it (p.claude) — the
  // npm/Node install shows a `node` job, so the job name alone isn't enough. The
  // job-name check is a cheap fallback for a bare `claude` binary in the foreground.
  const CLAUDE_JOBS = new Set(["claude", "claude-code"]);
  function isClaudePane(p) { return Boolean(p.claude) || CLAUDE_JOBS.has(p.job); }

  // Claude panes get a fixed yellow title, regardless of theme or group color — matches
  // --clr-claude in styles.css.
  const CLAUDE_YELLOW = "#f6c177";

  // The active-pane accent for uncolored groups — matches --accent in styles.css. Colored
  // groups use their own (much stronger) color for the active-pane highlight instead.
  const ACTIVE_FALLBACK = "#4ea1ff";

  // Per-theme color palette (index 0-5, cycled by clicking a swatch) plus the neutral/label/
  // title colors that go with it. Selectable in Settings; "2a" is the default.
  // Both palettes are ordered: red, green, yellow, purple, blue, brown.
  const THEMES = {
    "2a": {
      colors: ["#e2685f", "#7dd3a8", "#e8c26a", "#d8a0e6", "#8ab4f8", "#b58a63"],
      none: "#333941",
      labelDim: "#79818b",
      labelOn: "#c9cfd6",
      titleDim: "#cdd3da",
      titleOn: "#ffffff",
    },
    "1a": {
      colors: ["#f0685c", "#9ccfa0", "#f3c96a", "#c792ea", "#7aa2f7", "#bb8b5e"],
      none: "#356168", // brighter than the chrome border tones — an uncolored group still needs a visible spine
      labelDim: "#7c9a9c",
      labelOn: "#cfe3e0",
      titleDim: "#b9cfcd",
      titleOn: "#ecf6f4",
    },
  };
  let currentTheme = "2a";
  function theme() { return THEMES[currentTheme] || THEMES["2a"]; }

  function rgba(hex, a) {
    const n = parseInt(hex.replace("#", ""), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  function nextColor(current) {
    if (current === null || current === undefined) return 0;
    if (current === 5) return null;
    return current + 1;
  }

  // Optimistic local overrides so a click updates the panel instantly rather than
  // waiting for the next poll (up to ~500ms) or the daemon's 1s periodic refresh —
  // the POST persists the change server-side in the background.
  const colorOverrides = new Map(); // tab id -> color index | null
  const collapsedOverrides = new Map(); // tab id -> bool

  // Same idea for focus: clicking a pane used to just fire the POST and wait for a poll
  // to notice — up to ~500ms of visible lag before the highlight moved. This makes the
  // click itself the source of truth until the server confirms it.
  let activeOverride = null; // session id, or null once the server has confirmed it
  function isPaneActive(p) { return activeOverride !== null ? p.id === activeOverride : Boolean(p.active); }

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

  let filterColor = null; // ephemeral, shared across all window sections, not persisted

  function renderTree(snapshot) {
    treeEl.innerHTML = "";
    if (!snapshot.windows || snapshot.windows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "tree-empty";
      empty.textContent = "(no windows open)";
      treeEl.appendChild(empty);
    } else {
      for (const w of snapshot.windows) {
        treeEl.appendChild(renderWindow(w));
      }
    }
  }

  function renderFilterChips() {
    const wrap = document.createElement("div");
    wrap.className = "filter-chips";
    theme().colors.forEach((hex, i) => {
      const chip = document.createElement("div");
      chip.className = "filter-chip";
      chip.title = `Filter group color ${i + 1}`;
      const selected = filterColor === i;
      chip.style.background = selected ? hex : rgba(hex, 0.22);
      chip.style.boxShadow = `inset 0 0 0 1px ${selected ? "#ffffff88" : rgba(hex, 0.7)}`;
      chip.addEventListener("click", () => {
        filterColor = filterColor === i ? null : i;
        if (lastSnapshot) renderTree(lastSnapshot);
      });
      wrap.appendChild(chip);
    });
    return wrap;
  }

  function renderWindow(w) {
    const wrap = document.createElement("div");
    wrap.className = "window-block";

    const meta = document.createElement("div");
    meta.className = "window-meta";
    const label = document.createElement("span");
    label.className = "window-label";
    label.textContent = w.title || w.id;
    meta.append(label, renderFilterChips());
    wrap.appendChild(meta);

    const list = document.createElement("div");
    list.className = currentTheme === "1a" ? "group-list-classic" : "group-list";
    for (const t of w.tabs || []) {
      list.appendChild(currentTheme === "1a" ? renderGroup1a(t) : renderGroup2a(t));
    }
    wrap.appendChild(list);

    return wrap;
  }

  function startGroupEdit(header, t) {
    const labelEl = header.querySelector(".group-name");
    const editBtn = header.querySelector(".group-edit-btn");
    if (!labelEl) return;
    const input = document.createElement("input");
    input.className = "group-edit-input";
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

  function cycleColor(t) {
    const current = colorOverrides.has(t.id) ? colorOverrides.get(t.id) : (t.color ?? null);
    const next = nextColor(current);
    colorOverrides.set(t.id, next);
    postAction("/api/set-tab-color", { id: t.id, color: next });
    if (lastSnapshot) renderTree(lastSnapshot);
  }

  function toggleCollapse(t) {
    const current = collapsedOverrides.has(t.id) ? collapsedOverrides.get(t.id) : Boolean(t.collapsed);
    const next = !current;
    collapsedOverrides.set(t.id, next);
    postAction("/api/set-tab-collapsed", { id: t.id, collapsed: next });
    if (lastSnapshot) renderTree(lastSnapshot);
  }

  // Shared by both theme skins — a group header is draggable to reorder its tab within the
  // window (see /api/move-tab). `dragRootEl` is whichever element should get the
  // dragging/drag-over marker classes (each skin's own root element, since their CSS differs).
  function wireGroupDrag(header, dragRootEl, t) {
    header.addEventListener("dragstart", (ev) => {
      dragTabId = t.id;
      dragWindowId = (findWindowForTab(t.id) || {}).id || null;
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", t.id);
      dragRootEl.classList.add("dragging");
      isDragging = true;
    });
    header.addEventListener("dragend", () => {
      dragRootEl.classList.remove("dragging");
      document.querySelectorAll(".drag-over").forEach((el) => el.classList.remove("drag-over"));
      isDragging = false;
      dragTabId = null;
      dragWindowId = null;
    });
    header.addEventListener("dragover", (ev) => {
      if (!dragTabId || dragTabId === t.id) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      dragRootEl.classList.add("drag-over");
    });
    header.addEventListener("dragleave", (ev) => {
      if (dragRootEl.contains(ev.relatedTarget)) return;
      dragRootEl.classList.remove("drag-over");
    });
    header.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      dragRootEl.classList.remove("drag-over");
      if (!dragTabId || dragTabId === t.id) return;
      const srcTabId = dragTabId;
      const srcWindowId = dragWindowId;
      const targetWin = findWindowForTab(t.id);
      if (!targetWin || srcWindowId !== targetWin.id) return;
      const position = (targetWin.tabs || []).findIndex((tab) => tab.id === t.id);
      if (position === -1) return;
      await postAction("/api/move-tab", { tab_id: srcTabId, window_id: srcWindowId, position });
    });
  }

  // Open a link chip's URL in the default browser. The panel is a WKWebView inside
  // iTerm2's toolbelt, where window.open on an external URL is unreliable — so route
  // through the daemon, which shells out to macOS `open` (http/https only).
  async function openLink(url) {
    try {
      const res = await fetch("/api/open-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!data.ok) toast(data.error || "open failed", false);
    } catch (e) {
      toast("open error: " + e, false);
    }
  }

  // Link chips attached to a group by the daemon (t.links): one per URL-valued property
  // of the workspace status file, labeled with the property name. A chip with a single
  // URL opens it; one with several opens a popup listing them all. Clicks don't bubble to
  // the header (which toggles collapse / drags).
  function renderLinkChips(t) {
    const wrap = document.createElement("span");
    wrap.className = "link-chips";
    for (const link of t.links || []) {
      const urls = link.urls || [];
      if (!urls.length) continue;
      const label = link.label || link.id;
      const chip = document.createElement("a");
      chip.className = "link-chip";
      chip.textContent = urls.length > 1 ? `${label} (${urls.length})` : label;
      chip.draggable = false;
      if (urls.length === 1) {
        chip.href = urls[0];
        chip.title = urls[0];
      } else {
        chip.title = `${urls.length} links`;
      }
      if (link.color) {
        chip.style.color = link.color;
        chip.style.borderColor = rgba(link.color, 0.5);
        chip.style.background = rgba(link.color, 0.12);
      }
      chip.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        if (urls.length === 1) openLink(urls[0]);
        else showLinkListPopup(chip, label, urls);
      });
      wrap.appendChild(chip);
    }
    return wrap;
  }

  // Popup listing a multi-URL chip's links; each row opens in the browser.
  function showLinkListPopup(anchor, label, urls) {
    dismissPopup();
    const popup = document.createElement("div");
    popup.className = "link-list-popup";
    popup.addEventListener("click", (ev) => ev.stopPropagation());

    const title = document.createElement("div");
    title.className = "link-list-title";
    title.textContent = label;
    popup.appendChild(title);

    for (const url of urls) {
      const item = document.createElement("a");
      item.className = "link-list-item";
      item.href = url;
      item.textContent = url.replace(/^https?:\/\//, "");
      item.title = url;
      item.draggable = false;
      item.addEventListener("click", (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        dismissPopup();
        openLink(url);
      });
      popup.appendChild(item);
    }

    document.body.appendChild(popup);
    // Anchor below the chip, clamped to the viewport's right/left edges.
    const rect = anchor.getBoundingClientRect();
    popup.style.top = rect.bottom + 4 + "px";
    const left = Math.min(rect.left, window.innerWidth - popup.offsetWidth - 8);
    popup.style.left = Math.max(8, left) + "px";
    activePopup = popup;
  }

  function renderGroup2a(t) {
    const th = theme();
    const wrap = document.createElement("div");
    wrap.className = "group";

    const effectiveColor = colorOverrides.has(t.id) ? colorOverrides.get(t.id) : (t.color ?? null);
    const effectiveCollapsed = collapsedOverrides.has(t.id) ? collapsedOverrides.get(t.id) : Boolean(t.collapsed);
    const col = effectiveColor === null || effectiveColor === undefined ? null : th.colors[effectiveColor];
    const dim = filterColor !== null && effectiveColor !== filterColor;

    wrap.style.background = col ? rgba(col, dim ? 0.02 : 0.05) : "rgba(0,0,0,.15)";
    wrap.style.boxShadow = `inset 3px 0 0 ${col ? (dim ? rgba(col, 0.25) : col) : th.none}`;

    const header = document.createElement("div");
    header.className = "group-header";
    header.draggable = true;
    wireGroupDrag(header, wrap, t);
    // A noticeably stronger fill than the body's soft tint, so the header reads as a
    // header at a glance instead of blending into its own panes.
    header.style.background = dim ? (col ? rgba(col, 0.05) : "transparent") : (col ? rgba(col, 0.24) : "#1a1d22");

    const swatch = document.createElement("span");
    swatch.className = "group-swatch";
    swatch.title = "Click to cycle group color";
    swatch.style.background = col ? (dim ? rgba(col, 0.3) : col) : "transparent";
    swatch.style.boxShadow =
      `inset 0 0 0 1px ${col ? "transparent" : th.none}, 0 0 0 4px ${col ? rgba(col, dim ? 0.06 : 0.16) : "transparent"}`;
    swatch.addEventListener("click", (ev) => { ev.stopPropagation(); cycleColor(t); });

    const name = document.createElement("span");
    name.className = "group-name";
    name.textContent = t.title;
    name.style.color = dim ? th.labelDim : (col || th.labelOn);
    name.addEventListener("click", (ev) => { ev.stopPropagation(); toggleCollapse(t); });

    const count = document.createElement("span");
    count.className = "group-count";
    count.textContent = String((t.panes || []).length);
    count.addEventListener("click", (ev) => { ev.stopPropagation(); toggleCollapse(t); });

    const caret = document.createElement("span");
    caret.className = "group-caret";
    caret.textContent = effectiveCollapsed ? "▶" : "▼";
    caret.addEventListener("click", (ev) => { ev.stopPropagation(); toggleCollapse(t); });

    const editBtn = document.createElement("span");
    editBtn.className = "group-edit-btn";
    editBtn.textContent = "✎";
    editBtn.title = "Rename tab";
    editBtn.addEventListener("click", (ev) => { ev.stopPropagation(); startGroupEdit(header, t); });

    header.append(name);
    if ((t.links || []).length) header.append(renderLinkChips(t));
    header.append(count, caret, editBtn, swatch);
    wrap.appendChild(header);

    if (!effectiveCollapsed) {
      for (const p of t.panes || []) {
        wrap.appendChild(renderPaneRow2a(p, col, dim));
      }
    }
    return wrap;
  }

  function renderPaneRow2a(p, groupColorHex, dim) {
    const th = theme();
    const row = document.createElement("div");
    const active = isPaneActive(p);
    row.className = "pane-row" + (active ? " active" : "");

    const isClaude = isClaudePane(p);

    const tooltipParts = [];
    if (p.job) tooltipParts.push(`job: ${p.job}`);
    if (p.cwd) tooltipParts.push(`cwd: ${p.cwd}`);
    if (p.last_line) tooltipParts.push(p.last_line);
    if (tooltipParts.length) row.title = tooltipParts.join("\n");

    // The active pane needs to read as clearly "selected" against its idle siblings —
    // a much bolder fill + ring than the idle tint, same idea as the group-header fix.
    row.style.background = dim
      ? "transparent"
      : (active ? rgba(groupColorHex || ACTIVE_FALLBACK, 0.32) : (groupColorHex ? rgba(groupColorHex, 0.06) : "transparent"));
    row.style.boxShadow = active && !dim ? `inset 0 0 0 2px ${rgba(groupColorHex || ACTIVE_FALLBACK, 0.8)}` : "none";

    const marker = document.createElement("span");
    marker.className = "pane-dot";
    marker.style.background = "#5a616b";
    marker.style.boxShadow = "0 0 0 5px rgba(90,97,107,0.16)";

    const main = document.createElement("div");
    main.className = "pane-main";

    const title = document.createElement("span");
    title.className = "pane-title";
    title.textContent = p.session_name || p.title || p.id;
    title.style.color = dim ? th.labelDim : (isClaude ? CLAUDE_YELLOW : (active ? th.titleOn : th.titleDim));

    const path = document.createElement("span");
    path.className = "pane-path";
    path.textContent = p.cwd || p.title || "";
    if (active && p.cwd) {
      path.title = "Click to copy";
      path.addEventListener("click", (ev) => {
        ev.stopPropagation();
        copyToClipboard(p.cwd).then((ok) => toast(ok ? "copied cwd" : "copy failed", ok));
      });
    }

    main.append(title, path);

    const closeBtn = document.createElement("span");
    closeBtn.className = "pane-close-btn";
    closeBtn.textContent = "×";
    closeBtn.title = "Close session";
    closeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showConfirmPopup(closeBtn, () => postAction("/api/close-session", { id: p.id }));
    });

    const children = [marker, main];
    if (!isClaude) {
      const status = document.createElement("span");
      status.className = "pane-status";
      status.textContent = "idle";
      status.style.color = "#6b727c";
      children.push(status);
    }
    children.push(closeBtn);
    row.append(...children);
    row.addEventListener("click", () => focusNode(p.kind, p.id));
    return row;
  }

  // "1a" — the classic terminal-styled theme: flat accent-bar groups (no rounded block, no
  // background tint), a text-glyph marker instead of a colored dot, and no pane-count. Shares
  // the same state/behavior helpers as the 2a skin (cycleColor, toggleCollapse, startGroupEdit,
  // wireGroupDrag) —
  // only presentation differs.
  function renderGroup1a(t) {
    const th = theme();
    const wrap = document.createElement("div");
    wrap.className = "group-classic";

    const effectiveColor = colorOverrides.has(t.id) ? colorOverrides.get(t.id) : (t.color ?? null);
    const effectiveCollapsed = collapsedOverrides.has(t.id) ? collapsedOverrides.get(t.id) : Boolean(t.collapsed);
    const col = effectiveColor === null || effectiveColor === undefined ? null : th.colors[effectiveColor];
    const dim = filterColor !== null && effectiveColor !== filterColor;

    wrap.style.borderLeft = `3px solid ${col ? (dim ? rgba(col, 0.25) : col) : th.none}`;

    const header = document.createElement("div");
    header.className = "group-classic-header";
    header.draggable = true;
    wireGroupDrag(header, wrap, t);
    // 1a is otherwise flat (no row backgrounds at all) — give the header a fill anyway,
    // so it still reads as a header at a glance instead of blending into its own panes.
    header.style.background = dim ? "transparent" : (col ? rgba(col, 0.3) : "rgba(255,255,255,.07)");

    const caret = document.createElement("span");
    caret.className = "group-caret-classic";
    caret.textContent = effectiveCollapsed ? "▸" : "▾";
    caret.addEventListener("click", (ev) => { ev.stopPropagation(); toggleCollapse(t); });

    const name = document.createElement("span");
    name.className = "group-name";
    name.textContent = t.title;
    name.style.color = dim ? th.labelDim : (col || th.labelOn);
    name.addEventListener("click", (ev) => { ev.stopPropagation(); toggleCollapse(t); });

    const swatch = document.createElement("span");
    swatch.className = "group-swatch-classic";
    swatch.title = "Click to cycle group color";
    swatch.style.background = col ? (dim ? rgba(col, 0.3) : col) : "transparent";
    swatch.style.boxShadow = `inset 0 0 0 1px ${col ? "transparent" : th.none}`;
    swatch.addEventListener("click", (ev) => { ev.stopPropagation(); cycleColor(t); });

    const editBtn = document.createElement("span");
    editBtn.className = "group-edit-btn";
    editBtn.textContent = "✎";
    editBtn.title = "Rename tab";
    editBtn.addEventListener("click", (ev) => { ev.stopPropagation(); startGroupEdit(header, t); });

    header.append(caret, name);
    if ((t.links || []).length) header.append(renderLinkChips(t));
    header.append(editBtn, swatch);
    wrap.appendChild(header);

    if (!effectiveCollapsed) {
      for (const p of t.panes || []) {
        wrap.appendChild(renderPaneRow1a(p, col, dim));
      }
    }
    return wrap;
  }

  function renderPaneRow1a(p, groupColorHex, dim) {
    const th = theme();
    const row = document.createElement("div");
    const active = isPaneActive(p);
    row.className = "pane-row-classic" + (active ? " active" : "");

    const isClaude = isClaudePane(p);

    const tooltipParts = [];
    if (p.job) tooltipParts.push(`job: ${p.job}`);
    if (p.cwd) tooltipParts.push(`cwd: ${p.cwd}`);
    if (p.last_line) tooltipParts.push(p.last_line);
    if (tooltipParts.length) row.title = tooltipParts.join("\n");

    // Same idea as the group-header fix: the active pane needs a bold fill + a full-width
    // accent bar (matching the group spine's weight), not just a faint tint bump.
    row.style.background = dim
      ? "transparent"
      : (active ? rgba(groupColorHex || ACTIVE_FALLBACK, 0.34) : (groupColorHex ? rgba(groupColorHex, 0.06) : "transparent"));
    row.style.borderLeft = `3px solid ${dim ? "transparent" : (active ? (groupColorHex || ACTIVE_FALLBACK) : "transparent")}`;

    const mark = document.createElement("span");
    mark.className = "pane-mark";
    mark.textContent = "·";
    mark.style.color = th.none;

    const title = document.createElement("span");
    title.className = "pane-title-classic";
    title.textContent = p.session_name || p.title || p.id;
    title.style.color = dim ? th.labelDim : (isClaude ? CLAUDE_YELLOW : (active ? th.titleOn : th.titleDim));

    const path = document.createElement("span");
    path.className = "pane-path-classic";
    path.textContent = p.cwd || p.title || "";
    if (active && p.cwd) {
      path.title = "Click to copy";
      path.addEventListener("click", (ev) => {
        ev.stopPropagation();
        copyToClipboard(p.cwd).then((ok) => toast(ok ? "copied cwd" : "copy failed", ok));
      });
    }

    const closeBtn = document.createElement("span");
    closeBtn.className = "pane-close-btn";
    closeBtn.textContent = "×";
    closeBtn.title = "Close session";
    closeBtn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      showConfirmPopup(closeBtn, () => postAction("/api/close-session", { id: p.id }));
    });

    row.append(mark, title, path, closeBtn);
    row.addEventListener("click", () => focusNode(p.kind, p.id));
    return row;
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

  let activePopup = null;
  function dismissPopup() {
    if (activePopup) { activePopup.remove(); activePopup = null; }
    setActiveNav(null);
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

  async function focusNode(kind, id) {
    if (kind === "session") {
      // Optimistic: highlight the clicked pane immediately instead of waiting for the
      // POST to round-trip through iTerm2 and then for a poll tick (up to ~500ms) to
      // notice. Cleared once a poll confirms the server agrees, or on failure below.
      activeOverride = id;
      if (lastSnapshot) renderTree(lastSnapshot);
    }
    try {
      const res = await fetch("/api/focus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, id }),
      });
      const data = await res.json();
      if (!data.ok) {
        toast(data.error || "focus failed", false);
        activeOverride = null;
        if (lastSnapshot) renderTree(lastSnapshot);
        return;
      }
    } catch (e) {
      toast("focus error: " + e, false);
      activeOverride = null;
      if (lastSnapshot) renderTree(lastSnapshot);
      return;
    }
    pollOnce(); // don't wait for the next scheduled tick to confirm/reconcile
  }

  function activeSessionId() {
    if (activeOverride !== null) return activeOverride;
    for (const w of lastSnapshot?.windows || []) {
      for (const t of w.tabs || []) {
        for (const p of t.panes || []) {
          if (p.active) return p.id;
        }
      }
    }
    return null;
  }

  const NAV_BTN_IDS = ["btn-cheatsheet", "btn-claude", "btn-settings", "btn-restore"];
  function setActiveNav(id) {
    for (const btnId of NAV_BTN_IDS) {
      document.getElementById(btnId).classList.toggle("active", btnId === id);
    }
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
    setActiveNav("btn-cheatsheet");
    await openModal("iTerm2 cheatsheet", async (body) => {
      if (itermCheatsheetCache === null) {
        const res = await fetch("/static/iterm_cheatsheet.html", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        itermCheatsheetCache = await res.text();
      }
      body.innerHTML = itermCheatsheetCache;
    });
  }

  let claudeCheatsheetCache = null;

  async function openClaudeCheatsheet() {
    setActiveNav("btn-claude");
    await openModal("Claude Code cheatsheet", async (body) => {
      if (claudeCheatsheetCache === null) {
        const res = await fetch("/static/claude_cheatsheet.html", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        claudeCheatsheetCache = await res.text();
      }
      body.innerHTML = claudeCheatsheetCache;
    });
  }

  const THEME_CHOICES = [["2a", "Modern"], ["1a", "Classic"]];

  function applyTheme(id) {
    currentTheme = id;
    document.body.dataset.theme = id;
    postAction("/api/settings", { theme: id });
    if (lastSnapshot) renderTree(lastSnapshot);
  }

  async function openSettings() {
    setActiveNav("btn-settings");
    await openModal("Settings", async (body) => {
      const res = await fetch("/api/about", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const dl = document.createElement("dl");
      dl.className = "settings-list";

      const addRow = (term, valueEl) => {
        const dt = document.createElement("dt");
        dt.textContent = term;
        const dd = document.createElement("dd");
        if (typeof valueEl === "string") dd.textContent = valueEl;
        else dd.appendChild(valueEl);
        dl.append(dt, dd);
      };

      addRow("Version", data.version || "(unknown)");

      const themeRow = document.createElement("div");
      themeRow.className = "settings-theme-row";
      for (const [id, label] of THEME_CHOICES) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "settings-theme-btn" + (currentTheme === id ? " active" : "");
        btn.textContent = label;
        btn.addEventListener("click", () => {
          applyTheme(id);
          themeRow.querySelectorAll(".settings-theme-btn").forEach((b) => b.classList.toggle("active", b === btn));
        });
        themeRow.appendChild(btn);
      }
      addRow("Theme", themeRow);

      body.innerHTML = "";
      body.appendChild(dl);
    });
  }

  document.getElementById("btn-cheatsheet").addEventListener("click", (ev) => {
    ev.stopPropagation();
    openItermCheatsheet();
  });

  document.getElementById("btn-claude").addEventListener("click", (ev) => {
    ev.stopPropagation();
    openClaudeCheatsheet();
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
      toast(`restored ${n} pane${n === 1 ? "" : "s"}`);
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
      if ((w.tabs || []).some((t) => t.id === tabId)) return w;
    }
    return null;
  }

  // True once `snapshot` itself reports `sessionId` as active — i.e. the real focus
  // change has landed, and the optimistic override in focusNode() can stand down.
  function snapshotConfirmsActive(snapshot, sessionId) {
    for (const w of snapshot.windows || []) {
      for (const t of w.tabs || []) {
        for (const p of t.panes || []) {
          if (p.id === sessionId) return Boolean(p.active);
        }
      }
    }
    return false;
  }

  async function pollOnce() {
    try {
      const res = await fetch("/api/tree", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      consecutiveFailures = 0;
      setStatus("live", "ok");
      if (activeOverride !== null && snapshotConfirmsActive(data, activeOverride)) {
        activeOverride = null;
      }
      const json = JSON.stringify(data);
      if (json !== lastSnapshotJson) {
        lastSnapshotJson = json;
        lastSnapshot = data;
        if (!document.querySelector(".group-edit-input") && !isDragging) {
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

  async function loadInitialTheme() {
    try {
      const res = await fetch("/api/settings", { cache: "no-store" });
      if (!res.ok) return;
      const data = await res.json();
      if (data.theme) {
        currentTheme = data.theme;
        document.body.dataset.theme = currentTheme;
      }
    } catch (_) { /* keep the default theme */ }
  }

  // Load the saved theme before the first poll so the panel never flashes the wrong skin.
  loadInitialTheme().then(() => {
    setStatus("connecting…");
    pollOnce();
    setInterval(pollOnce, 500);
  });
})();
