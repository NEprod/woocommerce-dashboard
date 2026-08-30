(function () {
  "use strict";

  document.querySelectorAll("[data-woo-test-form]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      var button = form.querySelector("button[type='submit']");
      if (button) {
        button.disabled = true;
        button.setAttribute("aria-disabled", "true");
        button.textContent = "Testing…";
      }
    });
  });
}());
