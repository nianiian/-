import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

ELSEVIER_API_KEY = os.getenv("ELSEVIER_API_KEY", "6c0d2ee9da2850c9fb8fbc39dc97a4de")
ELSEVIER_INST_TOKEN = os.getenv("ELSEVIER_INST_TOKEN") # Add Institution Token if available

def search_sciencedirect_and_download():
    # ScienceDirect Search API Endpoint
    search_url = "https://api.elsevier.com/content/search/sciencedirect"
    
    # Headers for Search (PUT)
    headers_search = {
        "X-ELS-APIKey": ELSEVIER_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ResearchScript/1.0"
    }
    if ELSEVIER_INST_TOKEN:
        headers_search["X-ELS-Insttoken"] = ELSEVIER_INST_TOKEN

    # Search Body (PUT)
    query_body = {
        "qs": "butyl acrylate",
        "display": {
            "show": 10,
            "offset": 0,
            "sortBy": "date"
        }
    }

    print("Searching ScienceDirect for 'butyl acrylate' via API...")
    
    try:
        # Use PUT method as recommended by documentation
        res_search = requests.put(search_url, json=query_body, headers=headers_search, timeout=30)
        
        if res_search.status_code != 200:
            print(f"Search failed: {res_search.status_code}")
            print(res_search.text)
            return

        data = res_search.json()
        print(f"Found {data.get('resultsFound')} results.")
        
        results = data.get("results", [])
        if results:
            print(f"DEBUG: First item keys: {list(results[0].keys())}")

        print(f"Found {len(results)} results in this page.")

        for i, item in enumerate(results):
            # Extract PII or DOI - Keys are cleaner in this API version
            pii = item.get("pii")
            doi = item.get("doi")
            title = item.get("title")
            
            print(f"\nItem {i+1}: {title}")
            print(f"  PII: {pii}, DOI: {doi}")

            identifier = pii if pii else doi
            if not identifier:
                print("  -> No PII or DOI found, skipping.")
                continue
            
            # Construct Article Retrieval URL
            # Prefer PII for Elsevier retrieval if available
            # Add view=FULL to request full text explicitly
            if pii:
                article_url = f"https://api.elsevier.com/content/article/pii/{pii}?view=FULL"
            else:
                article_url = f"https://api.elsevier.com/content/article/doi/{doi}?view=FULL"

            # Strategy: Try PDF first. If file size is too small (likely 1-page preview), fallback to XML.
            
            # 1. Attempt PDF Download
            print(f"  -> Attempting download via Article API: {article_url}")
            
            headers_dl_pdf = {
                "X-ELS-APIKey": ELSEVIER_API_KEY,
                "Accept": "application/pdf",
                "User-Agent": "ResearchScript/1.0"
            }
            if ELSEVIER_INST_TOKEN:
                headers_dl_pdf["X-ELS-Insttoken"] = ELSEVIER_INST_TOKEN

            try:
                res_dl = requests.get(article_url, headers=headers_dl_pdf, timeout=30)
                
                content_type = res_dl.headers.get("Content-Type", "").lower()
                content_len = len(res_dl.content)
                
                # Heuristic: Full text PDFs are rarely under 400KB. 1-page previews are usually ~200-300KB.
                is_full_pdf = res_dl.status_code == 200 and "pdf" in content_type and content_len > 400 * 1024
                
                if is_full_pdf:
                    filename = f"sciencedirect_dl_{identifier.replace('/', '_')}.pdf"
                    with open(filename, "wb") as f:
                        f.write(res_dl.content)
                    print(f"    -> SUCCESS (PDF): Saved to {filename} ({content_len} bytes)")
                    
                else:
                     if res_dl.status_code == 200:
                         print(f"    -> Warning: PDF downloaded but seems incomplete (only {content_len} bytes). Likely 1-page preview.")
                     else:
                         print(f"    -> PDF Download Failed (Status: {res_dl.status_code})")
                     
                     # 2. Fallback to XML
                     print("    -> Fallback: Attempting XML download (TDM permission)...")
                     headers_dl_xml = headers_dl_pdf.copy()
                     headers_dl_xml["Accept"] = "text/xml"
                     
                     res_xml = requests.get(article_url, headers=headers_dl_xml, timeout=30)
                     
                     if res_xml.status_code == 200:
                         xml_len = len(res_xml.content)
                         filename = f"sciencedirect_dl_{identifier.replace('/', '_')}.xml"
                         with open(filename, "wb") as f:
                             f.write(res_xml.content)
                         print(f"    -> SUCCESS (XML Fallback): Saved to {filename} ({xml_len} bytes)")
                     else:
                         print(f"    -> Failed to download XML as well. Status: {res_xml.status_code}")

            except Exception as e:
                print(f"    -> Error downloading: {e}")

    except Exception as e:
        print(f"Error during search: {e}")

if __name__ == "__main__":
    search_sciencedirect_and_download()
