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
    const text = (el.textContent || "").toLowerCase();
    return (
      text.includes("settings panel") ||
      text.includes("confirm") ||
      text.includes("reset")
    );
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
    { label: "清空对话",     icon: "🗑️" },
    { label: "清空全部会话", icon: "⚠️" },
    { label: "历史摘要",     icon: "📜" },
    { label: "会话列表",     icon: "📋" },
    { label: "重载 Prompts", icon: "🔄" },
    { label: "重载 Skills",  icon: "🔧" },
    { label: "查看记忆",     icon: "🧠" },
  ];

  /* ── Find & click last visible Chainlit action button ──────── */
  function clickChainlitAction(label) {
    const allBtns = Array.from(document.querySelectorAll("button"));
    // Filter to buttons whose text matches and are visible in DOM
    const matches = allBtns.filter(
      (b) => b.textContent.trim() === label && b.offsetParent !== null
    );
    if (matches.length) {
      matches[matches.length - 1].click();
      showToast("✓ " + label);
    } else {
      showToast("⚠️ 未找到操作按钮，请先发送一条消息");
    }
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
    `;

    return div;
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
        clickChainlitAction(btn.getAttribute("data-label"));
      });
    });

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
