# WooCommerce Dashboard Design System

## Status and authority

This document is the permanent visual specification for WooCommerce Dashboard.
It is the single source of truth for all Phase 2 and later interface work.

The canonical reference set comprises four approved project mockups:

1. Dashboard
2. Products
3. Standalone Login
4. Product Metadata Editor

They define the required visual language, hierarchy, spacing, proportions,
colour balance, component rhythm, and interaction philosophy. They are not a
licence to copy third-party branding, artwork, logos, illustrations, sample
catalogue content, or an exact page composition. Production pages must use the
project's own identity, real capabilities, and authoritative scanner data.

A proposed page is visually unacceptable if it would not immediately be
recognised as belonging beside all four references. When this document and an
older implementation disagree, this document governs future UI work. Behaviour,
scanner contracts, SKU identity, database semantics, and security constraints
remain governed by their respective technical specifications.

## Product character

The interface is a modern desktop SaaS workspace for sustained catalogue work.
It should feel premium, professional, calm, minimal, clean, and highly readable.
It is an application rather than a traditional content website.

The interface must not resemble:

- a stock Bootstrap administration theme;
- a Flask demonstration project;
- a collection of unrelated pages;
- a spreadsheet with navigation attached;
- a decorative marketing site;
- a mostly dark theme.

The overall composition is light. Deep slate surfaces create purposeful
structure and emphasis. Lime is memorable because it is limited.

## Reference-page observations

### Shared visual grammar

- Warm off-white surrounds a large, softly elevated application workspace.
- White and near-white surfaces occupy most of each page.
- A stable left sidebar provides identity and grouped navigation.
- Large, dark page titles establish hierarchy without excessive decoration.
- Cards use fine cool-grey borders, soft shadows, and generous radii.
- Deep slate appears in active navigation, filter bars, grouped headers,
  primary actions, and operational feature panels.
- Lime marks active, healthy, selected, or improving states in small doses.
- Muted teal supports neutral information; amber identifies attention states.
- Tables use whitespace, typography, pills, and nesting instead of heavy grids.
- Icons sit in soft circular or rounded containers and never replace labels for
  unfamiliar actions.

### Page-specific lessons

- **Dashboard:** mixed card sizes create an editorial hierarchy. The two large
  dark operational panels are the visual centre, not incidental decoration.
- **Products:** summary cards lead into a dark control bar, then collection
  groups with dark headers and parent-first expandable rows.
- **Login:** the navigation shell is removed. A centred split card pairs a dark
  identity panel with a restrained light form panel.
- **Metadata Editor:** the guided form owns most of the width while source,
  validation, scan, and product context occupy a supporting right column.

## Design tokens

Tokens are semantic. Components must not introduce page-specific hex values
when an existing semantic token expresses the purpose.

### Colour tokens

| Token | Recommended value | Purpose |
| --- | --- | --- |
| `--color-canvas` | `#F6F5F1` | Warm off-white page and login background |
| `--color-surface-primary` | `#FFFFFF` | Main cards, forms, sidebar, tables |
| `--color-surface-secondary` | `#F2F4F1` | Inset rows, grouped details, quiet regions |
| `--color-surface-elevated` | `#FBFCFA` | Menus, drawers, floating controls |
| `--color-surface-dark` | `#10262D` | Feature panels, toolbars, group headers |
| `--color-surface-dark-hover` | `#18353D` | Hover/selected state on dark surfaces |
| `--color-text-primary` | `#14262C` | Headings and primary copy |
| `--color-text-secondary` | `#526169` | Body and supporting labels |
| `--color-text-muted` | `#69777E` | Captions, timestamps, helper text |
| `--color-text-inverse` | `#F8FAF8` | Primary text on deep slate |
| `--color-border` | `#DCE2DE` | Inputs, cards, controls |
| `--color-divider` | `#E9EDEA` | Rows and low-emphasis separators |
| `--color-lime` | `#9BDC32` | Selected/healthy accent and small highlights |
| `--color-lime-hover` | `#86C421` | Lime control hover/pressed state |
| `--color-lime-soft` | `#EAF7D3` | Healthy/active pill background |
| `--color-lime-ink` | `#365F08` | Accessible text link/status on light surfaces |
| `--color-teal` | `#299DA8` | Supporting information accent |
| `--color-teal-soft` | `#E0F3F4` | Informational pill/icon background |
| `--color-warning` | `#E89A25` | Warning indicators and attention counts |
| `--color-warning-soft` | `#FFF1D8` | Warning pill/card background |
| `--color-warning-ink` | `#754600` | Warning text on a soft background |
| `--color-error` | `#C43F50` | Failed, invalid, destructive |
| `--color-error-soft` | `#FCE8EB` | Error pill/card background |
| `--color-error-ink` | `#8C2534` | Error text on a soft background |
| `--color-success` | `#43A652` | Succeeded and valid |
| `--color-success-soft` | `#E5F5E8` | Success pill/card background |
| `--color-success-ink` | `#246B30` | Success text on a soft background |
| `--color-info` | `#347DBB` | Neutral informational state |
| `--color-info-soft` | `#E7F2FB` | Informational banner background |
| `--color-info-ink` | `#205A89` | Informational text on a soft background |
| `--color-code-surface` | `#0C2027` | Advanced JSON/code editor |
| `--color-code-surface-raised` | `#142E36` | Code toolbar, search, selected line |
| `--color-code-text` | `#F4F8F6` | Code text |

