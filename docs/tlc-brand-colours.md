# TLC dashboard colour refresh

Application changes are confined to `custom.css`, `metadata-taxonomy.css` and
`taxonomy.css`. No application HTML, JavaScript, Python, SVG assets, database
schema, JSON, Shopify extension or storefront files change.

## Colour mapping

| Purpose | New colour |
| --- | --- |
| Existing canvas token | Warm Cream `#F8F3E7` |
| Decorative navy panels, navigation, primary actions, save bars | Forest Green `#1F3D2E` |
| Dark hover/raised decorative surfaces | `#294D3B` |
| Main light-surface text | Charcoal `#2E2E2E` |
| Supporting light-surface text | `#5F6B64` |
| Dark-surface links, selected navigation icons, accent actions, focus ring | Muted Gold `#D4B574` |
| Decorative selected-navigation inset | Antique Brass `#B08D57` |
| Code/log surface backgrounds | `#162E23`, `#1F3D2E`, `#11261C` |

White panels and input fills remain white. Existing secondary/elevated light
surfaces, borders, shadows, typography, spacing, sizes, transitions, positioning
and breakpoints are retained. Enabled form boundaries use Forest Green without
changing their widths. Existing validation/error overrides still take precedence.

Brand tokens are added inside the existing `:root`, not as another theme layer.
The old shared lime, teal, success, information, warning, error, neutral and
status-ink tokens remain unchanged. In particular, the old dark ink token still
colours the semantic readiness checkmark; it is not a decorative navy surface.
Brand colours are applied to decorative consumers separately so statuses do not
change accidentally. Low-specificity contextual link/helper colours keep text
readable inside dark panels without overriding explicit badges or light cards.

## Focused validation

Run `python3 -m pytest -q tests/test_brand_colours.py tests/test_branding.py`.
This checks the palette, preserved semantic tokens, retained white surfaces and
text contrast. `node --check tests/browser/brand-colours.browser.mjs` checks test
syntax; `git diff --check` checks whitespace.

The browser comparison uses the disposable, outbound-blocked
`tests/browser/organisation_fixture.py` server on loopback port 7503, not a live
dashboard. Run `tests/browser/brand-colours.browser.mjs` with `PLAYWRIGHT_MODULE`
and `CHROME_BINARY` pointing at an installed Playwright runtime and Chromium.
The baseline is commit `640d6071164b37bb6cc795ba34de5786eda5085a`.

It compares 18 pages at 1440×1000 and 390×844: Dashboard, Products, Collections,
Product Override, Shared Collection, Scanner, Operations, issue drill-down,
Orders, Packages, package editor, connection, discovery, Shopify review, taxonomy,
taxonomy review, Catalogue Intake and Settings. It compares element geometry,
fonts, spacing, borders and positioning, checks horizontal overflow, verifies
the fixed save bar clears mobile navigation, and opens the existing field modal.
It also compares 53 representative semantic styles at both widths and inspects
the complete loaded stylesheet rules for non-colour changes, including responsive
and hidden-state rules. Requests are restricted to the disposable loopback app;
all non-GET/HEAD requests are blocked.

Before/after screenshots and the machine-readable results are generated under
`/tmp/tlc-brand-colours/`. These are local test artefacts, not production captures.
Mobile testing is Chromium viewport emulation, not a physical iPhone/Safari test.
Shopify review/discovery pages are checked without submitting previews or API calls.

Key contrast ratios: off-white/Forest Green 11.34:1, Warm Cream/Forest Green
10.74:1, Charcoal/Warm Cream 12.26:1, Charcoal/Muted Gold 6.89:1,
Muted Gold/Forest Green 6.04:1, supporting text/Warm Cream 5.03:1.
Forest Green/white control boundaries are 11.90:1. Brass/cream is only 2.79:1,
so brass is decorative, not small text. These checks cover the changed brand
combinations, not a claim of whole-application WCAG certification: existing
semantic status colours and disabled-state colours are intentionally preserved.
The browser-chrome `theme-color` HTML metadata is untouched (CSS-only scope).

Validation on 25 October 2026: 27 focused Python checks passed; all 36 page/viewport
comparisons passed; all 106 semantic samples matched the baseline. The modal,
focus/hover, contrast, fixed-save-bar and complete CSSOM colour-only checks passed.
No horizontal overflow or geometry changes were found. No large functional
regression suite or live-store operations were needed for this CSS-only change.

## Merchant-controlled Unraid acceptance

1. Back up current appdata using the established procedure. Record the current
   immutable image tag for rollback; no database migration is introduced here.
2. Change only the image repository/tag to the immutable `tlc-brand-colours-`
   commit tag reported with this release. Keep all mounts, paths and environment
   settings unchanged. Apply the update manually when ready.
3. Hard-refresh/reopen TLCDashboard to avoid cached CSS. Check the cream canvas,
   green panels and primary controls, white inputs and readable links.
4. On mobile, inspect Product Override cards, the fixed Save Metadata bar, bottom
   navigation and a modal. Confirm no horizontal overflow or overlapping controls.
5. Compare existing Success, Warning, Recovery Required, Error, Processing and
   taxonomy badges with the previous release. Their colours must be unchanged.
6. No Shopify sync, scan or metadata save is required merely to accept this
   presentation change. No Unraid deployment is performed by this work.
