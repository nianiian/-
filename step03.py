import argparse
import json
import logging
import os
import sys
import time
import requests  # Added for downloading
import re        # Added for filename sanitization
import shutil    # Added for moving downloaded files
import importlib
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

import pandas as pd
from tqdm import tqdm

# Selenium modules are loaded lazily to keep this script runnable without selenium installed.
webdriver = None
Options = None
By = None


def _ensure_selenium_modules() -> bool:
    """Load selenium modules on demand; return True when available."""
    global webdriver, Options, By
    if webdriver is not None and Options is not None and By is not None:
        return True
    try:
        selenium_webdriver = importlib.import_module("selenium.webdriver")
        chrome_options_mod = importlib.import_module("selenium.webdriver.chrome.options")
        by_mod = importlib.import_module("selenium.webdriver.common.by")
        webdriver = selenium_webdriver
        Options = chrome_options_mod.Options
        By = by_mod.By
        return True
    except Exception:
        webdriver = None
        Options = None
        By = None
        return False

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# Gemini support
try:
    import google.genai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    GEMINI_AVAILABLE = False

# Configuration file path
from config_loader import get_config

# Load configuration (.env first, fallback to api_config.json)
CONFIG = get_config()

# Validation for API keys needed for downloading
ELSEVIER_API_KEY = CONFIG.get("elsevier_api_key", os.getenv("ELSEVIER_API_KEY", ""))
ELSEVIER_INST_TOKEN = CONFIG.get("elsevier_inst_token", os.getenv("ELSEVIER_INST_TOKEN", ""))
S2_API_KEY = CONFIG.get("semantic_scholar_api_key", "")
SPRINGER_API_KEY = CONFIG.get("springer_api_key", os.getenv("SPRINGER_API_KEY", ""))
OPENALEX_EMAIL = CONFIG.get("openalex_email", os.getenv("OPENALEX_EMAIL", ""))
UNPAYWALL_EMAIL = CONFIG.get(
    "unpaywall_email",
    os.getenv("UNPAYWALL_EMAIL", os.getenv("CONTACT_EMAIL", "")),
)
REST_API_KEY = CONFIG.get("rest_api_key", os.getenv("REST_API_KEY", ""))

def check_semantic_scholar_pdf(doi: str) -> Optional[str]:
    """
    Checks Semantic Scholar for a direct PDF link.
    Returns the URL string if found, else None.
    """
    try:
        paper_id = f"DOI:{doi}"
        url = f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
        params = {"fields": "openAccessPdf"}
        headers = {"User-Agent": "ResearchScript/1.0"}
        if S2_API_KEY:
            headers["Authorization"] = f"Bearer {S2_API_KEY}"

        res = requests.get(url, params=params, headers=headers, timeout=10)
        if res.status_code == 200:
            data = res.json()
            pdf_info = data.get("openAccessPdf")
            if pdf_info and pdf_info.get("url"):
                return pdf_info.get("url")
    except Exception:
        pass
    return None


def download_springer_jats(doi: str, output_path: Path) -> bool:
    """
    Download full-text JATS XML from Springer Nature Open Access API.
    Only works for Springer/Nature open access articles.
    Returns True if successful.
    """
    if not SPRINGER_API_KEY:
        return False

    try:
        # Springer JATS endpoint expects DOI without prefix
        jats_url = f"https://api.springernature.com/openaccess/jats?q=doi:{doi}&api_key={SPRINGER_API_KEY}"
        headers = {"Accept": "application/xml", "User-Agent": "ResearchScript/1.0"}

        res = requests.get(jats_url, headers=headers, timeout=30)
        if res.status_code == 200 and len(res.content) > 1000:
            # Check if we got actual JATS content (not an error page)
            content_text = res.content[:500].decode("utf-8", errors="ignore")
            if "<article" in content_text or "<response" in content_text:
                # Save as XML with same base name
                xml_path = output_path.with_suffix(".xml")
                with open(xml_path, "wb") as f:
                    f.write(res.content)
                return True
    except Exception:
        pass

    return False