Avoid gradients. A gradient is allowed only when it is so subtle that the
surface still reads as one colour, such as a two-to-four percent tonal shift in
the login identity panel. Do not use lime as a large page background.

### Accessible colour pairings

Required pairings are:

- primary text on canvas, primary, secondary, and elevated surfaces;
- secondary text on primary and elevated surfaces;
- inverse text on dark and dark-hover surfaces;
- dark primary text on lime controls;
- lime ink on white for text links, rather than bright lime for small text;
- warning ink on warning-soft;
- error ink on error-soft;
- success ink on success-soft;
- information ink on information-soft;
- code text on code surface.

Normal text must meet WCAG AA contrast of at least 4.5:1; large text and
non-text interface components must meet at least 3:1. Muted text is not exempt.
Bright lime must not carry white text. Status must always include text and/or an
icon, never colour alone.

### Typography tokens

Use the system stack:

```css
font-family: Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont,
  "Segoe UI", sans-serif;
```

Inter is optional and must be locally bundled if used. Do not introduce a
runtime font CDN. Use tabular numerals for statistics, prices, stock, and IDs.

| Token | Size / line height | Weight | Letter spacing | Use |
| --- | --- | --- | --- | --- |
| `--type-page-title` | `40px / 48px` | 750 | `-0.035em` | Desktop page title |
| `--type-section-title` | `24px / 32px` | 700 | `-0.02em` | Major content section |
| `--type-card-title` | `16px / 24px` | 700 | `-0.01em` | Card heading |
| `--type-group-title` | `15px / 22px` | 700 | `0` | Collection/table group heading |
| `--type-body` | `14px / 22px` | 400 | `0` | Default copy |
| `--type-label` | `13px / 20px` | 650 | `0` | Form labels, table emphasis |
| `--type-helper` | `12px / 18px` | 400 | `0` | Help and validation detail |
| `--type-caption` | `11px / 16px` | 500 | `0.01em` | Timestamps and metadata |
| `--type-badge` | `11px / 16px` | 650 | `0` | Pills and compact statuses |
| `--type-button` | `13px / 20px` | 650 | `0` | Controls |
| `--type-stat` | `28px / 34px` | 750 | `-0.025em` | Primary numerical values |
| `--type-code` | `13px / 20px` | 400 | `0` | JSON and machine-readable data |

On mobile, page titles are `32px / 40px` and section titles are `21px / 28px`.
Body copy should not fall below 14px. Paragraph text should normally stay below
72 characters per line; explanatory form copy should stay below 64.

### Spacing tokens

| Token | Value | Typical use |
| --- | --- | --- |
| `--space-0` | `0` | Reset |
| `--space-1` | `2px` | Optical alignment only |
| `--space-2` | `4px` | Tight icon/text relationship |
| `--space-3` | `8px` | Pills, compact row content |
| `--space-4` | `12px` | Control groups |
| `--space-5` | `16px` | Field and card internals |
| `--space-6` | `20px` | Standard card gap |
| `--space-7` | `24px` | Card padding, section rhythm |
| `--space-8` | `32px` | Page header and grid gap |
| `--space-9` | `40px` | Major section separation |
| `--space-10` | `48px` | Desktop page margin |
| `--space-11` | `64px` | Large editorial break |
| `--space-12` | `80px` | Exceptional breathing room |

