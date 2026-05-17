/*
 AgentA custom JavaScript:
  1. Forces the Chainlit Settings panel to open from the LEFT side.
  2. Injects a persistent left sidebar with quick-action buttons.
*/

/* ═══════════════════════════════════════════════════════════════
   Part 1 — Force settings panel to left side
═══════════════════════════════════════════════════════════════ */
(function () {
  function isSettingsPanel(el) {
    if (!el || !(el instanceof HTMLElement)) return false;
    // Only match Chainlit's settings drawer — NOT other dialogs like
    // "Create New Chat" which also contains "confirm" / "reset" text.
    const text = (el.textContent || "").toLowerCase();
    return text.includes("settings panel");
  }

  function forceLeft(el) {
    el.style.setProperty("left", "0", "important");
    el.style.setProperty("right", "auto", "important");
    el.style.setProperty("transform", "translateX(0)", "important");
    el.style.setProperty("border-right", "1px solid hsl(var(--border))", "important");
    el.style.setProperty("border-left", "none", "important");
  }

  function patchSettingsPanel() {
    const candidates = Array.from(document.querySelectorAll('[role="dialog"], [data-side], .fixed'));
    for (const el of candidates) {
      if (!isSettingsPanel(el)) continue;
      forceLeft(el);
      if (el.parentElement instanceof HTMLElement) {
        forceLeft(el.parentElement);
      }
    }
  }

  const observer = new MutationObserver(() => patchSettingsPanel());
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true });

  patchSettingsPanel();
  setInterval(patchSettingsPanel, 500);
})();

