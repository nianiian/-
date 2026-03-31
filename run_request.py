import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from tqdm import tqdm

# Reuse existing controller without running full batch
from pipeline_controller import PipelineController, INPUT_CSV, OUTPUT_BASE_DIR, COMPOUND_COLUMN


def parse_request(fp: Path) -> tuple[str, str]:
    with fp.open("r", encoding="utf-8") as f:
        data = json.load(f)
    cid = str(data.get("CID") or data.get("cid") or "").strip()
    time_str = str(data.get("Time") or data.get("date") or data.get("time") or "").strip()
    if not cid or not time_str:
        raise ValueError("Request JSON must include 'CID' and 'Time'.")
    # Normalize date to YYYYMMDD (accept YYYY/MM/DD or YYYY-MM-DD)
    dt = None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y%m%d"):
        try:
            dt = datetime.strptime(time_str, fmt)
            break
        except Exception:
            continue
    if dt is None:
        raise ValueError(f"Unrecognized Time format: {time_str}")
    date_token = dt.strftime("%Y%m%d")
    return cid, date_token


def cid_to_compound(cid: str, csv_path: Path, name_col: str = COMPOUND_COLUMN) -> Optional[str]:
    df = pd.read_csv(csv_path)
    # find column named 'cid' (any case)
    cid_col = None
    for c in df.columns:
        if str(c).lower() == 'cid':
            cid_col = c
            break
    if cid_col is None:
        return None
    row = df.loc[df[cid_col].astype(str) == str(cid)]
    if row.empty:
        return None
    return str(row.iloc[0][name_col])


def main():
    ap = argparse.ArgumentParser(description="Run pipeline for a single request if final output missing")
    ap.add_argument("--request", default="request.json", required=False, help="Path to request JSON with CID and Time")
    ap.add_argument("--final_dir", default="final_output", help="Directory where final files are stored")
    ap.add_argument("--csv", default="37_chemicals_test.csv", help="CSV mapping (must include name and cid columns; default: 37_chemicals_test.csv)")
    args = ap.parse_args()

    req_path = Path(args.request)
    if not req_path.exists():
        raise FileNotFoundError(f"Request file not found: {req_path}")

    cid, date_token = parse_request(req_path)
    # Check final file existence
    final_path = Path(args.final_dir) / f"{date_token}_{cid}.json"
    if final_path.exists():
        print(f"[SKIP] Final exists: {final_path}")
        return

    # Resolve compound name from CSV
    compound = cid_to_compound(cid, Path(args.csv))
    if not compound:
        raise ValueError(f"CID {cid} not found in {args.csv}")
    print(f"Request for CID={cid} ({compound}), date={date_token}")


    controller = PipelineController(str(Path(args.csv)), OUTPUT_BASE_DIR)

    with tqdm(total=1, desc=f"Processing {compound}") as pbar:
        controller.run_pipeline_for_compound(compound, pbar)

    # After run, check again
    if final_path.exists():
        print(f"[OK] Final created: {final_path}")
    else:
        print(f"[WARN] Final not found after run: {final_path}")


if __name__ == "__main__":
    main()