Whitespace is structural. Do not compress a page simply to avoid scrolling.

### Radii, borders, and shadows

| Token | Value |
| --- | --- |
| `--radius-input` | `10px` |
| `--radius-button` | `10px` |
| `--radius-pill` | `999px` |
| `--radius-card-small` | `14px` |
| `--radius-card-large` | `18px` |
| `--radius-group` | `16px` |
| `--radius-modal` | `20px` |
| `--radius-mobile-nav` | `22px` |
| `--border-default` | `1px solid var(--color-border)` |
| `--border-subtle` | `1px solid var(--color-divider)` |
| `--border-dark` | `1px solid rgba(255,255,255,0.10)` |
| `--focus-ring` | `0 0 0 3px rgba(111,166,24,0.32)` |
| `--shadow-card` | `0 8px 24px rgba(20,38,44,0.07)` |
| `--shadow-elevated` | `0 14px 36px rgba(20,38,44,0.11)` |
| `--shadow-modal` | `0 24px 64px rgba(20,38,44,0.18)` |

Cards should feel softly elevated. Do not combine a strong border with a heavy
shadow. Dark feature panels use the dark border and little or no shadow.

### Motion tokens

| Token | Value | Use |
| --- | --- | --- |
| `--motion-fast` | `120ms` | Hover, focus, icon response |
| `--motion-standard` | `180ms` | Menus, pills, small state changes |
| `--motion-panel` | `240ms` | Expandable rows and panels |
| `--motion-overlay` | `280ms` | Drawers and modals |
| `--ease-standard` | `cubic-bezier(.2,.8,.2,1)` | General transitions |

Animate opacity, colour, and transform. Avoid layout-jarring animation. Hover
elevation is no more than a two-pixel translation. Progress movement communicates
real work and must not imply progress that the application cannot measure.
Under `prefers-reduced-motion: reduce`, remove transforms and use immediate or
near-immediate state changes.

## Breakpoints and responsive modes

| Token | Width | Mode |
| --- | --- | --- |
| `--breakpoint-sm` | `480px` | Compact mobile adjustments |
| `--breakpoint-md` | `768px` | Tablet begins |
| `--breakpoint-lg` | `1024px` | Large tablet / compact desktop |
| `--breakpoint-xl` | `1200px` | Full desktop shell begins |
| `--breakpoint-2xl` | `1536px` | Wide desktop composition |

- **Wide desktop, 1536px and above:** 248px expanded sidebar; content may use a
  12-column grid up to 1680px; 40–48px content padding; mixed card spans.
- **Standard desktop, 1200–1535px:** 232px sidebar; 32px content padding;
  12-column grid with 20–24px gaps.
- **Tablet, 768–1199px:** 72px icon rail or fully dismissible drawer depending
  on task complexity; 24px content padding; 8-column grid; secondary columns
  move below primary content.
- **Mobile, below 768px:** no squeezed sidebar; 16px page margin; 4-column grid;
  primary bottom navigation plus a secondary off-canvas menu.

Every page needs an explicit responsive composition. Merely reducing width is
not responsive design.

## Layout philosophy

The standard authenticated page rhythm is:

1. Application sidebar or mobile navigation
2. Breadcrumb, when it clarifies hierarchy
3. Large page title and restrained description
4. Utility and primary actions
5. Summary/statistics, when decision-relevant
6. Primary content
7. Secondary/supporting content

Primary content receives more visual space. Equal-width cards are appropriate
only for peer statistics, not as a default layout strategy. Mixed card sizes
must align to the grid and create a deliberate editorial hierarchy.

Recommended grid spans on wide desktop:

- full-width toolbar or table: 12 columns;
- paired operational feature panels: 6 + 6;
- dominant editor plus context: 9 + 3 or 8 + 4;
- dashboard support row: 3 cards of 4 columns or a deliberate 5 + 4 + 3;
- statistic cards: 2–3 columns each depending on count.

## Permanent application shell

### Desktop

- Fixed or sticky left sidebar, 232–248px wide, on a white surface.
- Project-owned logo and product name at the top; no borrowed marks.
- Navigation groups have clear headings or parent items and indented children.
- Active destination uses a deep-slate rounded rectangle with inverse text; a
  small lime icon/detail may reinforce the selection.
