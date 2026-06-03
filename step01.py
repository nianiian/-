import argparse
import json
import os
import random
import requests
import time
from tqdm import tqdm
import pandas as pd
from pathlib import Path

# Configuration file path
from config_loader import get_config

# Load configuration (.env first, fallback to api_config.json)
CONFIG = get_config()
API_KEY = CONFIG.get("semantic_scholar_api_key", "")
TARGET_PAPERS = CONFIG.get("default_settings", {}).get("max_papers", 10000)
BATCH_SIZE = CONFIG.get("default_settings", {}).get("batch_size", 1000)
MAX_RETRIES = CONFIG.get("default_settings", {}).get("max_retries", 20)

if not API_KEY:
    print("[ERROR] Semantic Scholar API key not found in config file!")
    exit(1)

def fetch_semantic_scholar_with_token(search_params, max_retries=MAX_RETRIES):
    """Make API call with token-based pagination."""
    for attempt in range(max_retries):
        try:
            delay = 1 + random.random() * 2
            if attempt > 0:
                print(f"Attempt {attempt+1}/{max_retries}, waiting {delay:.2f} seconds...", flush=True)
            time.sleep(delay)
            
            response = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search/bulk", 
                params=search_params,
                headers={
                    "Authorization": f"Bearer {API_KEY}", 
                    "User-Agent": "Research Script (academic use)"
                }
            )
            
            # Log status code for non-200 responses
            if response.status_code != 200:
                print(f"\n[API Error] Status {response.status_code}: {response.reason}", flush=True)
                if response.status_code == 429:
                    # Rate limited - wait longer
                    wait_time = 60 + random.random() * 60  # 60-120 seconds
                    print(f"[Rate Limited] Waiting {wait_time:.1f}s before retry...", flush=True)
                    time.sleep(wait_time)
                    continue
                elif response.status_code >= 500:
                    # Server error - exponential backoff
                    wait_time = min(30 * (2 ** attempt), 300)  # 30s, 60s, 120s, 240s, max 300s
                    print(f"[Server Error] Waiting {wait_time}s before retry (attempt {attempt+1}/{max_retries})...", flush=True)
                    time.sleep(wait_time)
                    continue
            
            response.raise_for_status()
            json_response = response.json()
            
            data = json_response.get("data", [])
            total = json_response.get("total", 0)
            next_token = json_response.get("token", None)
            
            return data, total, next_token
            
        except requests.exceptions.HTTPError as e:
            print(f"\n[HTTP Error] {e}", flush=True)
            if attempt == max_retries - 1:
                print(f"[FAILED] Exhausted all {max_retries} retries due to HTTP error", flush=True)
                return [], 0, None
        except requests.exceptions.ConnectionError as e:
            print(f"\n[Connection Error] {e}", flush=True)
            if attempt == max_retries - 1:
                print(f"[FAILED] Exhausted all {max_retries} retries due to connection error", flush=True)
                return [], 0, None
        except requests.exceptions.Timeout as e:
            print(f"\n[Timeout Error] {e}", flush=True)
            if attempt == max_retries - 1:
                print(f"[FAILED] Exhausted all {max_retries} retries due to timeout", flush=True)
                return [], 0, None
        except Exception as e:
            print(f"\n[Unexpected Error] {type(e).__name__}: {e}", flush=True)
            if attempt == max_retries - 1:
                print(f"[FAILED] Exhausted all {max_retries} retries", flush=True)
                return [], 0, None
    
    print(f"\n[FAILED] Loop ended without success after {max_retries} attempts", flush=True)
    return [], 0, None