/* ═══════════════════════════════════════════════════════════════
   Part 2 — Left sidebar with quick-action buttons
═══════════════════════════════════════════════════════════════ */
(function () {
  const SIDEBAR_W_EXPANDED  = 220;
  const SIDEBAR_W_COLLAPSED = 52;
  const STORAGE_KEY = "agenta-sidebar-collapsed";

  const ACTIONS = [
    { label: "清空对话",     icon: "🗑️", cmd: "/clear" },
    { label: "清空全部会话", icon: "⚠️", cmd: "/clean-session yes" },
    { label: "历史摘要",     icon: "📜", cmd: "/history" },
    { label: "会话列表",     icon: "📋", cmd: "/session" },
    { label: "重载 Prompts", icon: "🔄", cmd: "/reload-prompts" },
    { label: "重载 Skills",  icon: "🔧", cmd: "/reload-skills" },
    { label: "查看记忆",     icon: "🧠", cmd: "/memory" },
  ];

  /* ── Submit a slash-command via the chat input ──────────────── */
  function sendCommand(cmd, label) {
    const textarea = document.querySelector("textarea");
    if (!textarea) {
      showToast("⚠️ 未找到输入框");
      return;
    }
    // Use the native HTMLTextAreaElement setter so React detects the change
    const nativeSetter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value"
    ).set;
    nativeSetter.call(textarea, cmd);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.focus();

    // Give React one tick to sync state, then press Enter to submit
    setTimeout(() => {
      textarea.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", keyCode: 13, bubbles: true, cancelable: true })
      );
      showToast("▶ " + label);
    }, 30);
  }

  /* ── Toast notification ─────────────────────────────────────── */
  let toastTimer = null;
  function showToast(msg) {
    let toast = document.getElementById("agenta-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "agenta-toast";
      toast.className = "agenta-toast";
      document.body.appendChild(toast);
    }
    toast.textContent = msg;
    toast.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove("show"), 2000);
  }

  /* ── Build sidebar HTML ─────────────────────────────────────── */
  function buildSidebar() {
    const div = document.createElement("div");
    div.id = "agenta-sidebar";

    div.innerHTML = `
      <div class="agenta-sb-header">
        <div class="agenta-sb-logo">
          <span class="agenta-sb-logo-icon">⚡</span>
          <span class="agenta-sb-title">AgentA</span>
        </div>
        <button class="agenta-sb-toggle" id="agenta-sb-toggle" title="折叠 / 展开">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.5"
               stroke-linecap="round" stroke-linejoin="round">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>
      </div>

      <div class="agenta-sb-section-label">快捷操作</div>

      <nav class="agenta-sb-nav">
        ${ACTIONS.map(
          (a) => `
          <button class="agenta-btn" data-label="${a.label}" title="${a.label}">
            <span class="agenta-btn-icon">${a.icon}</span>
            <span class="agenta-btn-label">${a.label}</span>
          </button>`
        ).join("")}
      </nav>

      <div class="agenta-sb-divider"></div>
      <div class="agenta-sb-section-label agenta-recents-label">
        <span>Recents</span>
      </div>
      <nav class="agenta-sb-nav agenta-recents-nav" id="agenta-recents-nav">
        <span class="agenta-recents-empty">加载中…</span>
      </nav>
    `;

    return div;
  }

  /* ── Recents: fetch sessions from backend ───────────────────── */
  let _activeSessionId = null;   // tracks which session is currently active

  function _timeAgo(isoStr) {
    if (!isoStr) return "";
    const d = new Date(isoStr.replace(" ", "T"));
    if (isNaN(d)) return isoStr.slice(0, 10);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60)   return "刚刚";
    if (diff < 3600) return Math.floor(diff / 60) + " 分钟前";
    if (diff < 86400) return Math.floor(diff / 3600) + " 小时前";
    return Math.floor(diff / 86400) + " 天前";
  }

  function renderRecents(sessions) {
    const nav = document.getElementById("agenta-recents-nav");
    if (!nav) return;
    if (!sessions.length) {
      nav.innerHTML = '<span class="agenta-recents-empty">暂无历史会话</span>';
      return;
    }
    nav.innerHTML = sessions.map((s) => {
      const title = s.first_user_msg
        ? s.first_user_msg.slice(0, 36) + (s.first_user_msg.length > 36 ? "…" : "")
        : s.session_id.slice(0, 8) + "…";
      const time  = _timeAgo(s.created_at);
      const active = s.session_id === _activeSessionId ? " active" : "";
      return `<button class="agenta-recent-item${active}"
                      data-sid="${s.session_id}"
                      title="${s.session_id}">
        <span class="agenta-recent-title">${title}</span>
        <span class="agenta-recent-time">${time}</span>
      </button>`;
    }).join("");

    nav.querySelectorAll(".agenta-recent-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sid = btn.getAttribute("data-sid");
        _activeSessionId = sid;
        renderRecents(sessions); // re-render to update active class
        sendCommand("/session " + sid, "切换会话");
      });
    });
  }

  function fetchAndRenderRecents() {
    fetch("/api/agenta/sessions")
      .then((r) => r.json())
      .then((data) => renderRecents(Array.isArray(data) ? data : []))
      .catch(() => {
        const nav = document.getElementById("agenta-recents-nav");
        if (nav) nav.innerHTML = '<span class="agenta-recents-empty">⚠ 无法加载</span>';
      });
  }

  /* ── Apply / sync layout padding ───────────────────────────── */
  function applyLayout(collapsed) {
    const w = collapsed ? SIDEBAR_W_COLLAPSED : SIDEBAR_W_EXPANDED;
    // Target Chainlit's root container and push content right
    const targets = [
      document.getElementById("root"),
      document.querySelector("body > div:not(#agenta-sidebar)"),
    ];
    for (const el of targets) {
      if (el && el !== document.getElementById("agenta-sidebar")) {
        el.style.setProperty("padding-left", w + "px", "important");
        el.style.setProperty("box-sizing", "border-box", "important");
        break;
      }
    }
  }

  /* ── Initialise once DOM is ready ───────────────────────────── */
  function init() {
    if (document.getElementById("agenta-sidebar")) return;
    if (!document.body) return;

    const collapsed = localStorage.getItem(STORAGE_KEY) === "true";
    const sidebar = buildSidebar();
    if (collapsed) sidebar.classList.add("collapsed");
    document.body.appendChild(sidebar);

    // Toggle collapse
    document.getElementById("agenta-sb-toggle").addEventListener("click", () => {
      const isCollapsed = sidebar.classList.toggle("collapsed");
      localStorage.setItem(STORAGE_KEY, String(isCollapsed));
      applyLayout(isCollapsed);
    });

    // Action buttons
    sidebar.querySelectorAll(".agenta-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const label = btn.getAttribute("data-label");
        const action = ACTIONS.find((a) => a.label === label);
        if (action) sendCommand(action.cmd, action.label);
      });
    });

    // Load Recents immediately, then refresh every 30 s
    fetchAndRenderRecents();
    setInterval(fetchAndRenderRecents, 30_000);

    applyLayout(collapsed);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Re-attempt after React mounts
  setTimeout(init, 400);
  setTimeout(init, 1500);

  // Keep layout in sync if something resets it (e.g. hot reload)
  const layoutObserver = new MutationObserver(() => {
    const sidebar = document.getElementById("agenta-sidebar");
    if (!sidebar) {
      init();
      return;
    }
    applyLayout(sidebar.classList.contains("collapsed"));
  });
  document.addEventListener("DOMContentLoaded", () => {
    layoutObserver.observe(document.body, { childList: true });
  });
})();