- Bottom sidebar area holds supported help/documentation actions and collapse.
- Content uses consistent 32–48px padding and may scroll independently.
- Top-right utilities belong in the page header: relevant time scope, supported
  notifications/help, and user menu. Do not show non-functional controls.
- Collapsed sidebar is a 72px icon rail with tooltips and persistent active state.

### Tablet

- Prefer a 72px rail when persistent navigation benefits the workflow.
- Use a drawer when the rail would compete with a dense editor or table.
- Actions remain 44px high and may wrap to a second line below the page title.
- Supporting side panels stack after the primary content in priority order.

### Mobile

- Use a floating or edge-anchored bottom bar for Dashboard, Products, Scanner,
  Issues/Operations, and More.
- The bar respects `env(safe-area-inset-bottom)` and has at least 8px external
  breathing room where device shape permits.
- Each destination combines an icon and short text label; do not use icons only.
- Minimum target size is 44×44px, preferred 48×48px.
- More opens an off-canvas menu containing every secondary destination.
- Primary page actions remain reachable near the title or in a sticky action
  region. Do not hide the only save action in an overflow menu.

## Navigation information architecture

```text
Dashboard

Catalogue
  Products
  Collections
  Variations
  Overrides

Scanner
  Scanner
  Schedules
  Logs

Data Quality
  Validation
  Issues

Metadata
  Reference
  Templates
  Examples

Operations
Integrations

Settings
  General
  Users
  Preferences
```

Unimplemented destinations render a polished planned-feature page that retains
the shell, names the intended outcome, and states that the feature is not yet
available. Never use blank templates, fake data presented as live, or raw 404s.

## Component state contract

Every interactive component must define the following states. A component may
omit a state only when it is logically impossible, and that omission should be
explicit in implementation notes.

| State | Required treatment |
| --- | --- |
| Default | Clear affordance, label, and adequate contrast |
| Hover | Subtle surface/border change; never the sole indication of action |
| Focus | Visible 3px focus ring with no layout shift |
| Active/pressed | Slightly stronger surface and immediate tactile response |
| Selected | Persistent icon/text/surface indication, not colour alone |
| Disabled | Reduced emphasis while retaining readable labels; no hover |
| Loading | Preserve dimensions; show spinner/skeleton and plain-language status |
| Success | Success icon plus vocabulary; announce meaningful changes |
| Warning | Amber icon plus text; state consequence or next action |
| Error | Error icon, concise message, and recovery action where possible |

Focus must remain visible on dark and light surfaces. Loading states must not
replace content with an unexplained spinner.

## Component library

### Buttons and compact controls

| Component | Default | Hover / active | Disabled / loading |
| --- | --- | --- | --- |
| Primary button | Dark surface, inverse text, optional leading icon | Dark-hover, up to 1px lift | Quiet grey surface; spinner retains label context |
| Secondary button | White, default border, primary text | Secondary surface, stronger border | Muted text and surface |
| Ghost button | Transparent, text/icon | Secondary surface | Muted without border |
| Destructive button | White or error-soft with error ink | Error background with inverse text | Muted error treatment |
| Icon button | 40–44px square/circle with accessible name | Secondary/elevated surface | Muted icon; tooltip remains unnecessary |
| Split/dropdown button | Primary action plus clearly divided menu trigger | Each region has its own state | Entire control or unavailable action disabled explicitly |

Primary actions are dark, not lime-filled by default. Lime may mark a selected
segment, healthy state, compact positive action, or focus detail.

### Tabs, badges, and pills

- Tabs use a restrained underline or selected soft surface. They are keyboard
  navigable and expose selected state to assistive technology.
- Status pills combine label and optional icon/dot. Height is 24–28px.
- Attribute pills use neutral grey; they are data, not statuses.
- Removable tag pills include a labelled remove action with a 32px target.
- Count badges remain compact and use tabular numerals.

### Inputs and selectors

- Standard height: 44px desktop and 48px mobile.
- White background, default border, 10px radius, primary text.
- Hover strengthens the border; focus adds the focus ring.
- Error uses error border, icon, and adjacent message.
- Read-only resolved values use secondary surface and a `Resolved`/source label.
- Disabled fields remain readable but are not confused with read-only data.
- Search combines a search icon, explicit placeholder, clear action, and visible
  result count when filtering changes the collection.
