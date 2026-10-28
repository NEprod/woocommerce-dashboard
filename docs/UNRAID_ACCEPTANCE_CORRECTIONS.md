# Two acceptance corrections after b124edadefc4

## Obsolete Attention scheduled entry

The settlement implementation passed its worker tests, but the scheduler's
clean-view early exit prevented that worker from running. Unraid had zero dirty
views and zero missing projections; its matching 3DCO queue remained Attention.

The existing five-minute entry and bounded worker now share one candidate rule.
Matching bootstrap/source-prerequisite Attention may enqueue the existing view
task even when views are clean. All actual settlement, source, identity, ownership,
uncertain-write and recovery checks remain in the original settlement function.
No scan, Shopify request or publishing mutation is introduced.

An unsuccessful check stores only a digest of the evaluated queue/identity/
comparison/context evidence in the existing derived `view_json`. Unchanged,
permanently ineligible evidence does not enqueue repeated work. Changed scan,
comparison, identity or context evidence permits re-evaluation. Active operation
ownership defers evaluation rather than recording a permanent rejection. This
index is not a scan checkpoint, verification timestamp or mutation authority.

After successful settlement, idle entries produce no new runs or source walks.
The normal task bookkeeping and existing leases remain unchanged. Original errors,
failed operations and all remote receipts remain historical evidence.

## Draft channel preferences

The real 16bit-0003 override authors `live: false` while inheriting its Live
collection's Online Store/Shop/POS selections. Those are retained preferences,
not instructions to activate an effective Draft.

The existing publication code now derives immediate Draft intent with no channel
actions. Canonical authored intent and all JSON values are untouched. Live intent
still uses all configured channels, and Archived conflicts remain guarded.
Readiness, signed publication previews, proof matching and readback comparison
use the same immediate intent. Existing automatic Draft metadata pause and
status-only Live→Draft execution are unchanged.

A valid uncreated Draft projects **Local Draft**. The old exact authored
Draft/channel finding can be superseded in that local-intent display without
rewriting or renewing the historical incomplete Shopify comparison. No remote
absence/currentness is inferred from local Draft intent. Genuine API, identity,
customisation and other source failures remain visible; recovery is not cleared.

The saved-view context policy changes once so existing derived views are rebuilt
by the normal worker. This is not a local catalogue scan or a remote refresh.
No new schema, migration, queue system or authored field is introduced.

## Focused coverage and acceptance

Tests use non-manual scheduler enqueue and the supervisor tick, real persisted
task claims, contention, scheduled clean-view settlement, idle no-ops and stable
ineligible evidence. Fixtures cover the 14-variant/53-image case, bootstrap
matching evidence, inherited Draft channels, linked Draft metadata pause and
Draft→Live using the same identity/channels. Four older reporting fixtures now
use Archived conflicts or Live required-read failures rather than the deliberately
corrected Draft behaviour; their failure/lifecycle assertions remain intact.

Keep automatic publishing **paused** on Unraid. Back up `/app/instance` and
catalogue, then explicitly deploy the immutable candidate with existing mounts.
After the existing startup grace and next five-minute view pass, confirm:

1. 3DCO/HERO settle to No Changes if their evidence still matches. Original failed
   receipts remain, no scan checkpoint is fabricated, and no publishing event is sent.
2. 16bit-0003 displays Local Draft without changing its JSON or creating Shopify data.
3. Real Create/Update prerequisites and protected Recovery Required remain.
4. Later idle passes create no repetitive settlement runs.

Do not run scans, reconciliation or publishing just to clear these two labels.
Draft→Live acceptance requires an authorised isolated staging store, not the live
store. Roll back to `obsolete-attention-b124edadefc4` with the same persistent
mounts and paused authority if needed. No automatic Unraid deployment is performed.
