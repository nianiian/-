import argparse
import csv
import json
from pathlib import Path


def count_papers(new_output_dir: str = "new_output") -> list[tuple[str, int]]:
    base = Path(new_output_dir)
    results: list[tuple[str, int]] = []

    if not base.exists() or not base.is_dir():
        raise SystemExit(f"Directory not found: {base}")

    for jf in sorted(base.glob("*_result.json")):
        compound = jf.stem.removesuffix("_result")
        try:
            with jf.open("r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data) if isinstance(data, list) else 0
        except Exception:
            count = 0
        results.append((compound, count))

    return results


def write_csv(rows: list[tuple[str, int]], out_csv: str) -> None:
    out_path = Path(out_csv)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["compound", "paper_count"])
        for compound, cnt in rows:
            w.writerow([compound, cnt])


def main():
    parser = argparse.ArgumentParser(description="Count papers per compound in new_output and write CSV")
    parser.add_argument("--dir", default="new_output", help="Input directory containing *_result.json (default: new_output)")
    parser.add_argument("--out", default="new_output_summary.csv", help="Output CSV path (default: new_output_summary.csv)")
    args = parser.parse_args()

    rows = count_papers(args.dir)
    write_csv(rows, args.out)

    total = sum(cnt for _, cnt in rows)
    print(f"Wrote {len(rows)} compounds, {total} total papers -> {args.out}")


if __name__ == "__main__":
    main()