- Multi-select and tag controls wrap gracefully and never clip selected values.
- Toggles show a text state where ambiguity exists; keyboard and screen-reader
  state must be available.

### Alerts, toasts, dialogs, and drawers

- Inline alerts use a soft semantic background, icon, title, concise message,
  and optional action. They do not rely on a coloured border alone.
- Toasts confirm short-lived outcomes and do not contain critical errors that
  disappear. Region updates are announced politely.
- Dialogs use a 20px radius, modal shadow, 24–32px padding, labelled title, and
  predictable primary/cancel placement.
- Drawers use the elevated surface and preserve focus trapping/return.
- Destructive confirmation names the target and consequence.

### Progress, loading, and pagination

- Determinate progress shows percentage or completed/total values.
- Indeterminate progress is labelled `Working` or with the active operation.
- Skeletons mirror the final layout and use subtle shimmer only when motion is
  allowed.
- Pagination includes previous/next labels for assistive technology, current
  page state, and a results summary.
- Large datasets default to server-side pagination or incremental loading; the
  interface must not pretend that all data is loaded.

### Image picker and summary sidebar

- Image pickers show project data, alt text state, source, selection, and upload
  or browse actions only when supported.
- Summary sidebars use stacked small cards with source, validation, scan, and
  identity context. They become normal full-width sections below the main form
  on tablet/mobile and never become a permanently sticky obstruction.

## Card and surface variants

| Variant | Purpose and treatment | Responsive behaviour |
| --- | --- | --- |
| Statistic card | White, subtle border/shadow, 18px radius, 20–24px padding; soft icon tile, label, large tabular value, compact trend | Horizontal scroll or 2-column wrap on tablet; 1–2 columns on mobile |
| Standard content card | White, 18px radius, 24px padding, clear title and optional action | Full width when stacking |
| Dark feature card | Dark surface/border, inverse text, 18px radius, 24–28px padding; limited lime/teal/amber | Stacks without losing internal hierarchy |
| Grouped table card | White outer container with 16px radius and dark group header | Table adapts or scrolls inside container |
| Status card | White/soft semantic surface with icon, label, number/status, action | Joins grid or stacks by priority |
| Warning card | Warning-soft with warning icon/ink and recovery action | Full-width near affected content |
| Empty-state card | White or secondary surface; useful icon, plain explanation, one next action | Compact on mobile; never fills space decoratively |
| Side-summary card | White, 14–18px radius, 20px padding; compact facts and source | Moves below primary content |
| Image card | White with controlled thumbnail ratio and metadata | Thumbnail shrinks but does not become illegible |
| Login split card | Dark identity half plus light form half, elevated shadow, 20px radius | Single light form card; identity becomes compact header |
| Planned-feature card | White with one purposeful dark inset or header, honest status and route back | Full width, actions stack if needed |

Cards do not all need icons. Icons support recognition; they are not decoration.

## Tables and grouped data

Premium data presentation uses hierarchy rather than grid density.

### Anatomy

- **Container:** white surface, rounded outer corners, subtle border/shadow.
- **Toolbar:** dark surface for search, filters, sort, and result controls.
- **Grouped header:** dark slate with collection title, count, active/missing
  summaries, last update, expansion control, and overflow menu.
- **Column header:** 44px minimum, secondary/elevated surface, label text.
- **Data row:** 56–64px minimum, white, subtle bottom divider.
- **Hover row:** secondary surface; actions remain visible without hover.
- **Selected row:** lime-soft or teal-soft plus selection icon/checkbox.
- **Expanded child region:** inset secondary surface nested within the parent,
  with 12–16px margin and rounded corners.
- **Loading:** row-shaped skeletons and an announced loading state.
- **Empty:** one spanning region with explanation and relevant action/filter reset.
- **Error:** inline error region preserving filters and retry action.
- **Pagination:** results summary, page controls, and page-size control.

On mobile, prioritise product identity, SKU, status, and primary action. Secondary
columns become labelled key/value rows inside an expandable card. Horizontal
scrolling is acceptable only when column comparison is the task; it must be
contained, signposted, keyboard accessible, and never cause page overflow.

