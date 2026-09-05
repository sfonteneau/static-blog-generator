(function(){
  const STORAGE_KEY = "theme";
  const root = document.documentElement;
  root.classList.add("js");

  function apply(theme){
    if(theme === "light" || theme === "dark"){
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
  }

  // Initial: saved preference, else system preference
  const saved = localStorage.getItem(STORAGE_KEY);
  if(saved){
    apply(saved);
  } else if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches){
    apply("light");
  } else {
    apply("dark");
  }

  function toggle(){
    const current = root.getAttribute("data-theme") || "dark";
    const next = current === "dark" ? "light" : "dark";
    apply(next);
    localStorage.setItem(STORAGE_KEY, next);
  }

  document.addEventListener("click", function(e){
    const btn = e.target.closest && e.target.closest(".theme-toggle");
    if(btn) toggle();
  });


function initMenu(){
  const header = document.querySelector(".site-header");
  if(!header) return;
  const nav = header.querySelector(".nav");
  if(!nav) return;

  // Ensure the nav has an id so aria-controls works
  if(!nav.id) nav.id = "site-nav";

  // Find a spot for the toggle button
  const headerRight = header.querySelector(".header-right") || header.querySelector(".container") || header;

  let btn = header.querySelector(".menu-toggle");
  if(!btn){
    btn = document.createElement("button");
    btn.type = "button";
    btn.className = "menu-toggle";
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-controls", nav.id);
    btn.innerHTML = '<span aria-hidden="true">☰</span><span>Menu</span>';
    // Put it at the start of the right-side controls
    if(headerRight.firstChild){
      headerRight.insertBefore(btn, headerRight.firstChild);
    } else {
      headerRight.appendChild(btn);
    }
  } else {
    btn.setAttribute("aria-controls", nav.id);
  }

  const syncHeaderHeight = () => {
    // Keep the overlay menu aligned just under the sticky header row
    const h = header.offsetHeight || 64;
    root.style.setProperty("--header-h", h + "px");
  };

  const close = () => {
    nav.classList.remove("is-open");
    btn.setAttribute("aria-expanded", "false");
    document.body.classList.remove("menu-open");
  };
  const toggle = () => {
    syncHeaderHeight();
    const open = nav.classList.toggle("is-open");
    btn.setAttribute("aria-expanded", open ? "true" : "false");
    document.body.classList.toggle("menu-open", open);

    // If the user is scrolled, ensure the menu's top is visible
    if(open){
      const top = header.getBoundingClientRect().top;
      if(top < 0){
        window.scrollTo({ top: window.scrollY + top, behavior: "auto" });
      }
    }
  };

  syncHeaderHeight();

  btn.addEventListener("click", toggle);

  document.addEventListener("keydown", (e) => {
    if(e.key === "Escape") close();
  });

  // Close on navigation click (mobile UX)
  nav.addEventListener("click", (e) => {
    const a = e.target.closest("a");
    if(a) close();
  });

  // If we go back to desktop, ensure nav is open (no collapsed state)
  const mq = window.matchMedia("(min-width: 761px)");
  const onChange = (e) => { if(e.matches) close(); };
  if(mq.addEventListener) mq.addEventListener("change", onChange);
  else mq.addListener(onChange);
}

  function initLangMenu(){
  const menu = document.querySelector(".lang-menu");
  if(!menu) return;

  // Close when clicking outside
  document.addEventListener("click", function(e){
    if(menu.open && !menu.contains(e.target)){
      menu.removeAttribute("open");
    }
  });

  // Close with Escape
  document.addEventListener("keydown", function(e){
    if(e.key === "Escape" && menu.open){
      menu.removeAttribute("open");
      const summary = menu.querySelector("summary");
      if(summary) summary.focus();
    }
  });
}

  document.addEventListener("DOMContentLoaded", function(){
    initMenu();
    initLangMenu();
  });
})();