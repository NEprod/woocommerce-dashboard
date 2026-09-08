"""Presentation only: group an existing sync snapshot without reads or eligibility changes."""

VIEWS = {"categories": "Categories", "attributes": "Attributes & Terms",
         "storefront_collections": "Storefront Collections → Woo Brands"}
BUCKETS = {"link": "Needs Link", "create": "Needs Create", "order": "Ordering correction", "verified": "Verified",
           "issues": "Issues", "import": "Available to import from Woo"}


def sync_views(plan=None):
    result = []
    for kind, title in VIEWS.items():
        rows = [r for r in (plan or {}).get("rows", [])
                if r["kind"] == kind or (kind == "attributes" and r["kind"] == "terms")]
        nodes = {}
        for row in rows:
            bucket = row.get("action") or ("verified" if row["state"] == "verified" else "issues")
            nodes[row["id"]] = {"row": row, "children": [], "bucket": bucket,
                                "label": BUCKETS[bucket]}
        local = {r["key"]: r["id"] for r in rows if not r["id"].startswith("remote:") and r["kind"] == kind}
        remote = {r["remote"]["id"]: r["id"] for r in rows if r.get("remote") and r["kind"] == kind}
        parents = {}
        for row in rows:
            parent = None
            if row["kind"] == "terms":
                parent = local.get(row["scope"])
            elif kind == "categories":
                parent = (remote.get(row.get("remote", {}).get("parent")) if row["id"].startswith("remote:")
                          else local.get((row.get("local") or {}).get("parent")))
            # Defensive against malformed remote hierarchy; never recurse in a cycle.
            cursor, seen = parent, {row["id"]}
            while cursor and cursor not in seen:
                seen.add(cursor)
                cursor = parents.get(cursor)
            if cursor not in seen:
                parents[row["id"]] = parent
        roots = []
        for key, node in nodes.items():
            parent = parents.get(key)
            (nodes[parent]["children"] if parent in nodes else roots).append(node)
        def decorate(branch):
            branch.sort(key=lambda n: ((n["row"].get("local") or {}).get("order", 0), n["row"]["name"].casefold()))
            for node in branch:
                decorate(node["children"])
                counts = {b: int(node["bucket"] == b) for b in BUCKETS}
                for child in node["children"]:
                    for b in counts:
                        counts[b] += child["counts"][b]
                node["counts"] = counts
        decorate(roots)
        counts = {b: sum(n["bucket"] == b for n in nodes.values()) for b in BUCKETS}
        unavailable = [u for u in (plan or {}).get("unavailable", [])
                       if u["kind"] == kind or (kind == "attributes" and u["kind"].startswith("terms:"))]
        result.append({"key": kind, "title": title, "roots": roots, "counts": counts,
                       "unavailable": unavailable, "buckets": BUCKETS})
    return result