## Status vocabulary

| Status | Semantic treatment | Required non-colour cue |
| --- | --- | --- |
| Active | Lime-soft / lime ink | Check or `Active` label |
| Draft | Neutral secondary | Draft label |
| Missing | Warning-soft / warning ink | Dashed-circle/warning icon and label |
| Override | Warning-soft or teal-soft by context | `Override` label |
| Shared Only | Teal-soft / teal ink | `Shared Only` label/source icon |
| Valid | Success-soft / success ink | Check and `Valid` |
| Invalid | Error-soft / error ink | Error icon and `Invalid` |
| Running | Lime-soft or info-soft | Progress indicator and `Running` |
| Succeeded | Success-soft | Check and `Succeeded` |
| Failed | Error-soft | Error icon and `Failed` |
| Recovery Required | Warning-soft | Recovery icon and full label |
| Warning | Warning-soft | Warning icon and label |
| Pending | Neutral/info-soft | Clock and `Pending` |
| Planned | Neutral outline or teal-soft | `Planned` label |

`Missing`, `Warning`, and `Recovery Required` may share amber semantics but must
retain distinct text. `Failed` and `Invalid` use error semantics. Do not assign
new colours merely to make every status unique.

## Dashboard specification

The Dashboard must answer within five seconds:

- Is the catalogue healthy?
- Are scans running?
- Are there errors?
- What changed recently?
- What needs attention?
- Which products were recently updated?

### Canonical hierarchy

1. Page title, description, time context, and refresh where meaningful
2. Summary statistic cards
3. Large dark Catalogue Health panel
4. Large dark Scanner Activity panel
5. Recent Changes
6. Needs Attention
7. Metadata Issues
8. Recent Products
9. Optional recent operation history

Health and scanner panels are allowed to occupy a substantial part of the page.
They use real status data and clear legends; no decorative chart may imply data
that is unavailable. Statistics should link to a relevant filtered destination
when that destination exists.

### Responsive behaviour

- Wide/desktop: summary cards form a peer row; dark panels share the principal
  row; supporting cards form a mixed three-card row; products span full width.
- Tablet: statistics use two or three columns; dark panels stack; supporting
  cards use two columns then one.
- Mobile: a horizontally scrollable, snap-aligned statistic strip or two-column
  grid; all other panels stack in the canonical order; tables become product
  cards or a concise recent-item list.

## Products specification

Products is the reference page for every data-heavy experience.

### Page hierarchy

1. Title, description, export/add actions only when supported
2. Collection/product/variation/missing/attention statistics
3. Dark search/filter/sort toolbar
4. Incomplete-data or validation notice when applicable
5. Collection-grouped product presentation
6. Pagination and results summary

### Collection group

The group header contains collection name, product count, active and missing
counts, last-updated information, expansion state, and overflow actions. Parent
products appear before variation detail. A product row contains:

- thumbnail or consistent placeholder;
- title and internal identity where useful;
- SKU;
- simple/variable type label;
- price where projected;
- catalogue status;
- variation count;
- shared/override/source state;
- last-updated information;
- visible `Edit metadata` action and secondary row actions.

Metadata editing remains prominent. Do not bury it exclusively in an overflow
menu.

### Variation expansion

- Expansion is lazy: fetch or render variation detail only when requested.
- The trigger is a real button with `aria-expanded` and `aria-controls`.
- The parent remains visually connected to the inset child region.
- Show visible total count even before expansion.
- Preview includes variation SKU, attribute pills, price, stock when available,
  status, override state, and last update.
- Provide `View all N variations` when the preview is truncated.
- Escape collapses when focus is within an expanded region; focus is not lost.
- Loading shows nested skeleton rows; error retains parent and offers retry;
  zero variations shows an explicit empty statement.

### Large-data behaviour

Search, filters, sorting, collection expansion, and pagination preserve state in
the URL where practical. Collapsed groups do not eagerly render thousands of
variations. Density remains calm: no zebra striping with high contrast, no
unlabelled action icon clusters, and no full-page horizontal scroll.

## Product Detail direction

The future detail page uses the standard shell and should include:

- product identity, image, SKU, and status;
- source collection and product type;
- scanner-resolved information;
- metadata validity and source;
- parent attributes and variation overview;
- collection defaults, product overrides, and resolved value comparison;
- recent scan/operation history;
- supported product actions;
- a primary route into guided metadata editing.

