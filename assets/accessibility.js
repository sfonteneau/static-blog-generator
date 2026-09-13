(function(){
  const root = document.documentElement;
  const isFrench = (root.lang || "fr").toLowerCase().startsWith("fr");

  function syncThemeButton(){
    const btn = document.querySelector(".theme-toggle");
    if(!btn) return;
    const current = root.getAttribute("data-theme") || "dark";
    const dark = current === "dark";
    btn.setAttribute("aria-pressed", dark ? "true" : "false");
    const label = btn.querySelector(".theme-toggle__label");
    if(label){
      label.textContent = isFrench ? "Mode sombre" : "Dark mode";
    }
  }

  function syncMenuButton(){
    const btn = document.querySelector(".menu-toggle");
    if(!btn) return;
    const open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-label", open ? (isFrench ? "Fermer le menu" : "Close menu") : (isFrench ? "Ouvrir le menu" : "Open menu"));
  }

  function init(){
    syncThemeButton();
    syncMenuButton();

    const themeObserver = new MutationObserver(syncThemeButton);
    themeObserver.observe(root, {attributes:true, attributeFilter:["data-theme"]});

    const menuBtn = document.querySelector(".menu-toggle");
    if(menuBtn){
      const menuObserver = new MutationObserver(syncMenuButton);
      menuObserver.observe(menuBtn, {attributes:true, attributeFilter:["aria-expanded"]});
    }
  }

  document.addEventListener("keydown", function(e){
    if(e.key !== "Escape") return;
    const nav = document.querySelector(".nav.is-open");
    const btn = document.querySelector(".menu-toggle");
    if(nav && btn){
      requestAnimationFrame(function(){ btn.focus(); });
    }
  }, true);

  if(document.readyState === "loading"){
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
