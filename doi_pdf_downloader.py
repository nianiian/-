"""
DOI PDF Downloader - 使用合法開放取用來源下載論文 PDF
支援來源：Unpaywall API、CrossRef、PubMed Central
"""

import requests
import os
import re
import time
import argparse
from pathlib import Path
from typing import Optional, Dict, Any
from urllib.parse import urlparse

# 設定
DEFAULT_EMAIL = "your_email@example.com"  # Unpaywall API 需要 email
DEFAULT_OUTPUT_DIR = "downloads"
REQUEST_TIMEOUT = 30
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2


class DOIPDFDownloader:
    """使用 DOI 從合法開放取用來源下載 PDF"""
    
    def __init__(self, email: str = DEFAULT_EMAIL, output_dir: str = DEFAULT_OUTPUT_DIR):
        self.email = email
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "DOI-PDF-Downloader/1.0 (Academic Research; mailto:{})".format(email)
        })
    
    def clean_doi(self, doi: str) -> str:
        """清理 DOI 格式"""
        # 移除 URL 前綴
        doi = re.sub(r'^https?://(dx\.)?doi\.org/', '', doi)
        doi = re.sub(r'^doi:', '', doi, flags=re.IGNORECASE)
        return doi.strip()
    
    def sanitize_filename(self, filename: str) -> str:
        """將字串轉換為安全的檔案名稱"""
        # 移除或替換不安全的字元
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = re.sub(r'\s+', '_', filename)
        return filename[:200]  # 限制長度
    
    def get_unpaywall_info(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        使用 Unpaywall API 查詢開放取用資訊
        Unpaywall 是合法的開放取用搜尋服務
        """
        url = f"https://api.unpaywall.org/v2/{doi}"
        params = {"email": self.email}
        
        try:
            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                print(f"  Unpaywall: DOI not found")
            else:
                print(f"  Unpaywall: HTTP {response.status_code}")
        except Exception as e:
            print(f"  Unpaywall error: {e}")
        
        return None
    
    def get_crossref_info(self, doi: str) -> Optional[Dict[str, Any]]:
        """
        使用 CrossRef API 取得論文元資料
        """
        url = f"https://api.crossref.org/works/{doi}"
        
        try:
            response = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                return response.json().get("message", {})
        except Exception as e:
            print(f"  CrossRef error: {e}")
        
        return None
    
    def get_pmc_pdf_url(self, doi: str) -> Optional[str]:
        """
        檢查 PubMed Central 是否有免費 PDF
        """
        # 先用 DOI 查詢 PMC ID
        url = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        params = {
            "ids": doi,
            "format": "json",
            "tool": "doi_pdf_downloader",
            "email": self.email
        }
        
        try:
            response = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code == 200:
                data = response.json()
                records = data.get("records", [])
                if records and "pmcid" in records[0]:
                    pmcid = records[0]["pmcid"]
                    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
        except Exception as e:
            print(f"  PMC lookup error: {e}")
        
        return None
    
    def find_pdf_url(self, doi: str) -> Optional[str]:
        """
        嘗試多個來源尋找 PDF URL
        優先順序：Unpaywall OA > PMC > Unpaywall (any)
        """
        print(f"Searching for open access PDF...")
        
        # 1. 嘗試 Unpaywall
        unpaywall_data = self.get_unpaywall_info(doi)
        if unpaywall_data:
            # 優先使用 best_oa_location
            best_oa = unpaywall_data.get("best_oa_location")
            if best_oa and best_oa.get("url_for_pdf"):
                print(f"  Found via Unpaywall (OA): {best_oa.get('host_type', 'unknown')}")
                return best_oa["url_for_pdf"]
            
            # 檢查其他 OA locations
            oa_locations = unpaywall_data.get("oa_locations", [])
            for loc in oa_locations:
                if loc.get("url_for_pdf"):
                    print(f"  Found via Unpaywall: {loc.get('host_type', 'unknown')}")
                    return loc["url_for_pdf"]
        
        # 2. 嘗試 PMC
        pmc_url = self.get_pmc_pdf_url(doi)
        if pmc_url:
            print(f"  Found via PubMed Central")
            return pmc_url
        
        # 3. 檢查 CrossRef 是否有直接連結
        crossref_data = self.get_crossref_info(doi)
        if crossref_data:
            links = crossref_data.get("link", [])
            for link in links:
                if link.get("content-type") == "application/pdf":
                    print(f"  Found via CrossRef")
                    return link.get("URL")
        
        return None
    
    def download_pdf(self, url: str, output_path: Path) -> bool:
        """
        下載 PDF 檔案
        """
        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = self.session.get(
                    url, 
                    timeout=REQUEST_TIMEOUT,
                    stream=True,
                    allow_redirects=True
                )
                
                if response.status_code == 200:
                    content_type = response.headers.get("Content-Type", "")
                    
                    # 確認是 PDF
                    if "pdf" in content_type.lower() or url.endswith(".pdf"):
                        with open(output_path, "wb") as f:
                            for chunk in response.iter_content(chunk_size=8192):
                                f.write(chunk)
                        
                        # 驗證檔案
                        if output_path.stat().st_size > 1000:  # 至少 1KB
                            return True
                        else:
                            output_path.unlink()
                            print(f"  Downloaded file too small, retrying...")
                    else:
                        print(f"  Not a PDF (Content-Type: {content_type})")
                        return False
                else:
                    print(f"  HTTP {response.status_code}, attempt {attempt + 1}/{RETRY_ATTEMPTS}")
                    
            except Exception as e:
                print(f"  Download error: {e}, attempt {attempt + 1}/{RETRY_ATTEMPTS}")
            
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
        
        return False
    
    def download_by_doi(self, doi: str, filename: Optional[str] = None) -> Optional[Path]:
        """
        主要方法：使用 DOI 下載 PDF
        
        Args:
            doi: 論文的 DOI
            filename: 自訂檔案名稱（不含副檔名），若無則使用 DOI
            
        Returns:
            成功時返回檔案路徑，失敗時返回 None
        """
        doi = self.clean_doi(doi)
        print(f"\n{'='*60}")
        print(f"DOI: {doi}")
        print(f"{'='*60}")
        
        # 尋找 PDF URL
        pdf_url = self.find_pdf_url(doi)
        
        if not pdf_url:
            print(f"No open access PDF found for this DOI")
            return None
        
        print(f"PDF URL: {pdf_url}")
        
        # 決定檔案名稱
        if filename:
            safe_filename = self.sanitize_filename(filename)
        else:
            safe_filename = self.sanitize_filename(doi.replace("/", "_"))
        
        output_path = self.output_dir / f"{safe_filename}.pdf"
        
        # 檢查是否已存在
        if output_path.exists():
            print(f"File already exists: {output_path}")
            return output_path
        
        # 下載
        print(f"Downloading to: {output_path}")
        if self.download_pdf(pdf_url, output_path):
            print(f"Successfully downloaded: {output_path}")
            return output_path
        else:
            print(f"Failed to download PDF")
            return None
    
    def download_batch(self, dois: list, delay: float = 1.0) -> Dict[str, Optional[Path]]:
        """
        批次下載多個 DOI
        
        Args:
            dois: DOI 列表
            delay: 每次下載之間的延遲（秒）
            
        Returns:
            DOI 到檔案路徑的對應字典
        """
        results = {}
        
        for i, doi in enumerate(dois):
            print(f"\n[{i+1}/{len(dois)}] Processing...")
            results[doi] = self.download_by_doi(doi)
            
            if i < len(dois) - 1:
                time.sleep(delay)
        
        # 統計結果
        success = sum(1 for v in results.values() if v is not None)
        print(f"\n{'='*60}")
        print(f"Batch download complete: {success}/{len(dois)} successful")
        print(f"{'='*60}")
        
        return results


def main():
    parser = argparse.ArgumentParser(
        description="Download PDF papers using DOI from open access sources"
    )
    parser.add_argument(
        "doi",
        nargs="?",
        help="DOI of the paper to download"
    )
    parser.add_argument(
        "--file", "-f",
        help="File containing DOIs (one per line)"
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )
    parser.add_argument(
        "--email", "-e",
        default=DEFAULT_EMAIL,
        help="Email for API requests (required by Unpaywall)"
    )
    
    args = parser.parse_args()
    
    if not args.doi and not args.file:
        parser.print_help()
        print("\nExample usage:")
        print("  python doi_pdf_downloader.py 10.1038/nature12373")
        print("  python doi_pdf_downloader.py --file dois.txt --output-dir papers/")
        return
    
    downloader = DOIPDFDownloader(email=args.email, output_dir=args.output_dir)
    
    if args.file:
        # 從檔案讀取 DOI 列表
        with open(args.file, "r", encoding="utf-8") as f:
            dois = [line.strip() for line in f if line.strip()]
        downloader.download_batch(dois)
    else:
        # 單一 DOI
        downloader.download_by_doi(args.doi)


if __name__ == "__main__":
    main()