Use a dominant main column with a supporting context column. Parent identity
remains visible above variation content; variations never read as peer products.

## Metadata Editor specification

The default is a guided, field-based CMS experience. Raw JSON is secondary.

### Page structure

- Breadcrumb: Catalogue → Products → Product → Edit Metadata.
- Large title, supporting description, and clear Cancel, Validate, Advanced
  JSON, and Save actions according to actual support.
- Main form column with grouped sections.
- Supporting column for source, validation, last scan, product summary, and
  supported quick actions.
- Save/validation state remains visible during long forms, using a sticky action
  bar only when it does not obscure content.

### Guided sections

1. Basic Information
2. Categories and Tags
3. Short Description
4. SEO Metadata
5. Publishing/Live status
6. Attributes
7. Image Attributes
8. Variation Modifiers
9. Shipping and Dimensions
10. Advanced fields where the scanner contract supports them

Each section has a title, one-sentence purpose, and collapsible behaviour only
when collapse improves navigation. Fields use labels above controls, helper text
below, inline validation, and counters where a real limit exists.

Repeatable data uses rows with explicit Add, Remove, and reorder controls.
Drag-and-drop must have keyboard alternatives. Do not invent WooCommerce limits
that are not represented by the protected contract.

### Provenance and inheritance

Every relevant value can be identified as one of:

- **Collection default** — authored at shared collection level;
- **Product override** — authored specifically for the product;
- **Resolved output** — scanner result, read-only in the editor.

Use text labels, source icons, helper copy, and optional side-by-side comparison.
Do not use colour alone. Inherited fields should not appear blank. Clearing an
override must explain the resulting inherited value before save.

### Validation and save

- Validate before save where safe, without silently changing data.
- Place field errors next to fields and summarise them at the top.
- Preserve user input after validation failure.
- Show saving state without enabling duplicate submissions.
- Confirm success and show the resulting operation/update state.
- Warn before navigation with unsaved changes.
- Scanner-resolved values are not directly editable.

### Responsive behaviour

- Desktop: 8+4 or 9+3 grid; action row aligns with page title.
- Tablet: form spans full width; support cards follow the form or move between
  logical sections; actions wrap without losing Save prominence.
- Mobile: single-column sections; summary cards follow Basic Information;
  primary Save is reachable in a safe-area-aware sticky bar; repeatable rows
  become labelled cards rather than squeezed grids.

## Advanced JSON mode

Advanced JSON is an expert mode reached through an explicit action or tab. It
does not replace or visually detach from the guided editor.

Required capabilities:

- warning that the full authored document is exposed;
- dark slate editor consistent with feature panels;
- syntax highlighting with accessible token contrast;
- line numbers, search, formatting, and schema validation;
- inline and summary schema errors with line/field location;
- save confirmation and duplicate-submit protection;
- unsaved-change warning;
- recovery after invalid JSON without losing the draft;
- explicit return to guided editing;
- concise source/provenance context.

The editor uses `--color-code-surface`, 13/20px monospace text, a visible caret,
selection treatment, 16px minimum padding, and a focus ring. Never use a
transparent textarea over an unrelated background.

## Login specification

The unauthenticated Login page removes the application shell and dashboard
content.

- Warm off-white full-page canvas with extremely subtle geometric texture only
  if project-owned and non-distracting.
- Centred split card, approximately 960–1040px wide and 600–680px tall on a
  standard desktop.
- Dark identity panel occupies about 42–46%; light form panel occupies the rest.
- Identity panel uses the project logo, product name, and one short proposition.
- Form panel contains title, restrained description, the authentication fields
  the application actually supports, password visibility, remember-me, password
  recovery, and a primary dark sign-in button.
- Optional account/support links appear only when the corresponding flow exists.
- No social login or create-account promise unless implemented and approved.

If authentication currently requires a username, the UI must say `Username`.
It may move to `Email address` only when the authentication contract supports
email login. Visual references do not authorise behavioural changes.

On mobile the dark identity panel becomes a compact branded header or is reduced
to the mark and product name. The form is one column, fills available width with
16–20px margins, and remains vertically usable with the software keyboard.

## Forms and long-form composition

