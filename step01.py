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
                print(f"Attempt {attempt+1}/{max_retries}, waiting {delay:.2f} seconds...")
            time.sleep(delay)
            
            response = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search/bulk", 
                params=search_params,
                headers={
                    "Authorization": f"Bearer {API_KEY}", 
                    "User-Agent": "Research Script (academic use)"
                }
            )
            
            response.raise_for_status()
            json_response = response.json()
            
            data = json_response.get("data", [])
            total = json_response.get("total", 0)
            next_token = json_response.get("token", None)
            
            return data, total, next_token
            
        except Exception as e:
            if attempt == max_retries - 1:
                return [], 0, None
    
    return [], 0, None

def fetch_all_papers_with_token(keyword, max_results=TARGET_PAPERS, batch_size=BATCH_SIZE):
    """Fetch all papers using token-based pagination."""
    all_papers = []
    current_token = None
    total_available = 0
    batch_count = 0
    
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

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Fetch papers for a specific compound")
    parser.add_argument("--keyword", required=True, help="Compound name to search")
    parser.add_argument("--output_dir", required=True, help="Output directory")
    parser.add_argument("--output_file", required=True, help="Output file name")
    parser.add_argument("--max_results", type=int, default=TARGET_PAPERS, help="Maximum number of results")
    return parser.parse_args()

def main():
    """Main execution function."""
    args = parse_args()
    
    print(f"Fetching papers for compound: {args.keyword}")
    print(f"Target papers: {args.max_results}")
    print(f"Using Semantic Scholar API key from config file")
    
    papers, total_available = fetch_all_papers_with_token(
        args.keyword, 
        max_results=args.max_results
    )
    
    if papers:
        output_file = Path(args.output_dir) / args.output_file
        save_results(papers, output_file)
        print(f"Successfully fetched {len(papers)} papers for {args.keyword}")
    else:
        print(f"No papers found for {args.keyword}")

if __name__ == "__main__":
    main()
