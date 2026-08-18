(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const navigation = document.getElementById("appNavigation");
    if (navigation) {
      navigation.querySelectorAll("a[href]").forEach(function (link) {
        link.addEventListener("click", function () {
          const instance = window.bootstrap
            ? window.bootstrap.Offcanvas.getInstance(navigation)
            : null;
          if (instance) instance.hide();
        });
      });
    }

    const toggle = document.querySelector("[data-sidebar-toggle]");
    const frame = document.querySelector(".app-frame");
    if (!toggle || !frame) return;
    if (window.localStorage.getItem("dashboard-sidebar-collapsed") === "true") {
      frame.classList.add("is-sidebar-collapsed");
    }
    function syncToggle() {
      const expanded = !frame.classList.contains("is-sidebar-collapsed");
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.setAttribute("aria-label", expanded ? "Collapse navigation" : "Expand navigation");
    }
    syncToggle();
    toggle.addEventListener("click", function () {
      frame.classList.toggle("is-sidebar-collapsed");
      window.localStorage.setItem("dashboard-sidebar-collapsed", String(frame.classList.contains("is-sidebar-collapsed")));
      syncToggle();
    });
  });
})();