def check_openalex_pdf(doi: str) -> Optional[str]:
    """
    Query OpenAlex by DOI and return a best-effort OA full-text URL.
    Returns URL string when found, else None.
    """
    if not doi:
        return None

    # Normalize DOI input (strip possible https://doi.org/ prefix)
    normalized_doi = re.sub(r"^https?://(dx\\.)?doi\\.org/", "", doi.strip(), flags=re.IGNORECASE)
    if not normalized_doi:
        return None

    params = {
        "filter": f"doi:https://doi.org/{normalized_doi}",
        "per-page": 1,
    }
    if OPENALEX_EMAIL:
        params["mailto"] = OPENALEX_EMAIL

    try:
        res = requests.get(
            "https://api.openalex.org/works",
            params=params,
            headers={"User-Agent": "ResearchScript/1.0"},
            timeout=15,
        )
        if res.status_code != 200:
            logging.getLogger("safer_alt_en").debug(
                "OpenAlex lookup failed for DOI %s with status %s",
                normalized_doi,
                res.status_code,
            )
            return None

        data = res.json()
        results = data.get("results", []) if isinstance(data, dict) else []
        if not results:
            return None

        work = results[0]
        open_access = work.get("open_access", {}) if isinstance(work, dict) else {}
        best_location = work.get("best_oa_location", {}) if isinstance(work, dict) else {}
        primary_location = work.get("primary_location", {}) if isinstance(work, dict) else {}

        for candidate in [
            open_access.get("oa_url"),
            best_location.get("pdf_url"),
            primary_location.get("pdf_url"),
            best_location.get("landing_page_url"),
            primary_location.get("landing_page_url"),
        ]:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        locations = work.get("locations", []) if isinstance(work, dict) else []
        if isinstance(locations, list):
            for location in locations:
                if not isinstance(location, dict):
                    continue
                pdf_url = location.get("pdf_url")
                if isinstance(pdf_url, str) and pdf_url.strip():
                    return pdf_url.strip()
        return None
    except Exception as exc:
        logging.getLogger("safer_alt_en").debug(
            "OpenAlex lookup exception for DOI %s: %s",
            normalized_doi,
            exc,
        )
        return None


def _extract_unpaywall_email(raw_value: str) -> str:
    """Extract contact email from REST_API_KEY value (email or URL form)."""
    raw = (raw_value or "").strip()
    if not raw:
        return ""

    if "@" in raw and "http" not in raw.lower():
        return raw

    candidate = raw if raw.lower().startswith("http") else f"https://{raw}"
    try:
        query = parse_qs(urlparse(candidate).query)
        return (query.get("email", [""])[0] or "").strip()
    except Exception:
        return ""


def check_unpaywall_pdf(doi: str) -> Optional[str]:
    """
    Query Unpaywall by DOI and return a best OA full-text URL.
    Returns URL string when found, else None.
    """
    if not doi:
        return None

    email = (UNPAYWALL_EMAIL or "").strip() or _extract_unpaywall_email(REST_API_KEY)
    if not email:
        return None

    normalized_doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi.strip(), flags=re.IGNORECASE)
    if not normalized_doi:
        return None

    try:
        url = f"https://api.unpaywall.org/v2/{normalized_doi}"
        res = requests.get(url, params={"email": email}, timeout=20)
        if res.status_code != 200:
            return None

        payload = res.json()
        data = payload if isinstance(payload, dict) else {}
        best = data.get("best_oa_location") or {}

        for candidate in [best.get("url_for_pdf"), best.get("url")]:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        locations = data.get("oa_locations") or []
        if isinstance(locations, list):
            for location in locations:
                if not isinstance(location, dict):
                    continue
                for candidate in [location.get("url_for_pdf"), location.get("url")]:
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
        return None
    except Exception:
        return None

def webpage_to_pdf_via_selenium(doi: str, output_path: Path) -> bool:
    """
    將 DOI 對應的網頁截圖轉換為 PDF（最後的 fallback 策略）。
    使用 Chrome DevTools Protocol 的 Page.printToPDF 功能。
    Returns True if successful.
    """
    if not _ensure_selenium_modules():
        return False
    
    import base64
    
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    
    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(15)  # Add timeout limit
        
        # 訪問 DOI 頁面
        start_url = f"https://doi.org/{doi}"
        driver.get(start_url)
        time.sleep(5)  # 等待重定向和頁面載入
        
        # 使用 Chrome DevTools Protocol 生成 PDF
        print_options = {
            'landscape': False,
            'displayHeaderFooter': False,
            'printBackground': True,
            'preferCSSPageSize': True,
            'paperWidth': 8.27,   # A4 寬度 (英寸)
            'paperHeight': 11.69, # A4 高度 (英寸)
            'marginTop': 0.4,
            'marginBottom': 0.4,
            'marginLeft': 0.4,
            'marginRight': 0.4,
        }
        
        result = driver.execute_cdp_cmd('Page.printToPDF', print_options)
        pdf_data = base64.b64decode(result['data'])
        
        # 儲存 PDF
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'wb') as f:
            f.write(pdf_data)
        
        # 驗證檔案大小（至少 10KB 才算有效）
        if output_path.stat().st_size > 10 * 1024:
            return True
        else:
            output_path.unlink(missing_ok=True)
            return False
            
    except Exception:
        return False
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass


