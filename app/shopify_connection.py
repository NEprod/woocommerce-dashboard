"""Read-only Shopify foundation. Only the fixed connection query is permitted."""
from dataclasses import dataclass, field
import json
import re
from time import monotonic

import requests

QUERY = "query Connection { shop { id myshopifyDomain } currentAppInstallation { accessScopes { handle } } }"
VERSIONS = ("2026-10", "2026-07", "2026-04", "2026-01")


class ShopifyError(ValueError):
    pass


@dataclass(frozen=True)
class ShopifyConfiguration:
    domain: str
    token: str = field(repr=False)
    api_version: str = "2026-10"

    def validated(self):
        if not isinstance(self.domain, str):
            raise ShopifyError("Enter your shop-name.myshopify.com domain.")
        domain = self.domain.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.myshopify\.com", domain):
            raise ShopifyError("Enter only your shop-name.myshopify.com domain, without a URL, path or port.")
        if not isinstance(self.token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,512}", self.token):
            raise ShopifyError("Enter a valid Admin API access token, not a storefront token or client secret.")
        if self.api_version not in VERSIONS:
            raise ShopifyError("Select a supported pinned Shopify API version.")
        return ShopifyConfiguration(domain, self.token, self.api_version)


class ShopifyAdapter:
    platform = "shopify"
    can_publish = False

    def test_connection(self, configuration, *, session=None):
        config = configuration.validated()
        own = session is None
        session = session or requests.Session()
        if own:
            session.trust_env = False  # Never send the token through an inherited proxy/netrc.
        try:
            with session.post(
                f"https://{config.domain}/admin/api/{config.api_version}/graphql.json",
                headers={"X-Shopify-Access-Token": config.token, "Accept": "application/json"},
                json={"query": QUERY}, timeout=(5, 15), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code in (401, 403):
                    raise ShopifyError("Shopify rejected authentication or access. Check the installed app, token expiry and scopes.")
                if response.status_code == 429:
                    raise ShopifyError("Shopify is rate limiting this connection. Wait before testing again.")
                if response.status_code != 200:
                    raise ShopifyError("Shopify connection failed. Check the domain and service availability; no store data was changed.")
                if response.headers.get("X-Shopify-API-Version", config.api_version) != config.api_version:
                    raise ShopifyError("Shopify served a different API version. Select a supported version and test again.")
                raw = bytearray()
                started = monotonic()
                for chunk in response.iter_content(8192):
                    raw.extend(chunk)
                    if len(raw) > 65536 or monotonic() - started > 20:
                        raise ShopifyError("Shopify returned an oversized or incomplete response. Try again later.")
                data = json.loads(raw)
                if data.get("errors"):
                    raise ShopifyError("Shopify could not complete the read query. Check token permissions or rate limits.")
                shop = data["data"]["shop"]
                scopes = data["data"]["currentAppInstallation"]["accessScopes"]
                if (shop["myshopifyDomain"].lower() != config.domain
                        or not re.fullmatch(r"gid://shopify/Shop/\d+", shop["id"])):
                    raise ShopifyError("Shop identity did not match the configured domain. Nothing was saved.")
                handles = sorted({s["handle"] for s in scopes if re.fullmatch(r"[a-z_]{1,80}", s["handle"])})
                return {"shop_id": shop["id"], "domain": config.domain, "api_version": config.api_version,
                        "scopes": handles, "products_readable": bool({"read_products", "write_products"} & set(handles))}
        except requests.Timeout:
            raise ShopifyError("Shopify timed out. Retry the read-only connection test later.") from None
        except requests.RequestException:
            raise ShopifyError("Unable to reach Shopify securely. Check connectivity and the store domain.") from None
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError):
            raise ShopifyError("Shopify returned incomplete or invalid connection evidence. Nothing was saved.") from None
        finally:
            if own:
                session.close()
