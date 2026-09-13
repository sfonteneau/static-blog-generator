(function(){
  const STORAGE_KEY = "theme";
  const root = document.documentElement;
  const isFrench = (root.lang || "fr").toLowerCase().startsWith("fr");
  root.classList.add("js");

  function themeButton(){
    return document.querySelector(".theme-toggle");
  }

  function syncThemeButton(){
    const btn = themeButton();
    if(!btn) return;
    const current = root.getAttribute("data-theme") || "dark";
    const dark = current === "dark";
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
    const label = btn.querySelector(".theme-toggle__label");
    if(label) label.textContent = isFrench ? "Mode sombre" : "Dark mode";
    const icon = btn.querySelector(".theme-toggle__icon");
    if(icon) icon.textContent = dark ? "🌙" : "☀️";
  }

  function apply(theme){
    if(theme === "light" || theme === "dark"){
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    syncThemeButton();
  }

  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch (_) {}
  if(saved){
    apply(saved);
  } else if(window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches){
    apply("light");
  } else {
    apply("dark");
  }

  function toggleTheme(){
    const current = root.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    apply(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
  }

  document.addEventListener("click", function(e){
    const btn = e.target.closest && e.target.closest(".theme-toggle");
    if(btn) toggleTheme();
  });

  function initMenu(){
    const header = document.querySelector(".site-header");
    if(!header) return;
    const nav = header.querySelector(".nav");
    if(!nav) return;

    if(!nav.id) nav.id = "site-nav";
    const headerRight = header.querySelector(".header-right") || header.querySelector(".container") || header;
    const mq = window.matchMedia("(min-width: 821px)");

    let btn = header.querySelector(".menu-toggle");
    if(!btn){
      btn = document.createElement("button");
      btn.type = "button";
      btn.className = "menu-toggle";
      btn.setAttribute("aria-expanded", "false");
      btn.setAttribute("aria-controls", nav.id);
      btn.innerHTML = '<span aria-hidden="true">☰</span><span class="menu-toggle__label"></span>';
      if(headerRight.firstChild){
        headerRight.insertBefore(btn, headerRight.firstChild);
      } else {
        headerRight.appendChild(btn);
      }
    } else {
      btn.setAttribute("aria-controls", nav.id);
    }

    function syncMenuButton(open){
      btn.setAttribute("aria-expanded", open ? "true" : "false");
      btn.setAttribute("aria-label", open ? (isFrench ? "Fermer le menu" : "Close menu") : (isFrench ? "Ouvrir le menu" : "Open menu"));
      const label = btn.querySelector(".menu-toggle__label");
      if(label) label.textContent = open ? (isFrench ? "Fermer" : "Close") : "Menu";
    }

    const syncHeaderHeight = () => {
      const h = header.offsetHeight || 64;
      root.style.setProperty("--header-h", h + "px");
    };

    const close = (restoreFocus) => {
      const wasOpen = nav.classList.contains("is-open");
      nav.classList.remove("is-open");
      document.body.classList.remove("menu-open");
      syncMenuButton(false);
      if(restoreFocus && wasOpen) btn.focus();
    };

    const toggle = () => {
      syncHeaderHeight();
      const open = nav.classList.toggle("is-open");
      document.body.classList.toggle("menu-open", open);
      syncMenuButton(open);

      if(open){
        const top = header.getBoundingClientRect().top;
        if(top < 0){
          window.scrollTo({top: window.scrollY + top, behavior: "auto"});
        }
      }
    };

    syncHeaderHeight();
    syncMenuButton(false);
    btn.addEventListener("click", toggle);

    document.addEventListener("keydown", (e) => {
      if(e.key === "Escape") close(true);
    });

    nav.addEventListener("click", (e) => {
      const a = e.target.closest("a");
      if(a) close(false);
    });

    const onChange = (e) => { if(e.matches) close(false); };
    if(mq.addEventListener) mq.addEventListener("change", onChange);
    else mq.addListener(onChange);
  }

  function initLangMenu(){
    const menu = document.querySelector(".lang-menu");
    if(!menu) return;

    document.addEventListener("click", function(e){
      if(menu.open && !menu.contains(e.target)){
        menu.removeAttribute("open");
      }
    });

    document.addEventListener("keydown", function(e){
      if(e.key === "Escape" && menu.open){
        menu.removeAttribute("open");
        const summary = menu.querySelector("summary");
        if(summary) summary.focus();
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function(){
    syncThemeButton();
    initMenu();
    initLangMenu();
  });
})();
