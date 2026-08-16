(function () {
  "use strict";
  document.addEventListener("DOMContentLoaded", function () {
    const navigation = document.getElementById("appNavigation");
    if (!navigation) return;
    navigation.querySelectorAll("a[href]").forEach(function (link) {
      link.addEventListener("click", function () {
        const instance = window.bootstrap
          ? window.bootstrap.Offcanvas.getInstance(navigation)
          : null;
        if (instance) instance.hide();
      });
    });
  });
})();
