import argparse
import json
import re
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from tqdm import tqdm

# Configuration file path
from config_loader import get_config

# Load configuration (.env first, fallback to api_config.json)
CONFIG = get_config()
ELSEVIER_API_KEY = CONFIG.get("elsevier_api_key", "")
S2_API_KEY = CONFIG.get("semantic_scholar_api_key", "")

if not ELSEVIER_API_KEY:
    print("[WARNING] Elsevier API key not found - will use other sources")

def fetch_abstract_from_semantic_scholar(doi: str, client: httpx.Client) -> str | None:
    """Fetch abstract from Semantic Scholar API (fastest, best coverage)."""
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": "abstract"}
    headers = {"User-Agent": "ResearchPipeline/1.0"}
    if S2_API_KEY:
        headers["x-api-key"] = S2_API_KEY
    try:
        response = client.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            abstract = data.get("abstract")
            if abstract:
                return abstract.strip()
    except Exception:
        pass
    return None


def fetch_abstract_from_elsevier(doi: str, client: httpx.Client, api_key: str = ELSEVIER_API_KEY) -> str | None:
    """Fetch abstract from Elsevier API using httpx client."""
    if not api_key:
        return None
    url = f"https://api.elsevier.com/content/article/doi/{doi}"
    headers = {
        "Accept": "application/json",
        "X-ELS-APIKey": api_key
    }
    try:
        response = client.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        abstract = data.get("full-text-retrieval-response", {}).get("coredata", {}).get("dc:description", None)
        return abstract.strip() if abstract else None
    except Exception:
        return None


def fetch_abstract_from_crossref(doi: str, client: httpx.Client) -> str | None:
    """Fetch abstract from Crossref API using httpx client."""
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = client.get(url, timeout=10)
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

def _sort_key(rec: dict):
    try:
        doi = ""
        ext = rec.get("externalIds", {}) if isinstance(rec, dict) else {}
        if isinstance(ext, dict):
            doi = str(ext.get("DOI", "")).strip().lower()
        title = str(rec.get("title", "")).strip().lower() if isinstance(rec, dict) else ""
        return (1 if not doi else 0, doi or title, title)
    except Exception:
        return (1, "", "")

def _build_client() -> httpx.Client:
    """Create an httpx client with connection pooling."""
    # httpx handles retries internally with transport
    transport = httpx.HTTPTransport(retries=3)
    return httpx.Client(transport=transport, follow_redirects=True)

def fill_missing_abstracts(records, persistent_cache: dict, workers: int = 8):
    """Fill missing abstracts using DOI lookups with parallel requests."""
    # Build work list: indices of records needing abstract and their DOIs OR titles
    work = []
    cache_hits = 0
    for idx, entry in enumerate(records):
        abstract = (entry.get("abstract") or "").strip()
        doi = (entry.get("externalIds", {}).get("DOI") or "").strip()
        title = (entry.get("title") or entry.get("Article title") or "").strip()
        
        if not abstract:
            # Check persistent cache first
            if doi and doi in persistent_cache:
                records[idx]["abstract"] = persistent_cache[doi]
                cache_hits += 1
            elif title and title in persistent_cache:
                records[idx]["abstract"] = persistent_cache[title]
                cache_hits += 1
            elif doi:
                # Still missing, need to fetch
                work.append((idx, doi, title))

    if not work:
        print(f"All records have abstracts. (Cache hits: {cache_hits})")
        return cache_hits

    print(f"Need to fetch abstracts for {len(work)} records (using {workers} workers, Cache hits: {cache_hits})...")
    client = _build_client()

    def fetch_for_doi(doi: str, title: str):
        if doi in persistent_cache:
            return persistent_cache[doi]
        # Priority: Semantic Scholar (fastest) -> Elsevier -> Crossref
        abs_txt = fetch_abstract_from_semantic_scholar(doi, client=client)
        if not abs_txt:
            abs_txt = fetch_abstract_from_elsevier(doi, client=client)
        if not abs_txt:
            abs_txt = fetch_abstract_from_crossref(doi, client=client)
            
        if abs_txt:
            persistent_cache[doi] = abs_txt
            # Do NOT duplicate with title key when DOI is already used
        return abs_txt

    filled_count = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {ex.submit(fetch_for_doi, doi, title): (idx, doi, title) for idx, doi, title in work}
        # Update progress every 1% (miniters = total / 100)
        update_interval = max(1, len(future_map) // 100)
        pbar = tqdm(as_completed(future_map), total=len(future_map), 
                    desc="Filling missing abstracts", miniters=update_interval,
                    dynamic_ncols=True, file=sys.stdout)
        for fut in pbar:
            idx, doi, title = future_map[fut]
            try:
                new_abs = fut.result()
            except Exception:
                new_abs = None
            if new_abs:
                records[idx]["abstract"] = new_abs
                filled_count += 1
            pbar.set_postfix(fetched=filled_count, refresh=False)

    return cache_hits + filled_count

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
    
    # --- Abstract Caching System ---
    output_path = Path(args.output_file)
    output_dir = output_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_file = output_dir / "abstract_cache.json"
    
    persistent_cache = {}
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                persistent_cache.update(json.load(f))
        except Exception:
            pass
            
    if output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                old_records = json.load(f)
                for rec in old_records:
                    doi = str(rec.get("externalIds", {}).get("DOI", "")).strip()
                    title = str(rec.get("title", "") or rec.get("Article title", "")).strip()
                    abstract = str(rec.get("abstract", "")).strip()
                    if abstract:
                        if doi:
                            persistent_cache[doi] = abstract
                        elif title:
                            persistent_cache[title] = abstract
        except Exception:
            pass
            
    print(f"Loaded {len(persistent_cache)} abstracts from local cache.")

    filled_count = fill_missing_abstracts(records, persistent_cache, workers=args.workers)
    
    # Save the updated cache back
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(persistent_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to save abstract cache: {e}")
        
    # --- End Caching System ---
    
    # Save in the original order
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