- Use two-column field grids only when fields are naturally paired.
- Use a single column on mobile.
- Group fields into cards/sections; never produce an uninterrupted wall.
- Section separation is 32–40px; field separation is 16–20px.
- Required indicators are textual/symbolic and explained once.
- Error summaries link to invalid fields.
- Destructive actions are separated from ordinary save actions.
- Read-only scanner output uses labelled secondary surfaces.
- Unsaved-change behaviour is consistent across guided and JSON modes.
- Modal forms use 24–32px padding; full-page editors are preferred for complex
  metadata.

## Empty, error, and planned states

Every data view implements:

- first-use empty state with one useful next step;
- filtered empty state with a clear-filter action;
- loading state shaped like expected content;
- recoverable error with retry and preserved context;
- permission error without leaking protected information;
- planned state that is visually complete and factually honest.

Avoid oversized illustrations. A project-owned line icon, concise explanation,
and one primary action are sufficient.

## Accessibility requirements

- Meet WCAG AA contrast for text and interface components.
- Preserve a visible focus indicator on every surface.
- Use logical DOM and tab order matching visual order.
- Label all controls; icon-only controls require accessible names.
- Provide meaningful image alternatives; decorative imagery has empty alt text.
- Expandable rows, accordions, tabs, menus, dialogs, and drawers are keyboard
  operable with correct states.
- Announce scan, validation, save, and status changes through appropriate live
  regions without excessive chatter.
- Use 44×44px minimum touch targets.
- Support reduced motion and 200% text zoom without loss of content.
- Never communicate status by colour alone.
- Keep code text at least 13px with 1.5 line height and strong contrast.
- Preserve table headers and relationships for assistive technology even when
  the mobile visual presentation becomes cards.

## Correct and incorrect usage

### Correct

- A white Products page uses a deep-slate filter bar and collection headers,
  while ordinary rows remain light and spacious.
- A dashboard gives its two most important operational summaries large dark
  panels and keeps supporting activity on white cards.
- Lime marks the active navigation detail, healthy pill, and a small trend—not
  the entire page or every button.
- Metadata editing begins with guided fields and keeps source/resolved context
  visible; Advanced JSON remains one deliberate expert action away.
- On mobile, the sidebar becomes bottom navigation and More drawer; the content
  is recomposed rather than squeezed.

### Incorrect

- Turning the whole application dark because some reference panels are dark.
- Applying lime backgrounds to whole cards, headers, or the canvas.
- Using equal Bootstrap columns for every section regardless of importance.
- Rendering parent products and variations as unrelated flat rows.
- Showing a raw JSON textarea as the primary metadata experience.
- Hiding critical save/edit actions in hover-only or overflow-only controls.
- Adding date pickers, notifications, export, Add Product, social login, or
  support links solely because they appear in a mockup.
- Copying the mockup's sample logo, people, product imagery, wording, or data.
- Introducing page-specific colours, radii, and shadows instead of shared tokens.
- Allowing a wide table to create full-page horizontal scrolling.

## Implementation acceptance checklist

Before a page is accepted:

1. It clearly belongs beside all four canonical references.
2. Warm white and white dominate; dark emphasis has a stated purpose.
3. It uses semantic tokens and shared components.
4. Its hierarchy identifies one primary purpose and action.
5. Real application capabilities and scanner data drive every claim.
6. Default, hover, focus, active, selected, disabled, loading, success, warning,
   and error states have been considered.
7. Wide desktop, desktop, tablet, and mobile compositions have been designed.
8. Keyboard, screen-reader, contrast, reduced-motion, and zoom checks pass.
9. Empty, loading, filtered-empty, error, and planned states are complete.
10. The page introduces no borrowed brand asset or unsupported functionality.

## Permanent design principles

- Clarity before decoration.
- Consistency before novelty.
- Light canvas, purposeful dark emphasis.
- Lime is an accent, not a background theme.
- Hierarchy determines surface choice.
- Scanner data remains authoritative.
- Inheritance and overrides must be understandable.
- Whitespace is intentional.
- Tables are readable, calm, and relational.
- Guided editing first, raw JSON second.
- Accessibility is the default.
- Every page has one primary purpose.
- Shared components come before page-specific invention.
- Responsive behaviour is designed, not improvised.
- The approved Dashboard, Products, Login, and Metadata Editor references define
  the visual standard.