def download_via_selenium_doi(doi: str, output_path: Path) -> bool:
    """
    Attempts to download PDF via generic DOI resolution using Selenium.
    Returns True if successful.
    """
    if not _ensure_selenium_modules():
        return False

    # 1. Setup temp download dir
    temp_dir = output_path.parent / "temp_selenium_downloads"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Clean temp dir
    for path in temp_dir.iterdir():
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass

    # 2. Setup Driver
    chrome_options = Options()
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--headless=new")  # Run in background without visible window
    
    prefs = {
        "download.default_directory": str(temp_dir.absolute()),
        "download.prompt_for_download": False,
        "plugins.always_open_pdf_externally": True
    }
    chrome_options.add_experimental_option("prefs", prefs)

    driver = None
    try:
        driver = webdriver.Chrome(options=chrome_options)
        driver.set_page_load_timeout(15)  # Add timeout limit
        
        # 3. Visit DOI
        start_url = f"https://doi.org/{doi}"
        driver.get(start_url)
        time.sleep(5) # Wait for redirect
        
        # 4. Find PDF Link
        pdf_link = None
        
        # Heuristic 1: Meta Tag (Most reliable)
        try:
            meta_pdf = driver.find_element(By.XPATH, "//meta[@name='citation_pdf_url']")
            if meta_pdf:
                pdf_link = meta_pdf.get_attribute("content")
        except:
            pass
            
        # Heuristic 2: Link Analysis
        if not pdf_link:
            links = driver.find_elements(By.TAG_NAME, "a")
            for link in links:
                try:
                    href = link.get_attribute("href")
                    if href and ".pdf" in href.lower():
                        pdf_link = href
                        break 
                except: continue
        
        if pdf_link:
            driver.get(pdf_link)
            # Wait for download
            for _ in range(15):
                time.sleep(1)
                files = [
                    p for p in temp_dir.iterdir()
                    if p.is_file() and p.suffix.lower() not in {".crdownload", ".tmp", ".zip"}
                ]
                if files:
                    # Move to final location
                    shutil.move(str(files[0]), str(output_path))
                    return True
    except Exception:
        pass
    finally:
        if driver:
            try: driver.quit()
            except: pass
        try:
            temp_dir.rmdir()  # Cleanup if empty
        except Exception:
            pass
            
    return False

def download_full_text(doi: str, title: str, output_dir: Path, open_access_pdf_url: str = "") -> str:
    """
    Downloads the full text for a given DOI/Title.
    Strategies:
    1. Elsevier API (PDF)
    2. Record openAccessPdf URL (from upstream)
    3. Semantic Scholar OpenAccess PDF
    4. Springer Nature Open Access JATS XML
    5. OpenAlex Open Access URL
    6. Unpaywall Open Access URL
    7. Selenium DOI Scraper
    8. Fallback: Elsevier XML
    9. Webpage capture to PDF via Selenium
    """
    # Create safe filename
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
    safe_doi = doi.replace("/", "_")
    base_filename = f"{safe_doi}_{safe_title.replace(' ', '_')}"
    final_pdf_path = output_dir / f"{base_filename}.pdf"

    # --- Strategy 1: Elsevier API ---
    if ELSEVIER_API_KEY:
        article_url = f"https://api.elsevier.com/content/article/doi/{doi}?view=FULL"
        headers_dl_pdf = {
            "X-ELS-APIKey": ELSEVIER_API_KEY,
            "Accept": "application/pdf",
            "User-Agent": "ResearchScript/1.0"
        }
        if ELSEVIER_INST_TOKEN:
            headers_dl_pdf["X-ELS-Insttoken"] = ELSEVIER_INST_TOKEN

        try:
            res_dl = requests.get(article_url, headers=headers_dl_pdf, timeout=20)
            content_type = res_dl.headers.get("Content-Type", "").lower()
            content_len = len(res_dl.content)
            
            # Heuristic: Full text PDFs are rarely under 400KB
            is_full_pdf = res_dl.status_code == 200 and "pdf" in content_type and content_len > 400 * 1024
            
            if is_full_pdf:
                with open(final_pdf_path, "wb") as f:
                    f.write(res_dl.content)
                return f"Downloaded PDF (Elsevier) ({content_len} bytes)"
            elif res_dl.status_code == 200 and "xml" in content_type:
                 # It gave us XML instead of PDF, save it but continue trying for PDF??
                 # Usually if we asked for PDF and got XML, permission is limited.
                 # Let's try Strategy 2 before settling for XML.
                 pass
                 
        except Exception as e:
            pass # Continue to next strategy

    # --- Strategy 2: Open access URL from upstream record ---
    if open_access_pdf_url:
        try:
            res_oa = requests.get(open_access_pdf_url, timeout=30)
            content_type = res_oa.headers.get("Content-Type", "").lower()
            if res_oa.status_code == 200 and ("pdf" in content_type or len(res_oa.content) > 50_000):
                with open(final_pdf_path, "wb") as f:
                    f.write(res_oa.content)
                return f"Downloaded PDF (Record openAccessPdf) ({len(res_oa.content)} bytes)"
        except Exception:
            pass

    # --- Strategy 3: Semantic Scholar ---
    s2_pdf_url = check_semantic_scholar_pdf(doi)
    if s2_pdf_url:
        try:
            res_s2 = requests.get(s2_pdf_url, timeout=30)
            if res_s2.status_code == 200:
                with open(final_pdf_path, "wb") as f:
                    f.write(res_s2.content)
                return f"Downloaded PDF (Semantic Scholar) ({len(res_s2.content)} bytes)"
        except Exception:
            pass

    # --- Strategy 4: Springer Nature Open Access JATS ---
    if SPRINGER_API_KEY:
        try:
            if download_springer_jats(doi, final_pdf_path):
                xml_path = final_pdf_path.with_suffix(".xml")
                return f"Downloaded JATS XML (Springer OA) ({xml_path.stat().st_size} bytes)"
        except Exception:
            pass

    # --- Strategy 5: OpenAlex ---
    openalex_url = check_openalex_pdf(doi)
    if openalex_url:
        try:
            res_openalex = requests.get(openalex_url, timeout=30)
            content_type = res_openalex.headers.get("Content-Type", "").lower()
            if res_openalex.status_code == 200 and ("pdf" in content_type or len(res_openalex.content) > 50_000):
                with open(final_pdf_path, "wb") as f:
                    f.write(res_openalex.content)
                return f"Downloaded PDF (OpenAlex) ({len(res_openalex.content)} bytes)"
        except Exception:
            pass

    # --- Strategy 6: Unpaywall ---
    unpaywall_url = check_unpaywall_pdf(doi)
    if unpaywall_url:
        try:
            res_unpaywall = requests.get(unpaywall_url, timeout=30)
            content_type = res_unpaywall.headers.get("Content-Type", "").lower()
            if res_unpaywall.status_code == 200 and ("pdf" in content_type or len(res_unpaywall.content) > 50_000):
                with open(final_pdf_path, "wb") as f:
                    f.write(res_unpaywall.content)
                return f"Downloaded PDF (Unpaywall) ({len(res_unpaywall.content)} bytes)"
        except Exception:
            pass

    # --- Strategy 7: Selenium DOI Scraper ---
    # Only try this if we really don't have it yet
    # This is slow, so maybe log it
    try:
        # print(f"Attempting Selenium fallback for {doi}...")
        if download_via_selenium_doi(doi, final_pdf_path):
            return "Downloaded PDF (Selenium Scraper)"
    except Exception:
        pass

    # --- Strategy 8: Fallback Elsevier XML ---
    if ELSEVIER_API_KEY:
         try:
             headers_dl_xml = headers_dl_pdf.copy()
             headers_dl_xml["Accept"] = "text/xml"
             res_xml = requests.get(article_url, headers=headers_dl_xml, timeout=15)
             if res_xml.status_code == 200:
                 xml_path = output_dir / f"{base_filename}.xml"
                 with open(xml_path, "wb") as f:
                     f.write(res_xml.content)
                 return f"Downloaded XML Fallback ({len(res_xml.content)} bytes)"
         except: pass

    # --- Strategy 9: Webpage to PDF (網頁截圖轉 PDF) ---
    # 最後的 fallback：將網頁內容轉為 PDF
    try:
        if webpage_to_pdf_via_selenium(doi, final_pdf_path):
            return f"Downloaded PDF (Webpage Capture) ({final_pdf_path.stat().st_size} bytes)"
    except Exception:
        pass

    return "Failed to download full text"

