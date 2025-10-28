import argparse
import json
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from tqdm import tqdm

# Configuration file path
CONFIG_FILE = "api_config.json"

def load_config():
    """Load API keys and settings from config file."""
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        print(f"[ERROR] Configuration file '{CONFIG_FILE}' not found!")
        print("Please create the config file with your API keys.")
        exit(1)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse config file: {e}")
        exit(1)

# Load configuration
CONFIG = load_config()
ELSEVIER_API_KEY = CONFIG.get("elsevier_api_key", "")

if not ELSEVIER_API_KEY:
    print("[ERROR] Elsevier API key not found in config file!")
    exit(1)

def fetch_abstract_from_elsevier(doi, session: requests.Session, api_key=ELSEVIER_API_KEY):
    """Fetch abstract from Elsevier API using provided session."""
    url = f"https://api.elsevier.com/content/article/doi/{doi}"
    headers = {
        "Accept": "application/json",
        "X-ELS-APIKey": api_key
    }
    try:
        response = session.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        abstract = data.get("full-text-retrieval-response", {}).get("coredata", {}).get("dc:description", None)
        return abstract.strip() if abstract else None
    except Exception:
        return None

def fetch_abstract_from_crossref(doi, session: requests.Session):
    """Fetch abstract from Crossref API using provided session."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        abstract = data["message"].get("abstract", None)
        if abstract:
            abstract = re.sub('<[^<]+?>', '', abstract)
            return abstract.strip()
        return None
    except Exception:
        return None

def load_records(input_file):
    """Load records from JSON file."""
    with open(input_file, "r", encoding="utf-8") as f:
        return json.load(f)

def save_records(records, output_file):
    """Save records to JSON file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

def _build_session() -> requests.Session:
    """Create a requests session with connection pooling and retries."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def fill_missing_abstracts(records, workers: int = 8):
    """Fill missing abstracts using DOI lookups with parallel requests."""
    # Cache per DOI to avoid duplicate requests
    cache = {}

    # Build work list: indices of records needing abstract and their DOIs
    work = []
    for idx, entry in enumerate(records):
        abstract = (entry.get("abstract") or "").strip()
        doi = (entry.get("externalIds", {}).get("DOI") or "").strip()
        if abstract == "" and doi:
            work.append((idx, doi))

    if not work:
        return 0

    session = _build_session()

    def fetch_for_doi(doi: str):
        if doi in cache:
            return cache[doi]
        # Elsevier first, fallback to Crossref
        abs_txt = fetch_abstract_from_elsevier(doi, session=session)
        if not abs_txt:
            abs_txt = fetch_abstract_from_crossref(doi, session=session)
        cache[doi] = abs_txt
        return abs_txt

    filled_count = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(fetch_for_doi, doi): (idx, doi) for idx, doi in work}
        for fut in tqdm(as_completed(future_map), total=len(future_map), desc="Filling missing abstracts (parallel)"):
            idx, doi = future_map[fut]
            try:
                new_abs = fut.result()
            except Exception:
                new_abs = None
            if new_abs:
                records[idx]["abstract"] = new_abs
                filled_count += 1

    return filled_count

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Fetch missing abstracts")
    parser.add_argument("--input_file", required=True, help="Input JSON file")
    parser.add_argument("--output_file", required=True, help="Output JSON file")
    parser.add_argument("--workers", type=int, default=8, help="Parallel worker threads for DOI lookups")
    return parser.parse_args()

def main():
    """Main execution function."""
    args = parse_args()
    
    print(f"Loading records from: {args.input_file}")
    print(f"Using Elsevier API key from config file")
    
    records = load_records(args.input_file)
    
    filled_count = fill_missing_abstracts(records, workers=args.workers)
    
    save_records(records, args.output_file)
    
    total_count = len(records)
    has_abstract_count = sum(1 for entry in records if (entry.get("abstract") or "").strip() != "")
    
    print(f"Total records: {total_count}")
    print(f"Records with abstract: {has_abstract_count}")
    print(f"Abstracts filled: {filled_count}")
    print(f"Results saved to: {args.output_file}")
    if total_count > 0:
        pct = has_abstract_count / total_count * 100
        print(f"Coverage: {pct:.1f}% abstracts present after fill")

if __name__ == "__main__":
    main()