/* ═══════════════════════════════════════════════════════════════
   Part 4 — Hover tooltips for the three top-right header buttons
   (Readme, Settings, Theme) — styled to match the "New Chat" tip.

   DOM observations (Chainlit 1.x):
   • Readme  → aria-label="Readme"  (or button text "Readme")
   • Settings→ NO aria-label at all  → located by adjacency
   • Theme   → aria-label or title = "Toggle theme"
═══════════════════════════════════════════════════════════════ */
(function () {
  /* ── Shared tooltip element ───────────────────────────────── */
  let tipEl = null;
  let hideTimer = null;

  function ensureTip() {
    if (tipEl) return tipEl;
    tipEl = document.createElement("div");
    tipEl.id = "agenta-hdr-tip";
    document.body.appendChild(tipEl);
    return tipEl;
  }

  function showTip(btn, text) {
    clearTimeout(hideTimer);
    const t = ensureTip();
    t.textContent = text;
    t.classList.add("visible");
    const r = btn.getBoundingClientRect();
    t.style.left = Math.round(r.left + r.width / 2) + "px";
    t.style.top  = Math.round(r.bottom + 8) + "px";
  }

  function hideTip() {
    hideTimer = setTimeout(() => {
      if (tipEl) tipEl.classList.remove("visible");
    }, 80);
  }

  /* ── Attach listeners once per button ────────────────────── */
  function patchBtn(btn, label) {
    if (btn._agentaHdrTip) return;
    btn._agentaHdrTip = label;          // store the label for adjacency pass
    btn.addEventListener("mouseenter", () => showTip(btn, label));
    btn.addEventListener("mouseleave", hideTip);
    btn.addEventListener("click",      hideTip);
  }

  /* ── Helper: any accessible name on a button ─────────────── */
  function btnName(btn) {
    return (
      btn.getAttribute("aria-label") ||
      btn.getAttribute("title") ||
      btn.textContent.trim()
    ).toLowerCase();
  }

  function scanButtons() {
    const allBtns = Array.from(document.querySelectorAll("button"))
      .filter((b) => !b.closest("#agenta-sidebar")); // skip our own sidebar

    /* Pass 1 — label / title / text matching */
    allBtns.forEach((btn) => {
      if (btn._agentaHdrTip) return;
      const name = btnName(btn);
      if (name === "readme" || name.includes("readme")) {
        patchBtn(btn, "Readme");
      } else if (
        name.includes("theme") ||
        name.includes("toggle theme") ||
        name.includes("dark mode") ||
        name.includes("light mode")
      ) {
        patchBtn(btn, "切换主题");
      } else if (name.includes("setting")) {
        patchBtn(btn, "设置");
      }
    });

    /* Pass 2 — positional fallback for Settings
       The Settings button has no accessible name in Chainlit 1.x.
       It sits immediately between the Readme button and the
       Toggle theme button in document order. */
    const readmeIdx = allBtns.findIndex(
      (b) => b._agentaHdrTip === "Readme"
    );
    const themeIdx = allBtns.findIndex(
      (b) => b._agentaHdrTip === "切换主题"
    );
    if (readmeIdx !== -1 && themeIdx !== -1 && themeIdx > readmeIdx) {
      for (let i = readmeIdx + 1; i < themeIdx; i++) {
        if (!allBtns[i]._agentaHdrTip) {
          patchBtn(allBtns[i], "设置");
        }
      }
    }
  }

  /* ── Boot ─────────────────────────────────────────────────── */
  const mo = new MutationObserver(scanButtons);

  function start() {
    scanButtons();
    mo.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
  setTimeout(scanButtons, 600);
  setTimeout(scanButtons, 2000);
})();

/* ═══════════════════════════════════════════════════════════════
   Part 5 — Settings button toggle: click again while panel is
   open to close it (equivalent to pressing Escape / Cancel).

   Detection re-uses exactly the same heuristic as Part 1:
   the settings panel contains "settings panel", "confirm", or
   "reset" text — whichever selector holds that text and is
   currently visible is considered the open panel.
═══════════════════════════════════════════════════════════════ */
(function () {
  /* ── Same heuristic as Part 1's isSettingsPanel() ─────────── */
  function looksLikeSettings(el) {
    const t = (el.textContent || "").toLowerCase();
    return (
      t.includes("settings panel") ||
      t.includes("confirm") ||
      t.includes("reset")
    );
  }

  /* ── Return the settings panel element if it is visible ────── */
  function getOpenPanel() {
    const candidates = document.querySelectorAll(
      '[role="dialog"], [data-side], .fixed'
    );
    for (const el of candidates) {
      if (!looksLikeSettings(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) return el; // visible on screen
    }
    return null;
  }

  /* ── Dismiss the panel ────────────────────────────────────── */
  function closePanel(panel) {
    // Radix Sheet / Dialog responds to native Escape on the element
    panel.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        keyCode: 27,
        bubbles: true,
        cancelable: true,
      })
    );
    // Belt-and-suspenders: also fire on document
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        keyCode: 27,
        bubbles: true,
        cancelable: true,
      })
    );
    // Last-resort: click the × button inside the panel
    setTimeout(() => {
      if (!getOpenPanel()) return; // already closed
      const x =
        panel.querySelector('[aria-label="Close"]') ||
        panel.querySelector('button[aria-label="close"]') ||
        Array.from(panel.querySelectorAll("button")).find((b) =>
          /close|cancel|取消/i.test(b.textContent + b.getAttribute("aria-label"))
        );
      if (x) x.click();
    }, 60);
  }

  /* ── Attach toggle once per settings button ────────────────── */
  function attachToggle(btn) {
    if (btn._agentaSettingsToggle) return;
    btn._agentaSettingsToggle = true;
    btn.addEventListener(
      "click",
      function (e) {
        const panel = getOpenPanel();
        if (panel) {
          // Panel visible → eat click, close panel
          e.stopImmediatePropagation();
          e.preventDefault();
          closePanel(panel);
        }
        // Panel not visible → let event reach React, which opens it
      },
      true // ← capture phase: fires before React's bubble-phase handler
    );
  }

  /* ── Locate the settings button (same position logic as Part 4) */
  function scanForSettingsBtn() {
    const allBtns = Array.from(document.querySelectorAll("button")).filter(
      (b) => !b.closest("#agenta-sidebar")
    );
    const readmeIdx = allBtns.findIndex((b) =>
      (b.getAttribute("aria-label") || b.getAttribute("title") || b.textContent)
        .toLowerCase()
        .includes("readme")
    );
    const themeIdx = allBtns.findIndex((b) =>
      (b.getAttribute("aria-label") || b.getAttribute("title") || "")
        .toLowerCase()
        .includes("theme")
    );
    if (readmeIdx !== -1 && themeIdx !== -1 && themeIdx > readmeIdx) {
      for (let i = readmeIdx + 1; i < themeIdx; i++) {
        attachToggle(allBtns[i]);
      }
    }
  }

  /* ── Boot ─────────────────────────────────────────────────── */
  const mo = new MutationObserver(scanForSettingsBtn);
  function start() {
    scanForSettingsBtn();
    mo.observe(document.body, { childList: true, subtree: true });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
  setTimeout(scanForSettingsBtn, 600);
  setTimeout(scanForSettingsBtn, 2000);
})();