# Default Configuration
@dataclass
class Config:
    input_file: Path = None
    output_file: Path = None
    # LLM provider: "openai" or "gemini"
    llm_provider: str = CONFIG.get("default_settings", {}).get("llm_provider", "openai")
    # OpenAI settings
    openai_api_key: str = CONFIG.get("openai_api_key", "")
    model: str = CONFIG.get("default_settings", {}).get("openai_model", "gpt-4.1-mini")
    models: List[str] = field(default_factory=list)
    # Gemini settings
    gemini_api_key: str = CONFIG.get("gemini_api_key", "")
    gemini_model: str = CONFIG.get("default_settings", {}).get("gemini_model", "gemini-2.0-flash")
    # Common settings
    temperature: float = 0.0
    max_tokens: int = 1000
    max_retries: int = 3
    target: str = None
    workers: int = 8
    download_pdf: bool = True

def setup_logger():
    """Set up logging configuration."""
    logger = logging.getLogger("safer_alt_en")
    logger.handlers.clear()  # Clear existing handlers
    
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger

def build_prompt(title, doi, abstract, target):
    """Build analysis prompt for OpenAI API."""
    return f"""
You are a rigorous scientific abstract reviewer. Only use the ABSTRACT below. Do NOT add external knowledge.

Task: Determine whether the abstract explicitly presents a **functional substitute** that **REPLACES or REDUCES the usage of {target}**.

**CRITICAL REQUIREMENT**: 
1. The abstract MUST explicitly mention "{target}" (or its common synonyms/abbreviations) by name.
2. The alternative must REPLACE or REDUCE the usage of "{target}" itself - either fully or partially.

Decision rules (follow strictly):
1) Answer **yes** if ALL of the following conditions are met:
   a) The abstract explicitly mentions "{target}" by name (or a well-known synonym);
   b) The abstract indicates that another substance/material:
      - Fully REPLACES {target} in the same application, OR
      - Partially REPLACES {target} (e.g., used as a co-monomer to reduce the amount of {target} needed), OR
      - Is explicitly described as a bio-based/sustainable alternative TO {target} itself (replacing petroleum-based {target}).

2) Answer **no** if ANY of the following is true:
   - "{target}" is NOT explicitly mentioned in the abstract;
   - The alternative replaces a DIFFERENT component in the formulation that is NOT {target} (e.g., if the abstract describes a formulation containing {target} but the alternative replaces an additive/coalescent/solvent/surfactant rather than {target} itself);
   - The alternative is meant only to enhance or modify properties without replacing any {target};
   - The abstract merely compares {target} with another material without any substitution intent;
   - It discusses a completely different chemical that happens to share a partial name;
   - {target} is used only as a reference/comparison material, not as the material being replaced.

3) Key distinction: If the abstract describes a formulation where {target} is one component and something else (like a coalescent, solvent, or additive) is being replaced, answer **no** — the alternative must specifically replace {target}, not other ingredients.

Output format (must be valid JSON; no extra text):
{{
  "reasoning": "<1–3 sentences. First state whether '{target}' is explicitly mentioned. Then explain whether the alternative replaces {target} itself or replaces something else in the formulation.>",
  "alternatives provided": "<'yes' or 'no'>",
  "alternatives": ["<name of alternative 1>", "<name of alternative 2>", ...]
}}

Note: The "alternatives" field should contain the specific names of functional substitutes for {target}. If no alternatives are provided ("alternatives provided": "no"), use an empty array [].

Paper info:
- Title: {title}
- DOI: {doi or 'N/A'}

ABSTRACT:
{abstract}
""".strip()