def fetch_all_papers_with_token(keyword, max_results=TARGET_PAPERS, batch_size=BATCH_SIZE, year_range: int | None = None):
    """Fetch all papers using token-based pagination.
    
    Args:
        keyword: Search keyword
        max_results: Maximum number of papers to fetch
        batch_size: Number of papers per API call
        year_range: If set, only fetch papers from the last N years (e.g., 20 for 2006-2026)
    """
    all_papers = []
    current_token = None
    total_available = 0
    batch_count = 0
    
    # Calculate year filter
    year_filter = None
    if year_range:
        from datetime import datetime
        current_year = datetime.now().year
        start_year = current_year - year_range
        year_filter = f"{start_year}-{current_year}"
        print(f"Year filter: {year_filter} (last {year_range} years)")
    
    pbar = tqdm(total=max_results, desc=f"Fetching papers for {keyword}")
    
    while len(all_papers) < max_results:
        batch_count += 1
        remaining = max_results - len(all_papers)
        current_batch_size = min(batch_size, remaining)
        
        search_params = {
            "query": keyword,
            "fields": "title,abstract,authors,year,url,externalIds,venue,publicationTypes",
            "limit": current_batch_size
        }
        
        # Add year filter if specified
        if year_filter:
            search_params["year"] = year_filter
        
        if current_token:
            search_params["token"] = current_token
        
        current_batch, total, next_token = fetch_semantic_scholar_with_token(search_params)
        
        if not current_batch:
            break
        
        if total and total > total_available:
            total_available = total
            if total < max_results:
                pbar.total = min(total, max_results)
                pbar.refresh()
        
        all_papers.extend(current_batch)
        pbar.update(len(current_batch))
        
        if not next_token:
            break
            
        current_token = next_token
        
        delay = 2 + random.random() * 3
        time.sleep(delay)
    
    pbar.close()
    return all_papers[:max_results], total_available

def save_results(papers, output_file):
    """Save results to specified file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)
    
    print(f"Results saved to: {output_path}")

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

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Fetch papers for a specific compound")
    parser.add_argument("--keyword", required=True, help="Compound name to search")
    parser.add_argument("--output_dir", required=True, help="Output directory")
    parser.add_argument("--output_file", required=True, help="Output file name")
    parser.add_argument("--max_results", type=int, default=TARGET_PAPERS, help="Maximum number of results")
    parser.add_argument("--year_range", type=int, default=20, help="Only fetch papers from the last N years (default: 20)")
    return parser.parse_args()


def main():
    """Main execution function."""
    args = parse_args()
    
    print(f"Fetching papers for compound: {args.keyword}")
    print(f"Target papers: {args.max_results}")
    print(f"Year range: last {args.year_range} years")
    print(f"Using Semantic Scholar API key from config file")
    
    # Check if step00_queries.json exists — UNION merge: keep compound name + append AI queries
    queries_file = Path(args.output_dir) / "step00_queries.json"
    queries = [args.keyword]
    if queries_file.exists():
        try:
            with open(queries_file, 'r', encoding='utf-8') as f:
                ai_queries = json.load(f)
            ai_list: list[str] = []
            if isinstance(ai_queries, list):
                ai_list = ai_queries
            elif isinstance(ai_queries, dict) and "queries" in ai_queries:
                ai_list = ai_queries["queries"]
            # Union: compound name first, then non-duplicate AI queries
            seen: set[str] = {args.keyword}
            for q in ai_list:
                if q not in seen:
                    queries.append(q)
                    seen.add(q)
            print(f"Unified query pool: compound name + {len(queries) - 1} AI queries = {len(queries)} total")
        except Exception as e:
            print(f"Failed to read {queries_file}: {e}. Fallback to default keyword.")

    all_collected_papers = {}  # Use dict to deduplicate by paperId
    total_available_overall = 0

    for query in queries:
        print(f"\n--- Running query: {query} ---")
        papers_cur, total_cur = fetch_all_papers_with_token(
            query, 
            max_results=args.max_results,
            year_range=args.year_range
        )
        total_available_overall = max(total_available_overall, total_cur)
        
        # Deduplicate and track query keyword
        for p in papers_cur:
            pid = p.get("paperId")
            if pid and pid not in all_collected_papers:
                p["query_keyword"] = query
                all_collected_papers[pid] = p
                
    papers = list(all_collected_papers.values())
    
    if papers:
        print(f"\nCollected {len(papers)} unique papers across all queries for {args.keyword}")
        output_file = Path(args.output_dir) / args.output_file
        save_results(papers, output_file)
        print(f"Successfully fetched {len(papers)} papers for {args.keyword}")
    else:
        print(f"\nNo papers found for {args.keyword}")

if __name__ == "__main__":
    main()
