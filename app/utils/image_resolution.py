"""Pure scanner-compatible Single Variable image-layout analysis.

The scanner owns emitted rows.  This module describes that established folder
selection without reading or mutating the filesystem so Intake validation can
use the same ownership and fallback vocabulary.
"""

from __future__ import annotations

from itertools import product
from pathlib import PurePosixPath

from app.utils.catalogue_paths import is_reserved_directory_name


def _finding(code, message, *, state="blocking", path="$.image_attributes"):
    return {"code": code, "message": message, "path": path, "state": state}


def _parts(value):
    return tuple(PurePosixPath(str(value)).parts)


def _display(parts):
    return f"{PurePosixPath(*parts).as_posix()}/" if parts else "collection root"


def _image_path(value):
    if isinstance(value, dict):
        value = value.get("path", "")
    return _parts(value)


def resolve_single_variable_image_layout(document, folders, images):
    """Return deterministic ownership/fallback results matching the scanner.

    Scanner behaviour protected here:
    - the first ``image_attributes`` value selects a required root folder;
    - direct images in that folder are reusable broader images;
    - a configured subsequent image-attribute folder may add exact images;
    - collection-root Parent images remain parent-owned and are only a UI
      preview fallback when a variation row has no own image.
    """

    attributes = document.get("attributes") if isinstance(document.get("attributes"), dict) else {}
    image_attributes = document.get("image_attributes") if isinstance(document.get("image_attributes"), list) else []
    folder_parts = [_parts(value) for value in folders]
    folder_set = set(folder_parts)
    image_parts = [_image_path(value) for value in images]
    direct_images = {}
    for parts in image_parts:
        if not parts:
            continue
        direct_images.setdefault(parts[:-1], []).append(parts[-1])

    root_folders = [parts[0] for parts in folder_parts if len(parts) == 1]
    parent_folders = [name for name in root_folders if is_reserved_directory_name(name)]
    product_folders = [name for name in root_folders if not is_reserved_directory_name(name)]
    findings = []
    if len(parent_folders) > 1:
        findings.append(_finding(
            "duplicate_parent",
            "Multiple case variants of the reserved Parent directory are ambiguous.",
        ))

    if not image_attributes:
        findings.append(_finding(
            "image_attributes_required",
            "Single Variable folder validation requires ordered image attributes.",
        ))
    unknown_attributes = [name for name in image_attributes if name not in attributes]
    for name in unknown_attributes:
        findings.append(_finding(
            "unknown_image_attribute",
            f"Image attribute ‘{name}’ is not a defined attribute.",
        ))

    expected_depth = len(image_attributes)
    first_values = attributes.get(image_attributes[0], []) if image_attributes and not unknown_attributes else []
    first_values = first_values if isinstance(first_values, list) else []
    known_first = {str(value) for value in first_values}
    unknown_roots = sorted(
        {name for name in product_folders if name not in known_first},
        key=lambda value: (value.casefold(), value),
    )
    if unknown_roots:
        examples = ", ".join(f"‘{name}/’" for name in unknown_roots[:3])
        findings.append(_finding(
            "unexplained_folders",
            f"Image-owner folder values are not defined by the first image attribute: {examples}.",
        ))

    missing_owner_folders = [str(value) for value in first_values if (str(value),) not in folder_set]
    if missing_owner_folders:
        examples = ", ".join(f"‘{name}/’" for name in missing_owner_folders[:3])
        findings.append(_finding(
            "missing_image_owner_folder",
            f"Required first image-attribute folders are missing: {examples}. The scanner cannot resolve these variation owners.",
        ))

    unexplained_paths = set()
    too_deep = set()
    root_images = 0
    visible_exact = set()
    expected_values = [attributes.get(name, []) for name in image_attributes]
    expected_image_combinations = set()
    if expected_values and all(isinstance(values, list) for values in expected_values):
        expected_image_combinations = {
            tuple(str(value) for value in combination)
            for combination in product(*expected_values)
        }
    for parts in direct_images:
        if not parts:
            root_images += len(direct_images[parts])
            continue
        if is_reserved_directory_name(parts[0]):
            if len(parts) > 1:
                too_deep.add(parts)
            continue
        if expected_depth and len(parts) > expected_depth:
            too_deep.add(parts)
            continue
        if parts[0] not in known_first:
            continue
        if expected_depth and len(parts) == expected_depth:
            if parts in expected_image_combinations:
                visible_exact.add(parts)
            else:
                unexplained_paths.add(parts)
        elif len(parts) > 1:
            # The scanner only recognises configured image-attribute values;
            # nested folders named Parent are not reserved.
            unexplained_paths.add(parts)

    if root_images:
        findings.append(_finding(
            "unresolved_image_owner",
            "Images at the Prepared result root have no scanner image owner.",
        ))
    if too_deep:
        examples = ", ".join(f"‘{_display(parts)}’" for parts in sorted(too_deep)[:3])
        findings.append(_finding(
            "unsupported_depth",
            f"Image folders are deeper than the {expected_depth} configured image-attribute level(s): {examples}.",
        ))
    if unexplained_paths:
        examples = ", ".join(f"‘{_display(parts)}’" for parts in sorted(unexplained_paths)[:3])
        findings.append(_finding(
            "unexplained_folders",
            f"Image folders are not explained by the ordered image attributes: {examples}.",
        ))

    all_attribute_names = list(attributes)
    all_attribute_values = [values for values in attributes.values()]
    combinations = []
    if all_attribute_values and all(isinstance(values, list) for values in all_attribute_values):
        combinations = [
            dict(zip(all_attribute_names, values))
            for values in product(*all_attribute_values)
        ]

    parent_path = (parent_folders[0],) if len(parent_folders) == 1 else None
    parent_image_count = len(direct_images.get(parent_path, [])) if parent_path else 0
    resolutions = []
    for variation in combinations:
        if not image_attributes or unknown_attributes:
            continue
        requested = tuple(str(variation[name]) for name in image_attributes)
        base = requested[:1]
        base_exists = base in folder_set
        base_images = direct_images.get(base, [])
        exact_images = direct_images.get(requested, [])
        if exact_images:
            resolution_type = "exact"
            resolved = requested
            severity = "ready"
            owner = "variation"
            scanner_count = len(exact_images) + (len(base_images) if requested != base else 0)
        elif base_images:
            resolution_type = "exact" if requested == base else "broader"
            resolved = base
            severity = "ready" if requested == base else "warning"
            owner = "variation"
            scanner_count = len(base_images)
        elif base_exists and parent_image_count:
            # Scanner variation rows stay empty here; the application may use
            # the genuine parent source as an explicitly labelled preview.
            resolution_type = "parent"
            resolved = parent_path
            severity = "warning"
            owner = "parent"
            scanner_count = 0
        else:
            resolution_type = "missing"
            resolved = None
            severity = "blocking"
            owner = "none"
            scanner_count = 0
        resolutions.append({
            "attributes": variation,
            "requested_path": _display(requested),
            "resolved_path": _display(resolved) if resolved else None,
            "resolution_type": resolution_type,
            "severity": severity,
            "owner_type": owner,
            "source_image_count": len(direct_images.get(resolved, [])) if resolved else 0,
            "scanner_image_count": scanner_count,
        })

    broader = [row for row in resolutions if row["resolution_type"] == "broader"]
    parent_fallback = [row for row in resolutions if row["resolution_type"] == "parent"]
    missing = [row for row in resolutions if row["resolution_type"] == "missing"]
    if broader:
        example = broader[0]
        findings.append(_finding(
            "image_fallback_broader",
            f"{len(broader)} variation(s) use scanner-supported broader images; requested ‘{example['requested_path']}’, resolved ‘{example['resolved_path']}’. Handoff remains allowed.",
            state="warning",
        ))
    if parent_fallback:
        example = parent_fallback[0]
        findings.append(_finding(
            "image_fallback_parent",
            f"{len(parent_fallback)} variation(s) have no variation-owned image; preview fallback uses ‘{example['resolved_path']}’. Parent ownership is preserved and handoff remains allowed.",
            state="warning",
        ))
    if missing:
        example = missing[0]
        findings.append(_finding(
            "missing_image_source",
            f"{len(missing)} variation(s) have no usable exact, broader, or Parent image source; first unresolved owner is ‘{example['requested_path']}’.",
        ))

    exact_count = sum(row["resolution_type"] == "exact" for row in resolutions)
    fallback_count = sum(row["resolution_type"] in {"broader", "parent"} for row in resolutions)
    missing_count = sum(row["resolution_type"] == "missing" for row in resolutions)
    return {
        "expected_depth": expected_depth,
        "expected_variations": len(combinations),
        "visible_variations": len(visible_exact),
        "root_folders": root_folders,
        "parent_folders": parent_folders,
        "product_folders": product_folders,
        "resolutions": resolutions,
        "image_health": {"exact": exact_count, "fallback": fallback_count, "missing": missing_count},
        "findings": findings,
    }