/* ═══════════════════════════════════════════════════════════════
   Part 3 — Raise z-index on Chainlit dialogs so they sit above
   the custom sidebar (z-index 300).
   Positioning is handled entirely by CSS in custom.css to avoid
   fighting Tailwind / Radix inline-style reconciliation.
═══════════════════════════════════════════════════════════════ */
(function () {
  const watched = new WeakSet();

  function raiseZIndex(el) {
    if (!(el instanceof HTMLElement)) return;
    if (el.id === "agenta-sidebar") return;
    if (watched.has(el)) return;
    watched.add(el);
    el.style.setProperty("z-index", "9999", "important");
  }

  const domObserver = new MutationObserver((mutations) => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (!(node instanceof HTMLElement)) continue;
        if (node.matches('[role="dialog"], [role="alertdialog"]')) {
          raiseZIndex(node);
        }
        node
          .querySelectorAll('[role="dialog"], [role="alertdialog"]')
          .forEach(raiseZIndex);
      }
    }
  });

  function start() {
    domObserver.observe(document.body, { childList: true, subtree: true });
    document
      .querySelectorAll('[role="dialog"], [role="alertdialog"]')
      .forEach(raiseZIndex);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();

/* ═══════════════════════════════════════════════════════════════
   Part 4 — Toggle the Settings panel on repeated button clicks.
   Chainlit's settings button only opens the panel; clicking it
   again while it's open has no effect. We intercept the click in
   capture phase: if the panel is already open, stop propagation
   (prevents React's open handler) and dispatch Escape instead
   (which Radix Sheet listens for globally to close itself).
═══════════════════════════════════════════════════════════════ */
(function () {
  // Detect whether the settings Sheet/panel is currently open.
  function isSettingsOpen() {
    // Radix Sheet sets data-state="open" on the content element.
    // The settings panel also has data-side (forced left by Part 1).
    return !!(
      document.querySelector('[data-state="open"][data-side]') ||
      document.querySelector('[data-state="open"][role="dialog"][id*="setting"]') ||
      document.querySelector('[data-state="open"][aria-label*="setting" i]')
    );
  }

  // Find Chainlit's settings gear button (SettingsIcon in the header).
  function findSettingsBtn() {
    return Array.from(document.querySelectorAll("button")).find((btn) => {
      const title = (
        btn.title ||
        btn.getAttribute("aria-label") ||
        btn.getAttribute("aria-describedby") ||
        ""
      ).toLowerCase();
      // Also check any SVG <title> inside the button
      const svgTitle = (btn.querySelector("title")?.textContent || "").toLowerCase();
      return title.includes("setting") || svgTitle.includes("setting");
    });
  }

  // Close the open settings panel by pressing Escape (Radix global listener).
  function pressEscape() {
    document.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "Escape",
        code: "Escape",
        keyCode: 27,
        which: 27,
        bubbles: true,
        cancelable: true,
      })
    );
  }

  let attached = false;

  function attachToggle() {
    if (attached) return;
    const btn = findSettingsBtn();
    if (!btn) return;
    attached = true;

    // Capture phase: runs before React's delegated bubble-phase handler.
    btn.addEventListener(
      "click",
      (e) => {
        if (!isSettingsOpen()) return; // panel closed → let click open it normally
        // Panel is open → stop the click from reaching React's handler,
        // then close via Escape.
        e.stopImmediatePropagation();
        pressEscape();
      },
      true // capture
    );
  }

  // Try to attach immediately, then retry until the button is rendered.
  function tryAttach() {
    attachToggle();
    if (!attached) setTimeout(tryAttach, 500);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", tryAttach);
  } else {
    tryAttach();
  }
})();
