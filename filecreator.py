import argparse
import os
import shutil
import json
from pathlib import Path


def rename_step4_results(outputs_dir: str = "outputs", overwrite: bool = False) -> None:
    """
    Iterate over each chemical folder in `outputs_dir`, find `step04_results.json`,
    and rename it to `<chemical>_result.json` in the same folder.

    - outputs_dir: base directory containing per-chemical subfolders
    - overwrite: if True, replaces an existing `<chemical>_result.json`
    """

    base = Path(outputs_dir)
    if not base.exists() or not base.is_dir():
        print(f"[WARN] Outputs directory not found: {base}")
        return

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue

        compound = child.name
        src = child / "step04_results.json"
        if not src.exists():
            # Skip folders without step04_results.json
            continue

        dst = child / f"{compound}_result.json"

        try:
            if dst.exists():
                if overwrite:
                    # Remove existing destination when overwrite is requested
                    dst.unlink()
                else:
                    print(f"[SKIP] {compound}: destination exists -> {dst}")
                    continue

            src.rename(dst)
            print(f"[OK] {compound}: renamed '{src.name}' -> '{dst.name}'")
        except OSError as e:
            print(f"[ERROR] {compound}: failed to rename: {e}")


def collect_compound_results(
    outputs_dir: str = "outputs", dest_dir: str = "new_output", overwrite: bool = False
) -> None:
    """
    Collect per-compound result JSONs into a flat destination folder.

    Preference order per compound:
    1) <compound>_result.json
    2) step04_results.json (copied as <compound>_result.json)

    - outputs_dir: base directory containing per-chemical subfolders
    - dest_dir: destination folder to place collected files
    - overwrite: if True, replaces an existing destination file
    """

    base = Path(outputs_dir)
    out = Path(dest_dir)

    if not base.exists() or not base.is_dir():
        print(f"[WARN] Outputs directory not found: {base}")
        return

    out.mkdir(parents=True, exist_ok=True)

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue

        compound = child.name
        preferred = child / f"{compound}_result.json"
        fallback = child / "step04_results.json"

        if preferred.exists():
            src = preferred
        elif fallback.exists():
            src = fallback
        else:
            # Nothing to collect for this compound
            continue

        dest_file = out / f"{compound}_result.json"

        try:
            if dest_file.exists() and not overwrite:
                print(f"[SKIP] {compound}: destination exists -> {dest_file}")
                continue

            # Copy and always name as <compound>_result.json in dest
            shutil.copyfile(src, dest_file)
            print(f"[OK] {compound}: collected -> {dest_file}")
        except OSError as e:
            print(f"[ERROR] {compound}: failed to collect: {e}")


def _is_empty_alt(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return len(value.strip()) == 0
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def prune_empty_alternatives(dest_dir: str = "new_output") -> None:
    """
    In `dest_dir`, open each `<compound>_result.json` (list of records),
    remove entries whose alternative/alternatives field is empty or missing,
    and overwrite the file. Prints counts per file.
    """

    out = Path(dest_dir)
    if not out.exists() or not out.is_dir():
        print(f"[WARN] Destination directory not found: {out}")
        return

    files = sorted(out.glob("*_result.json"))
    for jf in files:
        try:
            with jf.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[ERROR] {jf.name}: failed to read JSON: {e}")
            continue

        if not isinstance(data, list):
            print(f"[SKIP] {jf.name}: not a list JSON")
            continue

        def has_nonempty_alt(rec: dict) -> bool:
            if not isinstance(rec, dict):
                return False
            alt = rec.get("alternatives")
            alt2 = rec.get("alternative")
            # keep if any alternative field is non-empty
            return (not _is_empty_alt(alt)) or (not _is_empty_alt(alt2))

        original = len(data)
        filtered = [r for r in data if has_nonempty_alt(r)]
        removed = original - len(filtered)

        if removed <= 0:
            print(f"[OK] {jf.name}: no empty-alternative entries")
            continue

        try:
            with jf.open("w", encoding="utf-8") as f:
                json.dump(filtered, f, ensure_ascii=False, indent=2)
            print(f"[OK] {jf.name}: removed {removed} entries (kept {len(filtered)}/{original})")
        except Exception as e:
            print(f"[ERROR] {jf.name}: failed to write JSON: {e}")

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Rename each outputs/<chemical>/step04_results.json to "
            "outputs/<chemical>/<chemical>_result.json"
        )
    )
    parser.add_argument(
        "--outputs-dir",
        default="outputs",
        help="Path to outputs directory (default: outputs)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing <chemical>_result.json if present",
    )
    parser.add_argument(
        "--collect",
        action="store_true",
        help=(
            "Copy each <chemical>_result.json into new_output/ as "
            "new_output/<chemical>_result.json; falls back to step04_results.json"
        ),
    )
    parser.add_argument(
        "--dest-dir",
        default="new_output",
        help="Destination folder for --collect (default: new_output)",
    )
    parser.add_argument(
        "--prune-empty-alt",
        action="store_true",
        help=(
            "In dest-dir, remove records whose alternative/alternatives is empty, then overwrite files"
        ),
    )
    args = parser.parse_args()

    # Always attempt rename first so downstream collect finds normalized names
    rename_step4_results(outputs_dir=args.outputs_dir, overwrite=args.overwrite)

    if args.collect:
        collect_compound_results(
            outputs_dir=args.outputs_dir,
            dest_dir=args.dest_dir,
            overwrite=args.overwrite,
        )

    if args.prune_empty_alt:
        prune_empty_alternatives(dest_dir=args.dest_dir)


if __name__ == "__main__":
    main()
