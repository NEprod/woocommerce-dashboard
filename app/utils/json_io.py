import json, os, tempfile, shutil


def prune_empties(data):
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            vv = prune_empties(v)
            if vv not in ("", None, [], {}):
                out[k] = vv
        return out
    if isinstance(data, list):
        out = [prune_empties(i) for i in data]
        return [i for i in out if i not in ("", None, [], {})]
    return data


def atomic_write_json(
    path: str, data: dict, ensure_ascii: bool = False, indent: int = 2
):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_dir = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=tmp_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        shutil.move(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
