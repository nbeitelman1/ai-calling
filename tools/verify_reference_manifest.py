"""Cross-platform verifier for the untouched independent reference pack."""
import hashlib
import json
import sys
from pathlib import Path


def main(root: str) -> int:
    root_path = Path(root).resolve()
    manifest = json.loads((root_path / "MANIFEST.json").read_text(encoding="utf-8"))
    rows = manifest.get("files", {})
    errors = []
    for name, expected in rows.items():
        relative = Path(*name.split("/"))
        path = (root_path / relative).resolve()
        if root_path not in path.parents or not path.is_file() or path.is_symlink():
            errors.append(f"{name}: missing or unsafe")
            continue
        data = path.read_bytes()
        if len(data) != expected["bytes"]:
            errors.append(f"{name}: size mismatch")
        if hashlib.sha256(data).hexdigest() != expected["sha256"]:
            errors.append(f"{name}: hash mismatch")
    listed = set(rows)
    extras = sorted(
        path.relative_to(root_path).as_posix()
        for path in root_path.rglob("*.py")
        if "__pycache__" not in path.parts
        and path.relative_to(root_path).as_posix() not in listed
    )
    result = {
        "scope": "BYTE_INTEGRITY_NOT_AUTHENTICITY",
        "files_checked": len(rows),
        "errors": errors,
        "unlisted_python_files": extras,
        "pass": len(rows) == 28 and not errors and not extras,
    }
    print(json.dumps(result, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
