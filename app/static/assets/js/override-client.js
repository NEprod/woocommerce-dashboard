(function (root) {
  "use strict";

  const messages = Object.freeze({
    authentication: "Your session has expired. Sign in again, then retry.",
    conflict: "Another catalogue operation is already running. Wait for it to finish, then retry.",
    missing: "The selected product or override folder is no longer available. Refresh Products and retry.",
    invalid: "The selected override folder is invalid. Choose a product folder and retry.",
    network: "The override request could not reach the server. Check the connection and retry.",
    unexpected: "The server returned an unexpected response. Refresh the page and retry.",
    destination: "The server did not return a valid editor destination. Refresh Products and retry.",
    route: "The override request could not be prepared. Refresh Products and retry.",
  });

  class OverrideClientError extends Error {
    constructor(message) {
      super(message);
      this.name = "OverrideClientError";
    }
  }

  function routeUrl(template, identifier, baseUrl) {
    const encoded = encodeURIComponent(String(identifier == null ? "" : identifier));
    if (!encoded) throw new OverrideClientError(messages.route);
    const replaced = String(template || "").replace(/\/0(?=\/|\?|#|$)/, `/${encoded}`);
    if (!template || replaced === template) {
      throw new OverrideClientError(messages.route);
    }
    try {
      return new URL(replaced, baseUrl).href;
    } catch (_) {
      throw new OverrideClientError(messages.route);
    }
  }

  function statusMessage(response) {
    if (response.redirected || response.status === 401) return messages.authentication;
    if (response.status === 404) return messages.missing;
    if (response.status === 409) return messages.conflict;
    if (response.status === 400) return messages.invalid;
    return null;
  }

  async function readJson(response) {
    const mapped = statusMessage(response);
    if (mapped) throw new OverrideClientError(mapped);

    const contentType = response.headers && response.headers.get
      ? (response.headers.get("content-type") || "").toLowerCase()
      : "";
    if (!contentType.includes("application/json")) {
      throw new OverrideClientError(messages.unexpected);
    }

    let output;
    try {
      output = await response.json();
    } catch (_) {
      throw new OverrideClientError(messages.unexpected);
    }
    if (!output || typeof output !== "object" || Array.isArray(output)) {
      throw new OverrideClientError(messages.unexpected);
    }
    if (!response.ok || output.error) {
      throw new OverrideClientError(messages.unexpected);
    }
    return output;
  }

  async function requestJson(fetcher, url, options) {
    let response;
    try {
      response = await fetcher(url, options);
    } catch (_) {
      throw new OverrideClientError(messages.network);
    }
    return readJson(response);
  }

  function editorDestination(editUrl, editorTemplate, productId, baseUrl) {
    try {
      const expected = new URL(routeUrl(editorTemplate, productId, baseUrl));
      const destination = new URL(String(editUrl || ""), baseUrl);
      if (
        destination.origin !== expected.origin ||
        destination.pathname !== expected.pathname ||
        destination.search !== expected.search ||
        destination.hash
      ) {
        throw new Error("unexpected editor route");
      }
      return destination.href;
    } catch (_) {
      throw new OverrideClientError(messages.destination);
    }
  }

  root.ProductsOverrideClient = Object.freeze({
    OverrideClientError,
    editorDestination,
    messages,
    readJson,
    requestJson,
    routeUrl,
  });
})(window);
