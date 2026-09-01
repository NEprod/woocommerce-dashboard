"""Read-only WooCommerce discovery, capability audit, and health history."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from flask import current_app

from app.models import CatalogueOperation
from app.utils.discord import notify_woo_connection_completed, notify_woo_connection_failed
from app.utils.operation_control import acquire_catalogue_operation, finish_catalogue_operation
from app.utils.operation_live import persist_live_state
from app.utils.redaction import redact_diagnostic


OPERATION_TYPE = "woo_connection_test"
CONNECT_TIMEOUT = 3.05
READ_TIMEOUT = 8
MAX_REDIRECTS = 3
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_DISCOVERY_INDEX_BYTES = 8 * 1024 * 1024
MAX_JSON_NESTING = 128
STREAM_CHUNK_BYTES = 64 * 1024
MAX_NAMESPACES = 20
MAX_CAPABILITIES = 12
MAX_LIMITATION_FINDINGS = 12
SAFE_METHODS = {"GET", "HEAD"}
PUBLISH_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH"}
MAX_WRITE_BODY_BYTES = 256 * 1024


class WooConnectionError(RuntimeError):
    """A controlled, safe connection-test failure."""

    def __init__(self, category, message, *, status_code=None, diagnostics=None, remote_error=None):
        super().__init__(message)
        self.category = str(category)[:64]
        self.message = redact_diagnostic(message, limit=300)
        self.status_code = status_code
        self.diagnostics = {
            key: value
            for key, value in (diagnostics or {}).items()
            if key in {
                "endpoint_category", "compressed_bytes", "decompressed_bytes",
                "configured_limit", "content_length", "content_encoding", "failure_phase",
            }
            and (value is None or isinstance(value, (str, int, float, bool)))
        }
        self.remote_error = _safe_remote_error(remote_error, status_code=status_code)


def _bounded_text(value, limit=300):
    if value is None:
        return ""
    text = re.sub(r"https?://[^\s<>\"']+", "[URL omitted]", str(value), flags=re.IGNORECASE)
    return redact_diagnostic(text, limit=limit)


def _safe_remote_error(payload, *, status_code=None):
    """Retain only bounded, documented Woo REST error fields."""

    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    safe = {
        "code": _bounded_text(payload.get("code"), 96),
        "message": _bounded_text(payload.get("message"), 400),
        "status": status_code if isinstance(status_code, int) else _safe_header_integer(data.get("status")),
        "params": {},
        "details": {},
    }
    params = data.get("params") if isinstance(data.get("params"), dict) else payload.get("params")
    if isinstance(params, dict):
        for key, value in list(params.items())[:12]:
            safe["params"][_bounded_text(key, 80)] = _bounded_text(value, 240)
    details = data.get("details") if isinstance(data.get("details"), dict) else payload.get("details")
    if isinstance(details, dict):
        for key, value in list(details.items())[:12]:
            if isinstance(value, dict):
                message = value.get("message") or value.get("code")
            else:
                message = value
            safe["details"][_bounded_text(key, 80)] = _bounded_text(message, 240)
    return {key: value for key, value in safe.items() if value not in (None, "", {})}


@dataclass(frozen=True)
class WooConfiguration:
    store_url: str
    consumer_key: str
    consumer_secret: str

    @property
    def complete(self):
        return bool(self.store_url and self.consumer_key and self.consumer_secret)

    def safe_summary(self):
        hostname = ""
        if self.store_url:
            try:
                hostname = urlsplit(self.store_url).hostname or ""
            except ValueError:
                pass
        return {
            "configured": self.complete,
            "store_url_configured": bool(self.store_url),
            "consumer_key_configured": bool(self.consumer_key),
            "consumer_secret_configured": bool(self.consumer_secret),
            "configuration_source": "Runtime environment",
            "hostname": hostname[:253],
        }


def effective_configuration():
    return WooConfiguration(
        store_url=os.environ.get("WOO_STORE_URL", "").strip(),
        consumer_key=os.environ.get("WOO_CONSUMER_KEY", "").strip(),
        consumer_secret=os.environ.get("WOO_CONSUMER_SECRET", "").strip(),
    )


def normalize_store_url(value):
    """Return one canonical HTTPS origin, stripping a supplied REST path."""

    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise WooConnectionError("invalid_url", "The Store URL is malformed.") from error
    if parsed.scheme.lower() != "https":
        raise WooConnectionError("invalid_scheme", "The Store URL must use HTTPS.")
    if not parsed.hostname or any(character.isspace() for character in parsed.hostname):
        raise WooConnectionError("invalid_url", "The Store URL must contain a valid hostname.")
    if parsed.username or parsed.password:
        raise WooConnectionError("embedded_credentials", "Credentials must not be embedded in the Store URL.")
    if parsed.query or parsed.fragment:
        raise WooConnectionError("invalid_url", "The Store URL must not contain a query string or fragment.")
    path = parsed.path or ""
    marker = re.search(r"/wp-json(?:/|$)", path, flags=re.IGNORECASE)
    if marker:
        path = path[: marker.start()]
    path = path.rstrip("/")
    host = parsed.hostname.encode("idna").decode("ascii").lower()
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit(("https", netloc, path, "", ""))


def _origin(url):
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _safe_header_integer(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _safe_content_encoding(value):
    encoding = str(value or "identity").strip().lower()[:32]
    return encoding if encoding in {"identity", "gzip", "br", "deflate"} else "other"


def _compressed_bytes_read(response):
    try:
        value = response.raw.tell()
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _json_nesting_is_bounded(payload):
    stack = [(payload, 1)]
    while stack:
        value, depth = stack.pop()
        if depth > MAX_JSON_NESTING:
            return False
        if isinstance(value, dict):
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
    return True


class ReadOnlyWooClient:
    allowed_methods = SAFE_METHODS
    unsafe_method_message = "WooCommerce discovery permits read-only requests only."

    def __init__(self, configuration, *, session=None):
        self.configuration = configuration
        self.base_url = normalize_store_url(configuration.store_url)
        self.session = session or requests.Session()
        self.request_count = 0
        self.last_transfer = {}

    def request_json(
        self, method, url, *, authenticated=False,
        response_limit=MAX_RESPONSE_BYTES, endpoint_category="ordinary_api",
        json_body=None,
    ):
        method = str(method).upper()
        if method not in self.allowed_methods:
            raise WooConnectionError("unsafe_method", self.unsafe_method_message)
        if method in SAFE_METHODS and json_body is not None:
            raise WooConnectionError("unsafe_body", "Read-only WooCommerce requests cannot contain a write body.")
        if json_body is not None:
            if not isinstance(json_body, (dict, list)):
                raise WooConnectionError("invalid_write_body", "The WooCommerce write payload is invalid.")
            if len(json.dumps(json_body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_WRITE_BODY_BYTES:
                raise WooConnectionError("write_body_too_large", "The WooCommerce write payload exceeds the safe size limit.")
        response_limit = int(response_limit)
        if response_limit <= 0 or response_limit > MAX_DISCOVERY_INDEX_BYTES:
            raise ValueError("Invalid bounded response policy")
        current = url
        for redirect_number in range(MAX_REDIRECTS + 1):
            response = None
            try:
                response = self.session.request(
                    method,
                    current,
                    auth=(self.configuration.consumer_key, self.configuration.consumer_secret) if authenticated else None,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                    allow_redirects=False,
                    verify=True,
                    stream=True,
                    json=json_body,
                    headers={"Accept": "application/json", "User-Agent": "WooCommerce-Dashboard/controlled-publisher" if method not in SAFE_METHODS else "WooCommerce-Dashboard/connection-test"},
                )
                self.request_count += 1
            except requests.exceptions.SSLError as error:
                raise WooConnectionError("tls_failure", "TLS certificate verification failed.") from error
            except requests.ConnectTimeout as error:
                raise WooConnectionError("connect_timeout", "The connection timed out.") from error
            except requests.ReadTimeout as error:
                raise WooConnectionError("read_timeout", "The response timed out.") from error
            except requests.ConnectionError as error:
                message = str(error).lower()
                category = "dns_failure" if any(token in message for token in ("name resolution", "nodename", "dns")) else "connection_failed"
                raise WooConnectionError(category, "The store could not be reached.") from error
            except requests.RequestException as error:
                raise WooConnectionError("network_failure", "The store request failed safely.") from error

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                if not location:
                    response.close()
                    raise WooConnectionError("invalid_redirect", "The store returned an invalid redirect.")
                destination = urljoin(current, location)
                if _origin(destination) != _origin(self.base_url):
                    response.close()
                    raise WooConnectionError("cross_origin_redirect", "The store redirected to another origin; credentials were not forwarded.")
                if method not in SAFE_METHODS:
                    response.close()
                    raise WooConnectionError("write_redirect_refused", "WooCommerce redirected a mutating request; the write was not repeated automatically.")
                if redirect_number >= MAX_REDIRECTS:
                    response.close()
                    raise WooConnectionError("redirect_loop", "The store exceeded the safe redirect limit.")
                response.close()
                current = destination
                continue

            http_error = response.status_code >= 400
            if http_error:
                categories = {
                    400: "bad_request", 401: "authentication_rejected", 403: "forbidden",
                    404: "not_found", 429: "rate_limited",
                }
                category = categories.get(response.status_code, "server_error" if response.status_code >= 500 else "http_error")
                messages = {
                    401: "WooCommerce rejected the configured credentials.",
                    403: "The configured credentials are forbidden from reading this resource.",
                    404: "The requested REST resource is unavailable.",
                    429: "The store rate-limited the connection test.",
                }
                http_category = category
                http_message = messages.get(response.status_code, f"The store returned HTTP {response.status_code}.")

            content_length = _safe_header_integer(response.headers.get("Content-Length"))
            content_encoding = _safe_content_encoding(response.headers.get("Content-Encoding"))
            chunks, total = [], 0
            try:
                try:
                    for chunk in response.iter_content(chunk_size=STREAM_CHUNK_BYTES):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > response_limit:
                            diagnostics = {
                                "endpoint_category": endpoint_category,
                                "compressed_bytes": _compressed_bytes_read(response),
                                "decompressed_bytes": total,
                                "configured_limit": response_limit,
                                "content_length": content_length,
                                "content_encoding": content_encoding,
                                "failure_phase": "streaming",
                            }
                            if http_error:
                                raise WooConnectionError(
                                    http_category, http_message, status_code=response.status_code,
                                    diagnostics={**diagnostics, "failure_phase": "http_error_body_truncated"},
                                )
                            if endpoint_category == "wordpress_rest_index":
                                raise WooConnectionError(
                                    "discovery_index_too_large",
                                    "The WordPress REST API index is larger than the current safe discovery limit. "
                                    "This is commonly caused by many plugins registering REST routes. "
                                    "No credentials were exposed and no write request was sent.",
                                    diagnostics=diagnostics,
                                )
                            raise WooConnectionError(
                                "response_too_large", "The store response exceeded the safe size limit.",
                                diagnostics=diagnostics,
                            )
                        chunks.append(chunk)
                except WooConnectionError:
                    raise
                except requests.ReadTimeout as error:
                    raise WooConnectionError("read_timeout", "The response timed out.") from error
                except requests.exceptions.ContentDecodingError as error:
                    raise WooConnectionError("content_decoding_failed", "The compressed store response could not be decoded safely.") from error
                except requests.ConnectionError as error:
                    raise WooConnectionError("connection_failed", "The store response stream ended unexpectedly.") from error
                transfer = {
                    "endpoint_category": endpoint_category,
                    "compressed_bytes": _compressed_bytes_read(response),
                    "decompressed_bytes": total,
                    "configured_limit": response_limit,
                    "content_length": content_length,
                    "content_encoding": content_encoding,
                }
                self.last_transfer = transfer
                try:
                    payload = json.loads(b"".join(chunks).decode(response.encoding or "utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError) as error:
                    if http_error:
                        raise WooConnectionError(
                            http_category, http_message, status_code=response.status_code,
                            diagnostics={**transfer, "failure_phase": "http_error"},
                        ) from error
                    raise WooConnectionError(
                        "malformed_json", "The store returned malformed JSON.",
                        diagnostics={**transfer, "failure_phase": "json_parsing"},
                    ) from error
                if not _json_nesting_is_bounded(payload):
                    if http_error:
                        raise WooConnectionError(
                            http_category, http_message, status_code=response.status_code,
                            diagnostics={**transfer, "failure_phase": "http_error"},
                        )
                    raise WooConnectionError(
                        "json_too_deep", "The store response exceeded the safe JSON nesting limit.",
                        diagnostics={**transfer, "failure_phase": "json_validation"},
                    )
                if http_error:
                    remote_error = _safe_remote_error(payload, status_code=response.status_code)
                    raise WooConnectionError(
                        http_category,
                        remote_error.get("message") or http_message,
                        status_code=response.status_code,
                        diagnostics={**transfer, "failure_phase": "http_error"},
                        remote_error=remote_error,
                    )
                return payload, response
            finally:
                response.close()
        raise WooConnectionError("redirect_loop", "The store exceeded the safe redirect limit.")


class PublisherWooClient(ReadOnlyWooClient):
    """The only transport policy that permits reviewed Woo mutations."""

    allowed_methods = PUBLISH_METHODS
    unsafe_method_message = "Controlled publishing permits GET, POST, PUT, and PATCH only; DELETE is forbidden."


def _route_methods(routes, candidates):
    methods = set()
    discovered = False
    for path, definition in (routes or {}).items():
        if not any(re.fullmatch(pattern, str(path)) for pattern in candidates):
            continue
        discovered = True
        if not isinstance(definition, dict):
            continue
        for endpoint in definition.get("endpoints", []):
            if isinstance(endpoint, dict):
                methods.update(str(value).upper() for value in endpoint.get("methods", []) if str(value).upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"})
    return discovered, sorted(methods)


def _capability(
    key, name, group, route, patterns, *, requirement, current_impact,
    future_impact, recommendation, required=False, dependent=None,
):
    return {
        "key": key, "name": name, "group": group, "route": route,
        "patterns": patterns, "required": required, "dependent": dependent,
        "requirement": requirement, "current_impact": current_impact,
        "future_impact": future_impact, "recommendation": recommendation,
    }


def _capability_specs(namespace):
    root = f"/{namespace}"
    return [
        _capability("products", "Products", "Publishing", f"{root}/products", [rf"{re.escape(root)}/products"], requirement="required_product_publishing", current_impact="Blocks product publishing.", future_impact="Required for product publishing.", recommendation="Restore authenticated read access before product publishing is enabled.", required=True),
        _capability("product_variations", "Product variations", "Publishing", None, [rf"{re.escape(root)}/products/\(\?P<product_id>.*\)/variations"], requirement="later_variation_publishing", current_impact="Required for variation publishing.", future_impact="Required by a later Phase 3 variation-publishing milestone.", recommendation="Review this capability before variation publishing is enabled.", dependent="product"),
        _capability("product_categories", "Product categories", "Publishing", f"{root}/products/categories", [rf"{re.escape(root)}/products/categories"], requirement="required_product_publishing", current_impact="Blocks product publishing.", future_impact="Required for product publishing.", recommendation="Restore authenticated read access before product publishing is enabled.", required=True),
        _capability("product_tags", "Product tags", "Publishing", f"{root}/products/tags", [rf"{re.escape(root)}/products/tags"], requirement="required_product_publishing", current_impact="Blocks product publishing.", future_impact="Required for product publishing.", recommendation="Restore authenticated read access before product publishing is enabled.", required=True),
        _capability("product_attributes", "Product attributes", "Publishing", f"{root}/products/attributes", [rf"{re.escape(root)}/products/attributes"], requirement="required_product_publishing", current_impact="Blocks product publishing.", future_impact="Required for product publishing.", recommendation="Restore authenticated read access before product publishing is enabled.", required=True),
        _capability("attribute_terms", "Attribute terms", "Publishing", None, [rf"{re.escape(root)}/products/attributes/\(\?P<attribute_id>.*\)/terms"], requirement="later_variation_publishing", current_impact="Required for variation publishing.", future_impact="Required by a later Phase 3 variation-publishing milestone.", recommendation="Review this capability before variation publishing is enabled.", dependent="attribute"),
        _capability("media", "Media", "Media", "/wp/v2/media", [r"/wp/v2/media"], requirement="later_media_synchronisation", current_impact="Required for media synchronisation.", future_impact="Required by a later Phase 3 media-synchronisation milestone.", recommendation="Review this capability before media synchronisation is enabled."),
        _capability("orders", "Orders", "Later milestones", f"{root}/orders", [rf"{re.escape(root)}/orders"], requirement="future_optional", current_impact="Does not affect product publishing.", future_impact="Required only for future order features.", recommendation="No action required for current Phase 3 publishing work."),
        _capability("customers", "Customers", "Later milestones", f"{root}/customers", [rf"{re.escape(root)}/customers"], requirement="future_optional", current_impact="Does not affect product publishing.", future_impact="Required only for future customer features.", recommendation="No action required for current Phase 3 publishing work."),
        _capability("system_status", "System status", "Diagnostics", f"{root}/system_status", [rf"{re.escape(root)}/system_status"], requirement="future_optional", current_impact="Does not affect product publishing.", future_impact="Required only for diagnostics.", recommendation="No action required unless expanded store diagnostics are needed."),
    ]


def _safe_scalar(value, limit=120):
    return redact_diagnostic(value if isinstance(value, (str, int, float, bool)) else "", limit=limit)


def _read_status(error):
    if error.category in {"authentication_rejected", "forbidden"}:
        return "forbidden"
    if error.category == "not_found":
        return "not_exposed"
    if error.category in {"malformed_json", "json_too_deep"}:
        return "malformed_response"
    if error.category in {"connect_timeout", "read_timeout"}:
        return "timeout"
    if error.category in {"dns_failure", "connection_failed", "network_failure", "tls_failure", "content_decoding_failed"}:
        return "transport_failure"
    return "unavailable"


def _read_label(status):
    return {
        "verified": "Read access verified", "forbidden": "Forbidden",
        "unavailable": "Unavailable", "not_exposed": "Not exposed",
        "not_tested": "Not tested", "malformed_response": "Malformed response",
        "timeout": "Timeout", "transport_failure": "Transport failure",
    }.get(status, "Unavailable")


def _limitation_explanation(read_status, status_code):
    if status_code:
        return f"Read access returned HTTP {int(status_code)}."
    return {
        "not_exposed": "The REST route was not exposed by the store.",
        "not_tested": "The route was discovered but could not be tested without a dependent record.",
        "malformed_response": "The resource returned a malformed or excessively nested response.",
        "timeout": "The bounded read request timed out.",
        "transport_failure": "The bounded read request ended with a transport failure.",
    }.get(read_status, "Authenticated read access could not be verified.")


def _limitation_finding(item):
    return {
        "key": item["key"], "label": item["name"],
        "requirement": item["requirement"],
        "route_discovered": bool(item["route_discovered"]),
        "read_status": item["read_status"], "http_status": item["status_code"],
        "severity": "warning", "continuation_allowed": True,
        "current_impact": item["current_impact"],
        "future_impact": item["future_impact"],
        "recommendation": item["recommendation"],
        "explanation": _limitation_explanation(item["read_status"], item["status_code"]),
    }


def run_connection_discovery(configuration=None, *, session=None):
    configuration = configuration or effective_configuration()
    if not configuration.complete:
        raise WooConnectionError("not_configured", "Store URL, Consumer Key, and Consumer Secret must be configured in the runtime environment.")
    client = ReadOnlyWooClient(configuration, session=session)
    started = time.monotonic()
    index_started = time.monotonic()
    index, index_response = client.request_json(
        "GET", f"{client.base_url}/wp-json/",
        response_limit=MAX_DISCOVERY_INDEX_BYTES,
        endpoint_category="wordpress_rest_index",
    )
    discovery_transfer = dict(client.last_transfer)
    index_latency = round((time.monotonic() - index_started) * 1000)
    if not isinstance(index, dict) or not isinstance(index.get("routes"), dict):
        raise WooConnectionError("rest_unavailable", "WordPress responded, but a valid REST index was not available.")
    raw_namespaces = index.get("namespaces", [])
    namespaces = [_safe_scalar(value, 80) for value in raw_namespaces if isinstance(value, str)][:MAX_NAMESPACES]
    woo_namespaces = sorted(
        (value for value in namespaces if re.fullmatch(r"wc/v\d+", value)),
        key=lambda value: int(value.rsplit("v", 1)[1]), reverse=True,
    )
    if not woo_namespaces:
        raise WooConnectionError("woo_namespace_absent", "WordPress REST is available, but no supported WooCommerce REST namespace was discovered.")
    namespace = woo_namespaces[0]
    routes = index["routes"]
    specs = _capability_specs(namespace)[:MAX_CAPABILITIES]
    route_summaries = {
        spec["name"]: _route_methods(routes, spec["patterns"])
        for spec in specs
    }
    store_name = _safe_scalar(index.get("name")) or "Not exposed"
    canonical = index.get("home") or index.get("url")
    namespace_count = len(raw_namespaces)
    rate_limit = _safe_scalar(index_response.headers.get("X-RateLimit-Remaining")) or "Not exposed"
    del routes, index, raw_namespaces
    capabilities = []
    product_id = None
    attribute_id = None
    authenticated_latency = None
    system_data = {}

    for spec in specs:
        discovered, methods = route_summaries[spec["name"]]
        read_status = "not_exposed" if not discovered else "not_tested"
        status_code = None
        route = spec["route"]
        if spec["dependent"] == "product" and product_id is not None:
            route = f"/{namespace}/products/{product_id}/variations"
        elif spec["dependent"] == "attribute" and attribute_id is not None:
            route = f"/{namespace}/products/attributes/{attribute_id}/terms"
        if discovered and route:
            request_started = time.monotonic()
            try:
                payload, response = client.request_json("GET", f"{client.base_url}/wp-json{route}?per_page=1", authenticated=True)
                elapsed = round((time.monotonic() - request_started) * 1000)
                authenticated_latency = elapsed if authenticated_latency is None else authenticated_latency
                status_code = response.status_code
                read_status = "verified"
                if spec["name"] == "Products" and isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    identifier = payload[0].get("id")
                    product_id = identifier if isinstance(identifier, int) and identifier > 0 else None
                if spec["name"] == "Product attributes" and isinstance(payload, list) and payload and isinstance(payload[0], dict):
                    identifier = payload[0].get("id")
                    attribute_id = identifier if isinstance(identifier, int) and identifier > 0 else None
                if spec["name"] == "System status" and isinstance(payload, dict):
                    system_data = payload
            except WooConnectionError as error:
                status_code = error.status_code
                read_status = _read_status(error)
        capabilities.append({
            "key": spec["key"], "name": spec["name"], "group": spec["group"],
            "required": spec["required"], "requirement": spec["requirement"],
            "route_discovered": discovered, "route_status": "discovered" if discovered else "not_exposed",
            "read_status": read_status, "read_state": _read_label(read_status),
            "advertised_methods": methods,
            "advertised_write_methods": [value for value in methods if value not in SAFE_METHODS],
            "write_permission": "Not verified", "status_code": status_code,
            "current_impact": spec["current_impact"], "future_impact": spec["future_impact"],
            "recommendation": spec["recommendation"],
        })

    required = [item for item in capabilities if item["required"]]
    required_verified = sum(item["read_status"] == "verified" for item in required)
    required_failed = [item for item in required if item["read_status"] != "verified"]
    optional_limitations = [item for item in capabilities if not item["required"] and item["read_status"] != "verified"]
    if required_failed:
        names = ", ".join(item["name"] for item in required_failed[:4])
        raise WooConnectionError("required_capability_failed", f"Required WooCommerce reads could not be verified: {names}.")

    environment = system_data.get("environment") if isinstance(system_data.get("environment"), dict) else {}
    settings = system_data.get("settings") if isinstance(system_data.get("settings"), dict) else {}
    canonical_url = "Verified store origin"
    if isinstance(canonical, str):
        try:
            if _origin(canonical) == _origin(client.base_url):
                canonical_url = normalize_store_url(canonical)
        except WooConnectionError:
            pass
    duration = round((time.monotonic() - started) * 1000)
    limitation_findings = [_limitation_finding(item) for item in optional_limitations[:MAX_LIMITATION_FINDINGS]]
    return {
        "state": "connected_with_limitations" if optional_limitations else "connected",
        "hostname": urlsplit(client.base_url).hostname or "",
        "canonical_store_url": canonical_url,
        "store_name": store_name,
        "store_reachability": "Reachable",
        "wordpress_rest": "Available", "woo_rest": "Available", "authentication": "Verified",
        "tls": "Verified", "selected_namespace": namespace,
        "namespaces": namespaces, "namespaces_truncated": namespace_count > len(namespaces),
        "wordpress_version": _safe_scalar(environment.get("wp_version")) or "Not exposed",
        "woocommerce_version": _safe_scalar(environment.get("version")) or "Not exposed",
        "currency": _safe_scalar(settings.get("currency")) or "Not exposed",
        "timezone": _safe_scalar(environment.get("site_timezone")) or "Not exposed",
        "permalink_compatibility": "Available" if woo_namespaces else "Unavailable",
        "capabilities": capabilities, "required_verified": required_verified,
        "required_total": len(required), "optional_limitations": len(optional_limitations),
        "limitation_findings": limitation_findings,
        "limitation_findings_truncated": max(0, len(optional_limitations) - len(limitation_findings)),
        "wordpress_latency_ms": index_latency, "authenticated_latency_ms": authenticated_latency,
        "duration_ms": duration, "request_count": client.request_count,
        "rate_limit": rate_limit,
        "discovery_transfer": discovery_transfer,
    }


def _operation_summary(result, *, failure=None):
    if failure:
        summary = {
            "health_state": "failed", "failure_category": failure.category,
            "failure_reason": failure.message, "status_code": failure.status_code,
        }
        if failure.diagnostics:
            summary["transfer_diagnostics"] = failure.diagnostics
        return summary
    return {
        key: result[key]
        for key in (
            "state", "hostname", "store_name", "store_reachability", "wordpress_rest", "woo_rest",
            "authentication", "tls", "selected_namespace", "namespaces", "namespaces_truncated",
            "wordpress_version", "woocommerce_version", "currency", "timezone",
            "permalink_compatibility", "capabilities", "required_verified", "required_total",
            "optional_limitations", "limitation_findings", "limitation_findings_truncated",
            "wordpress_latency_ms", "authenticated_latency_ms",
            "duration_ms", "request_count", "rate_limit", "discovery_transfer",
        )
    }


def execute_connection_test(*, session=None):
    configuration = effective_configuration()
    safe_configuration = configuration.safe_summary()
    lease = acquire_catalogue_operation(OPERATION_TYPE, {
        "configuration_state": "configured" if configuration.complete else "not_configured",
        "configuration_source": "runtime_environment",
        "store_hostname": safe_configuration["hostname"],
    })
    live = {
        "stage": "testing_connection", "status": "running", "current_item": safe_configuration["hostname"] or "WooCommerce",
        "latest_message": "Running bounded read-only REST discovery.", "progress": {"completed": 0, "total": 1, "percent": 0},
        "counts": {}, "next_sequence": 2,
    }
    try:
        persist_live_state(lease.id, live, [{"sequence": 1, "severity": "info", "line": "WooCommerce read-only connection test started."}])
        result = run_connection_discovery(configuration, session=session)
        status = "partial" if result["state"] == "connected_with_limitations" else "succeeded"
        summary = _operation_summary(result)
        try:
            discord_ok, discord_message = notify_woo_connection_completed(summary, operation_id=lease.id)
        except Exception:
            discord_ok, discord_message = False, "delivery failed"
        discord = {"state": "sent" if discord_ok else discord_message.replace(" ", "_"), "label": "Discord sent" if discord_ok else f"Discord {discord_message}", "events": [{"event": "terminal_summary", "state": "sent" if discord_ok else discord_message}]}
        logs = [{"sequence": 1, "severity": "info", "line": "WooCommerce read-only connection test started."}]
        for finding in summary.get("limitation_findings", []):
            suffix = f" — HTTP {finding['http_status']}" if finding.get("http_status") else ""
            logs.append({
                "sequence": len(logs) + 1, "severity": "warning",
                "line": f"Optional capability limited: {finding['label']}{suffix}. {finding['current_impact']}",
            })
        if status == "partial":
            logs.append({
                "sequence": len(logs) + 1, "severity": "info",
                "line": "Current product-publishing capabilities remain available.",
            })
        logs.append({
            "sequence": len(logs) + 1, "severity": "warning" if status == "partial" else "info",
            "line": "Connection test completed with limitations." if status == "partial" else "Connection test completed successfully.",
        })
        live.update({"stage": "completed", "status": status, "latest_message": "Read-only WooCommerce discovery completed.", "progress": {"completed": 1, "total": 1, "percent": 100}, "counts": {"warnings": result["optional_limitations"]}, "summary": summary, "discord": discord, "next_sequence": len(logs) + 1})
        persist_live_state(lease.id, live, logs)
        finish_catalogue_operation(
            lease.id, status=status, products_attempted=result["request_count"],
            products_succeeded=result["required_verified"], operation_summary=summary,
        )
        return lease.id
    except WooConnectionError as error:
        summary = _operation_summary({}, failure=error)
        try:
            discord_ok, discord_message = notify_woo_connection_failed(summary, operation_id=lease.id)
        except Exception:
            discord_ok, discord_message = False, "delivery failed"
        discord = {"state": "sent" if discord_ok else discord_message.replace(" ", "_"), "label": "Discord sent" if discord_ok else f"Discord {discord_message}", "events": [{"event": "terminal_summary", "state": "sent" if discord_ok else discord_message}]}
        live.update({"stage": "failed", "status": "failed", "latest_message": error.message, "progress": {"completed": 1, "total": 1, "percent": 100}, "counts": {"failures": 1}, "summary": summary, "discord": discord, "next_sequence": 3})
        persist_live_state(lease.id, live, [
            {"sequence": 1, "severity": "info", "line": "WooCommerce read-only connection test started."},
            {"sequence": 2, "severity": "error", "line": error.message},
        ])
        finish_catalogue_operation(
            lease.id, status="failed", products_attempted=1, products_failed=1,
            error=error.message, operation_summary=summary,
        )
        return lease.id
    except Exception:
        safe_error = WooConnectionError(
            "unexpected_failure",
            "The read-only connection test failed unexpectedly. Review configuration and try again.",
        )
        summary = _operation_summary({}, failure=safe_error)
        current_app.logger.error("WooCommerce connection test failed unexpectedly during read-only discovery")
        try:
            notify_woo_connection_failed(summary, operation_id=lease.id)
        except Exception:
            pass
        finish_catalogue_operation(
            lease.id, status="failed", products_attempted=1, products_failed=1,
            error=safe_error.message, operation_summary=summary,
        )
        return lease.id


def _summary_from_row(row):
    try:
        scope = json.loads(row.scope or "{}")
    except (TypeError, ValueError):
        return {}
    value = scope.get("operation_summary")
    return value if isinstance(value, dict) else {}


def build_woocommerce_workspace():
    """Build the offline workspace; this function never contacts the store."""

    configuration = effective_configuration().safe_summary()
    rows = CatalogueOperation.query.filter_by(operation_type=OPERATION_TYPE).order_by(CatalogueOperation.started_at.desc()).limit(20).all()
    latest = rows[0] if rows else None
    latest_summary = _summary_from_row(latest) if latest else {}
    last_success = next((row for row in rows if row.status in {"succeeded", "partial"}), None)
    last_failure = next((row for row in rows if row.status in {"failed", "interrupted"}), None)
    if latest and latest.status in {"running", "pending"}:
        state = "testing"
    elif latest:
        state = latest_summary.get("state") or latest_summary.get("health_state") or latest.status
    else:
        state = "not_tested" if configuration["configured"] else "not_configured"
    return {
        "configuration": configuration,
        "health": {"state": state, "latest": latest_summary, "last_success": last_success, "last_failure": last_failure},
        "history": [{
            "id": row.id, "short_id": row.id[:8], "status": row.status,
            "started_at": row.started_at, "finished_at": row.finished_at,
            "summary": _summary_from_row(row),
        } for row in rows],
        "writes_disabled": True,
    }
