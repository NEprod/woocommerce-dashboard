(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-catalogue-thumbnail]").forEach(function (frame) {
      const image = frame.querySelector("img");
      if (!image) return;
      function loaded() { frame.classList.add("has-image"); }
      function failed() { image.remove(); frame.classList.remove("has-image"); }
      image.addEventListener("load", loaded);
      image.addEventListener("error", failed);
      if (image.complete) {
        if (image.naturalWidth) loaded();
        else failed();
      }
    });

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

    const more = document.querySelector("[aria-controls=\"appNavigation\"]");
    if (more && navigation) {
      navigation.addEventListener("shown.bs.offcanvas", function () { more.setAttribute("aria-expanded", "true"); });
      navigation.addEventListener("hidden.bs.offcanvas", function () { more.setAttribute("aria-expanded", "false"); });
    }
    if (more && !document.querySelector(".mobile-bottom-nav a[aria-current=\"page\"]")) more.classList.add("is-active");
    document.querySelectorAll(".sidebar-navigation, .app-offcanvas .offcanvas-body").forEach(function (menu) {
      const groups = [...menu.querySelectorAll("[data-navigation-group]")];
      groups.forEach(function (group) {
        group.addEventListener("toggle", function () {
          if (group.open) groups.forEach(function (other) { if (other !== group) other.open = false; });
        });
      });
    });
    const toggle = document.querySelector("[data-sidebar-toggle]");
    const frame = document.querySelector(".app-frame");
    if (!toggle || !frame) return;
    let collapsed = false;
    try { collapsed = window.localStorage.getItem("dashboard-sidebar-collapsed") === "true"; } catch (_) {}
    if (collapsed) {
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
      try { window.localStorage.setItem("dashboard-sidebar-collapsed", String(frame.classList.contains("is-sidebar-collapsed"))); } catch (_) {}
      syncToggle();
    });
    document.querySelectorAll(".app-sidebar .sidebar-group > summary").forEach(function (summary) {
      summary.addEventListener("click", function () {
        if (frame.classList.contains("is-sidebar-collapsed")) {
          frame.classList.remove("is-sidebar-collapsed");
          try { window.localStorage.setItem("dashboard-sidebar-collapsed", "false"); } catch (_) {}
          syncToggle();
        }
      });
    });
  });
})();