class SaferAlternativeAnalyzer:
    """Analyzer for safer alternatives using OpenAI or Gemini API."""
    
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        self.provider = cfg.llm_provider.lower()
        
        if self.provider == "gemini":
            if not GEMINI_AVAILABLE:
                raise RuntimeError("Google Generative AI SDK unavailable. Install with: pip install google-genai")
            if not self.cfg.gemini_api_key:
                raise RuntimeError("Gemini API key not found! Set GEMINI_API_KEY in .env")
            self.gemini_client = genai.Client(api_key=self.cfg.gemini_api_key)
            self.gemini_model = None  # Not used with new API
            self.models = [self.cfg.gemini_model]
            self.client = None
            logger.info(f"Using Gemini provider with model: {self.cfg.gemini_model}")
        else:
            # Default to OpenAI
            if OpenAI is None:
                raise RuntimeError("OpenAI Python SDK unavailable.")
            if not self.cfg.openai_api_key:
                raise RuntimeError("OpenAI API key not found in config file!")
            self.client = OpenAI(api_key=self.cfg.openai_api_key)
            self.models = self.cfg.models if self.cfg.models else [self.cfg.model]
            self.gemini_model = None
            logger.info(f"Using OpenAI provider with model(s): {self.models}")
        
        # Token usage tracking
        self._usage_lock = threading.Lock()
        self._usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        self._per_model_usage: Dict[str, Dict[str, int]] = {}

    @property
    def usage(self) -> Dict[str, int]:
        with self._usage_lock:
            return dict(self._usage)

    def _accumulate_usage(self, completion, model_name: Optional[str] = None):
        try:
            usage = getattr(completion, "usage", None)
            if not usage:
                return
            with self._usage_lock:
                self._usage["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                self._usage["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
                self._usage["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
                if model_name:
                    slot = self._per_model_usage.setdefault(model_name, {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
                    slot["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                    slot["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
                    slot["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
        except Exception:
            # Be resilient if SDK changes shape
            pass

    def _call_gemini(self, prompt: str, model: str) -> Dict[str, Any]:
        """Call Gemini API with rate limit handling."""
        last_err = None
        system_prompt = "You are a careful scientific abstract reviewer. Output strictly JSON."
        full_prompt = f"{system_prompt}\n\n{prompt}"
        
        for attempt in range(self.cfg.max_retries):
            try:
                response = self.gemini_client.models.generate_content(
                    model=model,
                    contents=full_prompt,
                    config=genai.types.GenerateContentConfig(
                        temperature=self.cfg.temperature,
                        max_output_tokens=self.cfg.max_tokens,
                        response_mime_type="application/json"
                    )
                )
                raw = response.text or "{}"
                # Handle potential markdown code blocks
                if raw.startswith("```"):
                    lines = raw.split("\n")
                    raw = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
                data = json.loads(raw)
                reasoning = str(data.get("reasoning", "")).strip()
                alt_provided = str(data.get("alternatives provided", "")).strip().lower()
                alt_provided = "yes" if alt_provided == "yes" else "no"
                alternatives_raw = data.get("alternatives", [])
                if isinstance(alternatives_raw, list):
                    alternatives = [str(a).strip() for a in alternatives_raw if a]
                else:
                    alternatives = []
                return {"model": model, "reasoning": reasoning, "alternatives provided": alt_provided, "alternatives": alternatives}
            except Exception as e:
                last_err = e
                err_str = str(e).lower()
                # Handle rate limit (429) and quota errors with longer backoff
                if "429" in err_str or "rate" in err_str or "quota" in err_str or "resource" in err_str:
                    wait_time = min(60, 5 * (2 ** attempt))  # 5, 10, 20, 40, 60 seconds
                    self.logger.warning(f"Gemini rate limit hit, waiting {wait_time}s (attempt {attempt + 1}/{self.cfg.max_retries})")
                    time.sleep(wait_time)
                elif attempt < self.cfg.max_retries - 1:
                    wait_time = 2 ** attempt
                    self.logger.debug(f"Gemini error: {e}, retrying in {wait_time}s")
                    time.sleep(wait_time)
        raise RuntimeError(last_err or "Unknown Gemini error")

    def _call_one_model(self, prompt: str, model: str) -> Dict[str, Any]:
        if self.provider == "gemini":
            return self._call_gemini(prompt, model)
        
        # OpenAI path
        last_err = None
        for attempt in range(self.cfg.max_retries):
            try:
                completion = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "You are a careful scientific abstract reviewer. Output strictly JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                    response_format={"type": "json_object"},
                )
                # Track token usage if available, per model
                self._accumulate_usage(completion, model)
                raw = completion.choices[0].message.content or "{}"
                data = json.loads(raw)
                reasoning = str(data.get("reasoning", "")).strip()
                alt_provided = str(data.get("alternatives provided", "")).strip().lower()
                alt_provided = "yes" if alt_provided == "yes" else "no"
                # Extract alternatives list
                alternatives_raw = data.get("alternatives", [])
                if isinstance(alternatives_raw, list):
                    alternatives = [str(a).strip() for a in alternatives_raw if a]
                else:
                    alternatives = []
                return {"model": model, "reasoning": reasoning, "alternatives provided": alt_provided, "alternatives": alternatives}
            except Exception as e:
                last_err = e
                if attempt < self.cfg.max_retries - 1:
                    time.sleep(2 ** attempt)
        raise RuntimeError(last_err or "Unknown error")

    def analyze_one(self, record):
        """Analyze a single record with all configured models in parallel."""
        title = record.get("title") or record.get("Article title") or ""
        
        # Extract DOI from externalIds
        doi = ""
        external_ids = record.get("externalIds", {})
        if isinstance(external_ids, dict):
            doi = external_ids.get("DOI", "")
        # Extract publication year if available
        year = record.get("year") or record.get("Year") or ""

        
        abstract = record.get("abstract") or record.get("Abstract") or ""

        if not abstract:
            return {
                "title": title,
                "doi": doi,
                "year": year,
                "abstract": abstract,
                "target": self.cfg.target,
                "reasoning": "No abstract provided.",
                "alternatives provided": "no",
                "alternatives": [],
            }

        prompt = build_prompt(title, doi, abstract, self.cfg.target)

        # Dispatch calls to all models in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        votes: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=min(len(self.models), 8)) as ex:
            futures = {ex.submit(self._call_one_model, prompt, m): m for m in self.models}
            for fut in as_completed(futures):
                model = futures[fut]
                try:
                    votes.append(fut.result())
                except Exception as e:
                    errors[model] = str(e)

        # Reduce to majority vote; tie-breaker prefers "no"
        yes_count = sum(1 for v in votes if v.get("alternatives provided") == "yes")
        no_count = sum(1 for v in votes if v.get("alternatives provided") == "no")
        final_alt = "yes" if yes_count > no_count else "no"
        # Choose reasoning and alternatives from a model that matched the final vote, else any
        chosen_reason = ""
        chosen_alternatives: list = []
        for v in votes:
            if v.get("alternatives provided") == final_alt and v.get("reasoning"):
                chosen_reason = v["reasoning"]
                chosen_alternatives = v.get("alternatives", [])
                break
        if not chosen_reason and votes:
            chosen_reason = votes[0].get("reasoning", "")
            chosen_alternatives = votes[0].get("alternatives", [])

        result: Dict[str, Any] = {
            "title": title,
            "doi": doi,
            "year": year,
            "abstract": abstract,
            "target": self.cfg.target,
            "alternatives provided": final_alt,
            "alternatives": chosen_alternatives,
            "reasoning": chosen_reason,
            "votes": votes,
        }
        if errors:
            result["errors"] = errors
        return result

    def analyze_one_with_model(self, record, model: str) -> Dict[str, Any]:
        """Analyze a single record using a specific model (no voting)."""
        title = record.get("title") or record.get("Article title") or ""
        doi = ""
        external_ids = record.get("externalIds", {})
        if isinstance(external_ids, dict):
            doi = external_ids.get("DOI", "")
        year = record.get("year") or record.get("Year") or ""
        abstract = record.get("abstract") or record.get("Abstract") or ""
        if not abstract:
            return {
                "title": title,
                "doi": doi,
                "year": year,
                "abstract": abstract,
                "target": self.cfg.target,
                "reasoning": "No abstract provided.",
                "alternatives provided": "no",
                "alternatives": [],
                "model_used": model,
            }
        prompt = build_prompt(title, doi, abstract, self.cfg.target)
        res = self._call_one_model(prompt, model)
        
        # Check and Download if alternative is found
        download_status = "N/A"
        alt_provided = res.get("alternatives provided", "no")
        
        if self.cfg.download_pdf and alt_provided.lower() == "yes" and doi:
            try:
                # Determine output directory
                download_dir = self.cfg.output_file.parent / "research_pdf"
                download_dir.mkdir(parents=True, exist_ok=True)
                open_access_pdf_url = ""
                open_access_pdf = record.get("openAccessPdf", {})
                if isinstance(open_access_pdf, dict):
                    open_access_pdf_url = str(open_access_pdf.get("url", "") or "").strip()
                
                # Call the global download function
                download_status = download_full_text(doi, title, download_dir, open_access_pdf_url=open_access_pdf_url)
            except Exception as e:
                download_status = f"Error triggering download: {e}"

        return {
            "title": title,
            "doi": doi,
            "year": year,
            "abstract": abstract,
            "target": self.cfg.target,
            "alternatives provided": alt_provided,
            "alternatives": res.get("alternatives", []),
            "reasoning": res.get("reasoning", ""),
            "model_used": model,
            "download_status": download_status,
        }

    def run(self, records):
        """Run analysis on all records in parallel using distribute mode.

        Records are assigned to models in round-robin (one model per record).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import json

        total = len(records)
        results: List[Optional[Dict[str, Any]]] = [None] * total

        # --- Automatic Checkpoint & Resume Logic ---
        processed_dois = {}
        processed_titles = {}
        if self.cfg.output_file.exists():
            try:
                with open(self.cfg.output_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                for r in cached:
                    if r.get("doi"):
                        processed_dois[r["doi"]] = r
                    elif r.get("title"):
                        processed_titles[r["title"]] = r
                self.logger.info(f"Loaded {len(cached)} cached records from {self.cfg.output_file.name}")
            except Exception as e:
                self.logger.warning(f"Failed to load cache from {self.cfg.output_file.name}: {e}")

        tasks_to_run = []
        for i, rec in enumerate(records):
            doi = rec.get("externalIds", {}).get("DOI", "") if isinstance(rec.get("externalIds"), dict) else ""
            title = rec.get("title") or rec.get("Article title") or ""
            
            cached_res = processed_dois.get(doi) if doi else processed_titles.get(title)
            expected_model = self.models[i % len(self.models)] if self.models else self.cfg.model
            
            # 若快取的模型與本次預計使用的模型相同，才套用快取；否則視為沒跑過，重新放入排程
            if cached_res and cached_res.get("model_used") == expected_model:
                results[i] = cached_res
            else:
                tasks_to_run.append((i, rec))
        # --------------------------------------------

        def task_distribute(idx_record):
            idx, rec = idx_record
            # Round-robin choose model for this record
            model = self.models[idx % len(self.models)] if self.models else self.cfg.model
            return idx, self.analyze_one_with_model(rec, model)

        if tasks_to_run:
            self.logger.info(f"Resuming {len(tasks_to_run)} uncompleted tasks out of {total} total.")
            with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.workers))) as ex:
                futures = {ex.submit(task_distribute, item): item[0] for item in tasks_to_run}
                
                completed = total - len(tasks_to_run)
                pbar = tqdm(total=total, initial=completed, desc=f"Analyzing abstracts for {self.cfg.target}")
                save_interval = min(50, max(2, len(tasks_to_run) // 10))  # Checkpoint frequency
                completed_since_save = 0

                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        idx, res = fut.result()
                        results[idx] = res
                    except Exception as e:
                        # On failure, store a minimal error result to keep alignment
                        results[i] = {
                            "title": str(records[i].get("title", "")),
                            "doi": str(records[i].get("externalIds", {}).get("DOI", "")),
                            "year": records[i].get("year") or records[i].get("Year") or "",
                            "abstract": records[i].get("abstract") or records[i].get("Abstract") or "",
                            "target": self.cfg.target,
                            "alternatives provided": "no",
                            "alternatives": [],
                            "reasoning": f"Analysis failed: {e}",
                            "errors": {"record": str(e)},
                        }
                    
                    pbar.update(1)
                    completed_since_save += 1
                    
                    # Checkpoint save
                    if completed_since_save >= save_interval:
                        try:
                            # Save currently completed records
                            current_completed = [r for r in results if r is not None]
                            with open(self.cfg.output_file, 'w', encoding='utf-8') as f:
                                json.dump(current_completed, f, ensure_ascii=False, indent=2)
                            completed_since_save = 0
                        except Exception:
                            pass

                pbar.close()
        else:
            self.logger.info("All records were already processed and loaded from cache.")

        # Filter out any None placeholders (shouldn't happen) and return in input order
        return [r for r in results if r is not None]

def load_input_json(path):
    """Load input JSON file."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for k in ["results", "papers", "items", "data"]:
            if k in data and isinstance(data[k], list):
                return data[k]
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("Unsupported JSON structure for input.")

def save_outputs(results, output_file):
    """Save output file - only the main JSON file."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Only save the main output JSON file
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    
    print(f"Results saved to: {output_path}")

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Analyze abstracts for safer alternatives relative to a target.")
    parser.add_argument("--input_file", required=True, help="Input JSON file path")
    parser.add_argument("--output_file", required=True, help="Output JSON file path")
    parser.add_argument("--target", required=True, help="Target compound name")
    parser.add_argument("--api_key", help="OpenAI API key (optional, overrides config file)")
    parser.add_argument("--model", help="OpenAI model name (optional, overrides config file)")
    parser.add_argument("--models", help="Comma-separated list of OpenAI model names to run in parallel")
    parser.add_argument("--temperature", type=float, default=0.0, help="Temperature setting")
    parser.add_argument("--max_tokens", type=int, default=1000, help="Maximum tokens")
    parser.add_argument(
        "--max_retries",
        type=int,
        default=CONFIG.get("default_settings", {}).get("max_retries", 3),
        help="Maximum retry attempts",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=CONFIG.get("default_settings", {}).get("step03_workers", 8),
        help="Thread workers for parallelism",
    )
    parser.add_argument(
        "--download_pdf",
        action=argparse.BooleanOptionalAction,
        default=bool(CONFIG.get("default_settings", {}).get("step03_download_pdf", True)),
        help="Download PDF for records where alternatives provided == yes",
    )
    parser.add_argument("--years_back", type=int, default=CONFIG.get("default_settings", {}).get("years_back", 20), 
                        help="Only analyze papers from the last N years (default 20)")
    return parser.parse_args()

def main():
    """Main execution function."""
    args = parse_args()
    logger = setup_logger()

    # Create config with arguments (command line args override config file)
    # Parse models list if provided
    models_list: List[str] = []
    if args.models:
        models_list = [m.strip() for m in args.models.split(',') if m.strip()]

    cfg = Config(
        input_file=Path(args.input_file),
        output_file=Path(args.output_file),
        openai_api_key=args.api_key or CONFIG.get("openai_api_key", ""),
        model=args.model or CONFIG.get("default_settings", {}).get("openai_model", "gpt-4.1-mini"),
        models=models_list,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        target=args.target,
        max_retries=args.max_retries,
        workers=args.workers,
        download_pdf=args.download_pdf,
    )

    logger.info(f"Target: {cfg.target}")
    logger.info(f"Input: {cfg.input_file}")
    logger.info(f"Output: {cfg.output_file}")
    logger.info(f"LLM Provider: {cfg.llm_provider}")
    logger.info(f"PDF download enabled: {cfg.download_pdf}")
    if cfg.llm_provider.lower() == "gemini":
        logger.info(f"Model: {cfg.gemini_model} | mode=distribute")
    elif cfg.models:
        logger.info(f"Models: {', '.join(cfg.models)} | mode=distribute")
    else:
        logger.info(f"Model: {cfg.model} | mode=distribute")

    # Load and process records
    records = load_input_json(cfg.input_file)
    
    # Apply time filtering if years_back is specified
    if args.years_back > 0:
        from datetime import datetime
        current_year = datetime.now().year
        min_year = current_year - max(0, args.years_back - 1)
        before_count = len(records)
        
        records = [
            record for record in records
            if isinstance(record, dict) and 
            isinstance(record.get("year"), int) and 
            record["year"] >= min_year
        ]
        
        logger.info(f"Time filtering: {len(records)}/{before_count} papers from last {args.years_back} years (>= {min_year})")
    
    analyzer = SaferAlternativeAnalyzer(cfg, logger)
    results = analyzer.run(records)
    
    # Save outputs - only main JSON file
    save_outputs(results, cfg.output_file)

    # Print summary
    total_records = len(results)
    alternatives_found = sum(1 for r in results if r.get("alternatives provided") == "yes")

    logger.info(f"Analysis completed for {cfg.target}")
    logger.info(f"Total records processed: {total_records}")
    if total_records > 0:
        logger.info(f"Records with alternatives: {alternatives_found} ({alternatives_found/total_records*100:.1f}%)")
    else:
        logger.info(f"Records with alternatives: {alternatives_found} (0.0%)")
    logger.info(f"Results saved to: {cfg.output_file}")

    # Save token usage summary next to output
    try:
        output_stem = cfg.output_file.stem
        if output_stem.startswith("step03_results"):
            suffix = output_stem.replace("step03_results", "")
            usage_filename = f"step03_token_usage{suffix}.json"
        else:
            usage_filename = "step03_token_usage.json"
        
        usage_path = cfg.output_file.with_name(usage_filename)
        with usage_path.open("w", encoding="utf-8") as f:
            json.dump({
                "model_list": analyzer.models,
                "prompt_tokens": analyzer.usage.get("prompt_tokens", 0),
                "completion_tokens": analyzer.usage.get("completion_tokens", 0),
                "total_tokens": analyzer.usage.get("total_tokens", 0),
                "per_model": analyzer._per_model_usage,
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"Token usage saved to: {usage_path}")
    except Exception as e:
        logger.warning(f"Failed to write token usage file: {e}")

if __name__ == "__main__":
    main()
