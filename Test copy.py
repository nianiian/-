import requests
import json
import os
import time
from pathlib import Path
from config_loader import get_config

# Load configuration
CONFIG = get_config()
S2_API_KEY = CONFIG.get("semantic_scholar_api_key", "")

def test_semantic_scholar_pdf_download():
    print("Testing Semantic Scholar PDF Download...")
    
    # query = "1,2-dichloroethane safety alternative"
    query = "butyl acrylate toxicity"
    
    # Endpoint
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    
    # Request specific fields including openAccessPdf
    params = {
        "query": query,
        "limit": 10,
        "fields": "title,year,openAccessPdf" 
    }
    
    headers = {
        "User-Agent": "ResearchScript/1.0"
    }
    if S2_API_KEY:
        # Semantic Scholar API uses 'x-api-key' for public API or 'Authorization: Bearer' for partners/tokens
        # step01.py uses Bearer, adhering to that.
        headers["Authorization"] = f"Bearer {S2_API_KEY}"
    
    try:
        print(f"Searching for: '{query}'...")
        res = requests.get(url, params=params, headers=headers, timeout=30)
        
        if res.status_code != 200:
            print(f"API Request Failed: {res.status_code}")
            print(res.text)
            return
            
        data = res.json()
        total = data.get("total", 0)
        papers = data.get("data", [])
        
        print(f"Total found: {total}. Checking top {len(papers)} for PDFs...\n")
        
        pdf_count = 0
        for i, paper in enumerate(papers):
            title = paper.get("title", "No Title")
            oa_info = paper.get("openAccessPdf")
            
            print(f"[{i+1}] {title}")
            
            # Semantic Scholar can return 'openAccessPdf': None, or {'url': ...}
            # Sometimes url might be empty or invalid? debugging this now.
            
            if oa_info and isinstance(oa_info, dict) and oa_info.get("url"):
                pdf_url = oa_info["url"]
                print(f"    -> Found Open Access PDF: {pdf_url}")
                
                # Metadata check: sometimes S2 returns the DOI landing page as the PDF url if it thinks it's OA?
                # or maybe the dict has other keys.
                
                # Attempt Download
                try:
                    print("       Downloading...")
                    # Mimic a real browser to avoid 403 Forbidden on sites like MDPI
                    browser_headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Referer": "https://www.google.com/"
                    }
                    pdf_res = requests.get(pdf_url, headers=browser_headers, timeout=30, allow_redirects=True)
                    
                    content_type = pdf_res.headers.get("Content-Type", "").lower()
                    print(f"       Debug: Status {pdf_res.status_code}, Type {content_type}, URL {pdf_res.url}")

                    if pdf_res.status_code == 200 and "application/pdf" in content_type:
                        filename = f"s2_download_{i+1}.pdf"
                        with open(filename, "wb") as f:
                            f.write(pdf_res.content)
                        print(f"       SUCCESS: Saved to {filename} ({len(pdf_res.content)} bytes)")
                        pdf_count += 1
                    else:
                        print(f"       Failed. Status: {pdf_res.status_code}, Type: {content_type}")
                        
                except Exception as e:
                    print(f"       Error downloading: {e}")
            else:
                print("    -> No direct PDF link available (Metadata only).")
            print("-" * 50)
            print("Waiting for 5 seconds...")
            time.sleep(5)
            
        print(f"\nSummary: Downloaded {pdf_count} PDFs out of {len(papers)} results.")

    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_semantic_scholar_pdf_download()
