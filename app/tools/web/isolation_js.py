"""JavaScript-изоляция целевого div на странице Coinglass."""

import json


def build_isolation_js(target_class: str, enabled: bool) -> str:
    """JS для изоляции целевого div без разрушения React-дерева, flex-вёрстки и выпадающих меню."""
    class_json = json.dumps(target_class)
    # Префикс до хэша CSS-модуля (на случай если Coinglass обновит хэш)
    prefix = target_class.split("__")[0] + "__" if "__" in target_class else target_class
    prefix_json = json.dumps(prefix)
    enabled_js = "true" if enabled else "false"

    return f"""
(function() {{
  const TARGET_CLASS = {class_json};
  const TARGET_PREFIX = {prefix_json};
  const ENABLED = {enabled_js};
  const STYLE_ID = "Volume Kit-isolation-style";

  function stopWatchers() {{
    if (window.__thObserver) {{
      try {{ window.__thObserver.disconnect(); }} catch (e) {{}}
      window.__thObserver = null;
    }}
    if (window.__thInterval) {{
      clearInterval(window.__thInterval);
      window.__thInterval = null;
    }}
  }}

  function clearIsolation() {{
    stopWatchers();
    const st = document.getElementById(STYLE_ID);
    if (st) st.remove();
    if (document.body) {{
      document.body.classList.remove("th-isolated");
    }}
    document.querySelectorAll(
      "[data-th-keep],[data-th-target],[data-th-portal],[data-th-portal-ancestor]"
    ).forEach((el) => {{
      el.removeAttribute("data-th-keep");
      el.removeAttribute("data-th-target");
      el.removeAttribute("data-th-portal");
      el.removeAttribute("data-th-portal-ancestor");
    }});
    window.dispatchEvent(new Event("resize"));
  }}

  if (!ENABLED) {{
    clearIsolation();
    return;
  }}

  function ensureStyle() {{
    let st = document.getElementById(STYLE_ID);
    if (!st) {{
      st = document.createElement("style");
      st.id = STYLE_ID;
      (document.head || document.documentElement).appendChild(st);
    }}
    st.textContent = `
      html, body {{
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        height: 100% !important;
        min-height: 100vh !important;
        overflow-x: hidden !important;
      }}
      body.th-isolated *:not([data-th-keep]):not([data-th-target]):not([data-th-target] *):not([data-th-portal]):not([data-th-portal] *):not([data-th-portal-ancestor]) {{
        display: none !important;
      }}
      body.th-isolated [data-th-keep="1"] {{
        display: block !important;
        margin: 0 !important;
        padding: 0 !important;
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        height: 100% !important;
        min-height: 100% !important;
        border: none !important;
        box-shadow: none !important;
        position: static !important;
        transform: none !important;
      }}
      body.th-isolated [data-th-target="1"] {{
        display: flex !important;
        flex-direction: column !important;
        visibility: visible !important;
        opacity: 1 !important;
        width: 100% !important;
        max-width: 100% !important;
        min-width: 0 !important;
        height: 100vh !important;
        min-height: 100vh !important;
        margin: 0 !important;
        border: none !important;
        border-radius: 0 !important;
        box-shadow: none !important;
        box-sizing: border-box !important;
      }}
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_toolbar__"],
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_subbar__"],
      body.th-isolated [data-th-target="1"] > [class*="Toolbar_toolbar__"] {{
        flex: 0 0 auto !important;
      }}
      body.th-isolated [data-th-target="1"] > [class*="ChartCard_plot__"],
      body.th-isolated [data-th-target="1"] > [class*="ChartArea_single__"] {{
        flex: 1 1 0% !important;
        min-height: 360px !important;
        width: 100% !important;
      }}
      body.th-isolated [data-th-target="1"] [class*="ChartArea_stage__"] {{
        flex: 1 1 auto !important;
        min-height: 320px !important;
      }}
    `;
  }}

  function isIgnoredBodyChild(el) {{
    if (!el || el.nodeType !== 1) return true;
    const tag = el.tagName;
    if (
      tag === "SCRIPT" ||
      tag === "STYLE" ||
      tag === "LINK" ||
      tag === "NOSCRIPT" ||
      tag === "META"
    ) {{
      return true;
    }}
    if (el.id === "__next") return true;
    const cls = typeof el.className === "string" ? el.className : "";
    const idCls = ((el.id || "") + " " + cls).toLowerCase();
    if (
      idCls.includes("termly") ||
      idCls.includes("cookie") ||
      idCls.includes("consent")
    ) {{
      return true;
    }}
    return false;
  }}

  function markPortals() {{
    // 1. Все порталы (меню настроек, списки, диалоги скриншота), смонтированные прямо в body вне #__next
    if (document.body) {{
      Array.from(document.body.children).forEach((child) => {{
        if (!isIgnoredBodyChild(child) && child.getAttribute("data-th-portal") !== "1") {{
          child.setAttribute("data-th-portal", "1");
        }}
      }});
    }}

    // 2. Всплывающие меню/подсказки MUI, AntD и графика, если они смонтированы внутри #__next
    const portalSelectors = [
      ".MuiPopover-root",
      ".MuiPopper-root",
      ".MuiModal-root",
      ".MuiTooltip-popper",
      ".ant-dropdown",
      ".ant-select-dropdown",
      ".ant-tooltip",
      ".ant-popover",
      ".ant-modal-root",
      "[data-floating-ui-portal]",
      "[data-radix-popper-content-wrapper]",
      "[role='listbox']",
      "[role='tooltip']",
      "[role='dialog']",
      "[role='menu']",
      "[aria-modal='true']"
    ];
    for (const sel of portalSelectors) {{
      document.querySelectorAll(sel).forEach((el) => {{
        if (el.closest("header, footer, aside")) return;
        if (el.getAttribute("data-th-portal") !== "1") {{
          el.setAttribute("data-th-portal", "1");
        }}
        let cur = el.parentElement;
        while (cur && cur !== document.body && cur !== document.documentElement) {{
          if (
            !cur.hasAttribute("data-th-keep") &&
            !cur.hasAttribute("data-th-target") &&
            cur.getAttribute("data-th-portal-ancestor") !== "1"
          ) {{
            cur.setAttribute("data-th-portal-ancestor", "1");
          }}
          cur = cur.parentElement;
        }}
      }});
    }}
  }}

  function findTarget() {{
    let el = document.querySelector("div." + CSS.escape(TARGET_CLASS));
    if (!el) {{
      el = document.querySelector("." + CSS.escape(TARGET_CLASS));
    }}
    if (!el && TARGET_PREFIX) {{
      el = document.querySelector("div[class*='" + TARGET_PREFIX.replace(/'/g, "") + "']");
    }}
    return el;
  }}

  function applyIsolation() {{
    if (!document.body) return false;
    const target = findTarget();
    if (!target) return false;

    ensureStyle();

    const alreadySame = target.getAttribute("data-th-target") === "1"
      && document.body.classList.contains("th-isolated");

    if (!alreadySame) {{
      document.querySelectorAll("[data-th-keep],[data-th-target]").forEach((el) => {{
        el.removeAttribute("data-th-keep");
        el.removeAttribute("data-th-target");
      }});

      target.setAttribute("data-th-target", "1");
      let cur = target.parentElement;
      while (cur && cur !== document.documentElement) {{
        cur.setAttribute("data-th-keep", "1");
        cur = cur.parentElement;
      }}
      document.body.classList.add("th-isolated");

      setTimeout(() => window.dispatchEvent(new Event("resize")), 80);
      setTimeout(() => window.dispatchEvent(new Event("resize")), 350);
      setTimeout(() => window.dispatchEvent(new Event("resize")), 900);
    }}

    markPortals();
    return true;
  }}

  stopWatchers();
  applyIsolation();

  let debounceTimer = null;
  window.__thObserver = new MutationObserver(() => {{
    markPortals();
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => applyIsolation(), 120);
  }});
  if (document.documentElement) {{
    window.__thObserver.observe(document.documentElement, {{
      childList: true,
      subtree: true
    }});
  }}

  let ticks = 0;
  window.__thInterval = setInterval(() => {{
    ticks += 1;
    applyIsolation();
    if (ticks >= 40) {{
      clearInterval(window.__thInterval);
      window.__thInterval = null;
    }}
  }}, 500);
}})();
"""
