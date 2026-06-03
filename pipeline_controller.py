import pandas as pd
import os
import sys
import json
import logging
import importlib
from pathlib import Path
from typing import List, Dict, Any, Optional
import subprocess
from urllib.parse import urljoin
from tqdm import tqdm
from pathlib import Path

# Configuration file path
from config_loader import get_config

# Load configuration (.env first, fallback to api_config.json)
CONFIG = get_config()

# Get the directory where this script is located
SCRIPT_DIR = Path(__file__).parent.resolve()

# Configuration
INPUT_CSV = SCRIPT_DIR / "chemicals_test.csv"  # 包含 26筆化合物資料的 CSV 檔
OUTPUT_BASE_DIR = SCRIPT_DIR / "outputs"
COMPOUND_COLUMN = "name"  # CSV 中化合物名稱的欄位名

# Pipeline steps configuration 
STEP  = { 
    "step00": {
        "script": "step00.py",
        "output_file": "step00_queries.json",
        "description": "Generating search queries"
    },
    "step01": {
        "script": "step01.py",
        "output_file": "step01_results.json",
        "description": "Fetching papers"
    },
    "step02": {
        "script": "step02.py", 
        "output_file": "step02_results.json",
        "description": "Fetching abstracts"
    }, 
    "step03": {
        "script": "step03.py",
        "output_file": "step03_results.json",
        "description": "Analyzing alternatives"
    },
    "step04": {
        "script": "step04.py",
        "output_file": "step04_results.json",
        "description": "Extracting alternatives"
    }
}

class PipelineController:
    """Multi-compound pipeline controller with progress tracking."""
    
    def __init__(self, input_csv: str, output_base_dir: Path):
        self.input_csv = input_csv
        self.output_base_dir = output_base_dir
        self.cid_map: Dict[str, str] = {}
        self.compounds = self.load_compounds()
        self.setup_logging()
        
    def setup_logging(self):
        """Setup logging configuration."""
        stdout_handler = logging.StreamHandler(
            open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', closefd=False)
        )
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(SCRIPT_DIR / 'pipeline.log', encoding='utf-8'),
                stdout_handler,
            ]
        )
        self.logger = logging.getLogger(__name__)

    @property
    def current_model_name(self) -> str:
        # Get the provider and model string, handle commas if multiple models are specified
        provider = CONFIG.get("default_settings", {}).get("llm_provider", "openai").lower()
        if provider == "gemini":
            models = CONFIG.get("default_settings", {}).get("gemini_models", "")
            model = models.split(",")[0] if models else CONFIG.get("default_settings", {}).get("gemini_model", "gemini-1.5-flash")
        else:
            models = CONFIG.get("default_settings", {}).get("openai_models", "")
            model = models.split(",")[0] if models else CONFIG.get("default_settings", {}).get("openai_model", "default")
        return model.replace(":", "-").replace("/", "-")

    def get_step03_filename(self) -> str:
        return f"step03_results_{self.current_model_name}.json"

    def get_step04_filename(self) -> str:
        return f"step04_results_{self.current_model_name}.json"

    def get_step04_summary_filename(self) -> str:
        return f"step04_summary_{self.current_model_name}.json"

    
    def load_compounds(self) -> List[str]:
        """Load compound names (and optional CID) from CSV file.

        Expects a column named by COMPOUND_COLUMN (default 'name'). If a 'cid' or 'CID'
        column exists, it is stored in self.cid_map keyed by compound name (string).
        """
        try:
            df = pd.read_csv(self.input_csv)
            compounds = df[COMPOUND_COLUMN].tolist()
            # Build CID mapping if present
            cid_col = None
            for c in df.columns:
                if str(c).lower() == 'cid':
                    cid_col = c
                    break
            if cid_col is not None:
                try:
                    self.cid_map = {
                        str(row[COMPOUND_COLUMN]): str(row[cid_col])
                        for _, row in df.iterrows()
                        if pd.notna(row.get(COMPOUND_COLUMN)) and pd.notna(row.get(cid_col))
                    }
                except Exception:
                    self.cid_map = {}
            print(f"Loaded {len(compounds)} compounds: {compounds}")
            return compounds
        except Exception as e:
            print(f"Error loading compounds from {self.input_csv}: {e}")
            sys.exit(1)
    
    def create_compound_directory(self, compound: str) -> Path:
        """Create output directory for a specific compound."""
        compound_dir = self.output_base_dir / compound
        compound_dir.mkdir(parents=True, exist_ok=True)
        return compound_dir
    
    def check_step04_has_data(self, compound_dir: Path) -> bool:
        """Check if step04 extracted any dosage data."""
        step04_summary_file = compound_dir / self.get_step04_summary_filename()
        if not step04_summary_file.exists():
            return False
            
        try:
            with open(step04_summary_file, 'r', encoding='utf-8') as f:
                summary = json.load(f)
                
            stats = summary.get("statistics", {})
            # Check if any dosage data was successfully extracted
            useful_data_count = stats.get("extracted", 0)
            
            return useful_data_count > 0
            
        except Exception as e:
            self.logger.error(f"Error reading step04 summary: {e}")
            return False

    def check_step03_has_alternatives(self, compound_dir: Path) -> bool:
        """Check if step03 found any alternatives (alternatives provided = 'yes')."""
        step03_file = compound_dir / self.get_step03_filename()
        if not step03_file.exists():
            self.logger.warning(f"Step03 results file not found: {step03_file}")
            return False
        
        try:
            with open(step03_file, 'r', encoding='utf-8') as f:
                results = json.load(f)
            
            # Count papers with alternatives provided = 'yes'
            alternatives_count = sum(
                1 for result in results 
                if isinstance(result, dict) and 
                result.get("alternatives provided", "").lower() == "yes"
            )
            
            total_papers = len(results)
            self.logger.info(f"Step03 results: {alternatives_count}/{total_papers} papers have alternatives")
            
            # Return True if at least one paper has alternatives
            return alternatives_count > 0
            
        except Exception as e:
            self.logger.error(f"Error reading step03 results: {e}")
            return False
    
    def recursive_step03_search(self, compound: str, compound_dir: Path, current_years: int) -> bool:
        """Recursively search for alternatives by extending time range."""
        # Get configuration parameters
        years_extension = CONFIG.get("default_settings", {}).get("years_extension", 10)
        max_search_years = CONFIG.get("default_settings", {}).get("max_search_years", 30)
        
        self.logger.info(f"Searching for alternatives in last {current_years} years for {compound}")
        
        # Run step03 with current time range
        success = self.run_step03(compound, compound_dir, years_back=current_years)
        if not success:
            self.logger.warning(f"Step03 execution failed for {compound} with {current_years} years")
            return False
        
        # Check if alternatives were found
        has_alternatives = self.check_step03_has_alternatives(compound_dir)
        
        if has_alternatives:
            self.logger.info(f"Found alternatives for {compound} within {current_years} years")
            return True
        
        # If no alternatives found and we haven't reached the limit, try extending
        if current_years < max_search_years:
            next_years = min(current_years + years_extension, max_search_years)
            
            self.logger.info(f"No alternatives found in {current_years} years. Extending search to {next_years} years...")
            
            # Backup current results before trying extended search
            original_step03_file = compound_dir / self.get_step03_filename()
            import time
            timestamp = int(time.time())
            backup_step03_file = compound_dir / f"step03_results_backup_{self.current_model_name}_{current_years}y_{timestamp}.json"
            if original_step03_file.exists():
                import shutil
                shutil.copy2(original_step03_file, backup_step03_file)
            
            # Recursively try with extended time range
            return self.recursive_step03_search(compound, compound_dir, next_years)
        else:
            self.logger.info(f"Reached maximum search range of {max_search_years} years for {compound}. No alternatives found.")
            # 覆蓋 step03_results.json 寫入 no paper found
            step03_file = compound_dir / self.get_step03_filename()
            with open(step03_file, "w", encoding="utf-8") as f:
                json.dump({"no paper found": True}, f, ensure_ascii=False, indent=2)
            
            # 在 final_output 資料夾創建以日期_cid命名的檔案
            cid_val = self.cid_map.get(compound)
            if cid_val:
                from datetime import datetime
                date_str = datetime.now().strftime("%Y%m%d")
                final_output_dir = SCRIPT_DIR / "final_output"
                final_output_dir.mkdir(parents=True, exist_ok=True)
                final_output_file = final_output_dir / f"{date_str}_{cid_val}.json"
                
                with open(final_output_file, "w", encoding="utf-8") as f:
                    json.dump({"no paper found": True}, f, ensure_ascii=False, indent=2)
                self.logger.info(f"Created no paper found file: {final_output_file}")
            
            return False
          
    def run_step_with_progress(self, step_name: str, command_args: List[str], 
                             description: str, compound: str) -> bool:
        """Run a step and show real-time output."""
        try:
            # 使用 Popen 來即時顯示輸出
            process = subprocess.Popen(
                command_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )
            
            # 即時顯示輸出
            output_lines = []
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    output_lines.append(output.strip())
                    # 直接輸出，讓 tqdm 能正常顯示
                    print(output, end='')
            
            # 等待進程結束
            return_code = process.poll()
            
            if return_code == 0:
                self.logger.info(f"{step_name} completed successfully for {compound}")
                return True
            else:
                self.logger.error(f"{step_name} failed for {compound} with return code {return_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"{step_name} failed for {compound}: {e}")
            return False
    
    def run_step00(self, compound: str, compound_dir: Path) -> bool:
        """Run step 00: Generate search queries via AI Agentic Search."""
        print(f"\n{'='*60}")
        print(f"Step 00: Generating search queries for {compound}")
        print(f"{'='*60}")
        
        command_args = [
            sys.executable, str(SCRIPT_DIR / "step00.py"),
            compound
        ]
        
        # We can run it directly and save the queries to JSON
        try:
            from step00 import generate_search_queries
            queries = generate_search_queries(compound)
            if not queries:
                self.logger.warning(f"No queries generated for {compound}, continuing with standard search.")
                return True # non-fatal
                
            out_file = compound_dir / "step00_queries.json"
            with open(out_file, "w", encoding="utf-8") as f:
                json.dump({"compound": compound, "queries": queries}, f, indent=2, ensure_ascii=False)
            
            self.logger.info(f"Generated {len(queries)} queries for {compound}, saved to {out_file.name}")
            return True
        except Exception as e:
            self.logger.error(f"Error in step 00 for {compound}: {e}")
            return False

    def run_step01(self, compound: str, compound_dir: Path) -> bool:
        """Run step 01: Fetch papers for a compound."""
        print(f"\n{'='*60}")
        print(f"Step 01: Fetching papers for {compound}")
        print(f"{'='*60}")
        
        # Use max_search_years for step01 year_range to ensure enough papers for step03 expansion
        max_search_years = CONFIG.get("default_settings", {}).get("max_search_years", 30)
        
        command_args = [
            sys.executable, str(SCRIPT_DIR / "step01.py"),
            "--keyword", compound,
            "--output_dir", str(compound_dir),
            "--output_file", "step01_results.json",
            "--year_range", str(max_search_years)
        ]
        
        return self.run_step_with_progress("Step 01", command_args, "Fetching papers", compound)
    
    def run_step02(self, compound: str, compound_dir: Path) -> bool:
        """Run step 02: Fetch abstracts."""
        print(f"\n{'='*60}")
        print(f"Step 02: Fetching abstracts for {compound}")
        print(f"{'='*60}")
        
        input_file = compound_dir / "step01_results.json"
        if not input_file.exists():
            self.logger.error(f"Input file not found for step 02: {input_file}")
            return False
        
        command_args = [
            sys.executable, str(SCRIPT_DIR / "step02.py"),
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / "step02_results.json")
        ]
        # Optional parallel workers from config
        step02_workers = CONFIG.get("default_settings", {}).get("step02_workers")
        if isinstance(step02_workers, int) and step02_workers > 0:
            command_args += ["--workers", str(step02_workers)]
        
        return self.run_step_with_progress("Step 02", command_args, "Fetching abstracts", compound)
    
    def run_step03(self, compound: str, compound_dir: Path, years_back: int = None) -> bool:
        """Run step 03: Reasoning analysis."""
        print(f"\n{'='*60}")
        print(f"Step 03: Analyzing alternatives for {compound}")
        print(f"{'='*60}")
        
        input_file = compound_dir / "step02_results.json"
        if not input_file.exists():
            self.logger.error(f"Input file not found for step 03: {input_file}")
            return False
        
        command_args = [
            sys.executable, str(SCRIPT_DIR / "step03.py"),
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / self.get_step03_filename()),
            "--target", compound
        ]
        # If config specifies parallel models, pass them through
        openai_models = CONFIG.get("default_settings", {}).get("openai_models")
        if isinstance(openai_models, str) and openai_models.strip():
            command_args += ["--models", openai_models]
        else:
            # fallback to single model if present
            single_model = CONFIG.get("default_settings", {}).get("openai_model")
            if isinstance(single_model, str) and single_model.strip():
                command_args += ["--model", single_model]
        # Optional step03 workers
        step03_workers = CONFIG.get("default_settings", {}).get("step03_workers")
        if isinstance(step03_workers, int) and step03_workers > 0:
            command_args += ["--workers", str(step03_workers)]
        step03_download_pdf = CONFIG.get("default_settings", {}).get("step03_download_pdf")
        if isinstance(step03_download_pdf, bool):
            command_args += ["--download_pdf" if step03_download_pdf else "--no-download_pdf"]
        
        # Add years_back parameter if specified
        if years_back is not None:
            command_args += ["--years_back", str(years_back)]
        
        return self.run_step_with_progress("Step 03", command_args, "Analyzing alternatives", compound)
    
    def run_step04(self, compound: str, compound_dir: Path, write_final: bool = True) -> bool:
        """Run step 04: Extract alternatives.

        Args:
            write_final: If True and a CID is available, write results to final_output/.
                         Set to False for intermediate runs (e.g. Phase C) where the
                         final output will be written after merging.
        """
        print(f"\n{'='*60}")
        print(f"Step 04: Extracting alternatives for {compound}")
        print(f"{'='*60}")
        
        input_file = compound_dir / self.get_step03_filename()
        if not input_file.exists():
            self.logger.error(f"Input file not found for step 04: {input_file}")
            return False
        
        command_args = [
            sys.executable, str(SCRIPT_DIR / "step04.py"),
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / self.get_step04_filename()),
            "--target", compound
        ]
        # Optional: drop empty alternatives as per config
        step04_drop_empty = CONFIG.get("default_settings", {}).get("step04_drop_empty")
        if isinstance(step04_drop_empty, bool) and step04_drop_empty:
            command_args += ["--drop_empty"]
        # Only write final output when explicitly requested
        if write_final:
            cid_val = self.cid_map.get(compound)
            if cid_val:
                command_args += ["--cid", str(cid_val), "--final_dir", str(SCRIPT_DIR / "final_output")]
        
        return self.run_step_with_progress("Step 04", command_args, "Extracting alternatives", compound)

    # ------------------------------------------------------------------
    # PHASE C: Alternative + Context Fallback Search
    # ------------------------------------------------------------------

    def check_step04_needs_phase_c(self, compound_dir: Path) -> bool:
        """Return True if step04 found alternatives but could not extract any dosage.

        Specifically: at least one record has ``alternatives provided == 'yes'``
        AND ``dosage_info.status in ('insufficient_data', 'not_found')``,
        while no record has status ``'extracted'`` or ``'partial_data'``.
        """
        step04_file = compound_dir / self.get_step04_filename()
        if not step04_file.exists():
            return False
        try:
            with open(step04_file, encoding="utf-8") as f:
                results = json.load(f)
            has_dosage = any(
                r.get("dosage_info", {}).get("status") == "extracted"
                for r in results
            )
            if has_dosage:
                return False
            needs_search = any(
                r.get("alternatives provided", "").lower() == "yes"
                and r.get("dosage_info", {}).get("status") in ("insufficient_data", "not_found")
                for r in results
            )
            return needs_search
        except Exception as e:
            self.logger.error(f"Error reading step04 for Phase C check: {e}")
            return False

    # ------------------------------------------------------------------
    # ESI (Supplementary Information) Fallback Download
    # ------------------------------------------------------------------

    def has_any_complete_extraction(self, compound_dir: Path) -> bool:
        """Check if step04 results contain any paper with complete extraction.
        
        Complete extraction = has_material_dosage is True.
        If at least one paper has complete extraction, ESI fallback is not needed.
        """
        step04_file = compound_dir / self.get_step04_filename()
        if not step04_file.exists():
            return False
        
        try:
            with open(step04_file, encoding="utf-8") as f:
                results = json.load(f)
            
            for r in results:
                dosage_info = r.get("dosage_info", {})
                # Check if this paper has material dosage
                if dosage_info.get("has_material_dosage") is True:
                    return True
                if dosage_info.get("completeness") == "complete":
                    return True
        except Exception as e:
            self.logger.error(f"Error checking complete extraction: {e}")
        
        return False

    def get_insufficient_papers_needing_esi(
        self, compound_dir: Path, skip_dois: Optional[set[str]] = None
    ) -> list[dict]:
        """Find papers without complete extraction (missing material dosage) that might have ESI.
        
        Complete extraction = has material dosage (input quantities like eq, wt%, mol ratio)
        Papers with only result dosages (output like concentration, yield) need ESI supplement.

        skip_dois: DOIs already attempted in a previous ESI pass (e.g. Phase A), to avoid
                   retrying the same papers in Phase B.

        Returns list of dicts with keys: doi, title, alternatives, reason
        """
        step04_file = compound_dir / self.get_step04_filename()
        if not step04_file.exists():
            return []
        
        papers: list[dict] = []
        try:
            with open(step04_file, encoding="utf-8") as f:
                results = json.load(f)
            
            research_pdf_dir = compound_dir / "research_pdf"
            
            for r in results:
                dosage_info = r.get("dosage_info", {})
                status = dosage_info.get("status")
                
                # Skip if already has complete extraction (has material dosage)
                if dosage_info.get("has_material_dosage") is True:
                    continue
                if dosage_info.get("completeness") == "complete":
                    continue
                
                # Skip irrelevant papers
                if status == "irrelevant":
                    continue
                
                # Skip papers without alternatives
                if r.get("alternatives provided", "").lower() != "yes":
                    continue
                
                doi = r.get("doi", "")
                if not doi:
                    continue

                # Skip DOIs already attempted in a prior ESI pass
                if skip_dois and doi in skip_dois:
                    continue
                
                # Check if ESI already exists in research_pdf
                existing_esi = self._find_existing_esi(doi, research_pdf_dir)
                if existing_esi:
                    self.logger.info(f"ESI already exists for {doi}: {existing_esi.name}")
                    continue
                
                # Determine reason for needing ESI
                if status == "partial_data" and dosage_info.get("missing") == "material_dosage":
                    reason = "has_result_dosage_only"
                elif status == "insufficient_data":
                    reason = "no_dosage_found"
                else:
                    reason = "incomplete_extraction"
                
                papers.append({
                    "doi": doi,
                    "title": r.get("title", "")[:60],
                    "alternatives": r.get("alternatives", []),
                    "reason": reason,
                    "current_status": status,
                })
        except Exception as e:
            self.logger.error(f"Error finding papers needing ESI: {e}")
        
        return papers

    def _find_existing_esi(self, doi: str, research_pdf_dir: Path) -> Path | None:
        """Check if ESI file already exists for a DOI."""
        if not research_pdf_dir.exists():
            return None
        
        import re
        # Extract article ID from DOI
        doi_parts = doi.split("/")
        if len(doi_parts) >= 2:
            article_id = doi_parts[-1].lower().replace(".", "").replace("-", "")
        else:
            return None
        
        for f in research_pdf_dir.iterdir():
            fname_lower = f.name.lower()
            # ESI patterns: -s001, -s002, -sup-0001 (Wiley), _esi, supporting, supplementary
            if "-s00" in fname_lower or "-sup-" in fname_lower or "_esi" in fname_lower:
                if article_id[:8] in fname_lower.replace(".", "").replace("-", ""):
                    return f
            if "support" in fname_lower or "supplement" in fname_lower:
                if article_id[:8] in fname_lower.replace(".", "").replace("-", ""):
                    return f
        
        return None

    def _get_esi_url_from_doi(self, doi: str) -> str | None:
        """Get ESI download URL for a DOI by visiting the article page."""
        try:
            sync_api = importlib.import_module("playwright.sync_api")
            PlaywrightError = sync_api.Error
            sync_playwright = sync_api.sync_playwright
        except ModuleNotFoundError:
            self.logger.warning("[ESI] playwright not installed; skipping browser-based ESI URL lookup.")
            return None
        
        # Determine publisher and article URL
        if "wiley" in doi.lower() or doi.startswith("10.1002/"):
            article_url = f"https://onlinelibrary.wiley.com/doi/{doi}"
        elif doi.startswith("10.1021/"):
            article_url = f"https://pubs.acs.org/doi/{doi}"
        elif doi.startswith("10.1039/"):
            article_url = f"https://pubs.rsc.org/en/content/articlelanding/{doi}"
        elif doi.startswith("10.1016/"):
            article_url = f"https://www.sciencedirect.com/science/article/pii/{doi.split('/')[-1]}"
        else:
            # Generic DOI resolver
            article_url = f"https://doi.org/{doi}"
        
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    headless=False,
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--start-maximized',
                    ],
                )
                context = browser.new_context(accept_downloads=True, no_viewport=True)
                page = context.new_page()
                self.logger.info(f"Visiting {article_url} to find ESI link...")
                page.goto(article_url, wait_until="domcontentloaded", timeout=60000)

                max_wait = 30000 if doi.startswith("10.1002/") else 15000
                page.wait_for_timeout(max_wait)

                esi_xpaths = [
                    '//a[contains(@href, "/doi/suppl/") and contains(@href, "suppl_file")]',
                    '//a[contains(@href, "downloadSupplement")]',
                    '//a[contains(@href, "suppdata")]',
                    '//a[contains(@href, "supplement") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
                    '//a[contains(@href, "Supplement") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
                    '//a[contains(@href, "supporting") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
                    '//a[contains(text(), "Supporting Information") and not(contains(@href, "?goto="))]',
                    '//a[contains(text(), "Supplementary") and not(contains(@href, "?goto="))]',
                    '//section[@id="support-info"]//a[contains(@href, ".pdf")]',
                ]

                for xpath in esi_xpaths:
                    locator = page.locator(f'xpath={xpath}')
                    count = locator.count()
                    if count == 0:
                        continue
                    for index in range(count):
                        href = locator.nth(index).get_attribute('href') or ''
                        if href and ('pdf' in href.lower() or 'download' in href.lower()):
                            return urljoin(page.url, href)
                return None
        except PlaywrightError as e:
            self.logger.error(f"Error finding ESI URL for {doi}: {e}")
            return None

    def _save_esi_content(
        self, content: bytes, doi_safe: str, index: int, research_pdf_dir: Path
    ) -> Optional[Path]:
        """Save ESI content with correct extension (.pdf or .xml).
        Returns saved path, or None if content appears invalid (too short)."""
        if len(content) < 1000:
            return None
        if content[:4] == b"%PDF":
            ext = ".pdf"
        elif content[:5] in (b"<?xml", b"<art ") or b"<article" in content[:200]:
            ext = ".xml"
        else:
            ext = ".pdf"  # Assume PDF as fallback
        suffix = f"_ESI{index}" if index > 1 else "_ESI"
        out_path = research_pdf_dir / f"{doi_safe}{suffix}{ext}"
        out_path.write_bytes(content)
        self.logger.info(f"[ESI] Saved: {out_path.name} ({len(content)} bytes)")
        return out_path

    def _try_rsc_esi(self, doi: str, research_pdf_dir: Path) -> bool:
        """Download RSC ESI files, cycling through {suffix}1.pdf, {suffix}2.pdf until 404."""
        import httpx
        research_pdf_dir.mkdir(parents=True, exist_ok=True)
        doi_suffix = doi.split("/")[-1].lower()
        first_part = doi_suffix[:2]
        journal = doi_suffix[2:4]
        base_url = f"https://www.rsc.org/suppdata/{first_part}/{journal}/{doi_suffix}"
        doi_safe = doi.replace("/", "_")
        downloaded = False
        try:
            with httpx.Client(follow_redirects=True, timeout=300) as client:
                for i in range(1, 6):  # Try ESI 1–5
                    url = f"{base_url}/{doi_suffix}{i}.pdf"
                    out_pdf = research_pdf_dir / f"{doi_safe}_ESI{i}.pdf"
                    out_xml = research_pdf_dir / f"{doi_safe}_ESI{i}.xml"
                    if out_pdf.exists() or out_xml.exists():
                        downloaded = True
                        continue
                    try:
                        resp = client.get(url)
                        if resp.status_code == 200:
                            saved = self._save_esi_content(resp.content, doi_safe, i, research_pdf_dir)
                            if saved:
                                downloaded = True
                        elif resp.status_code == 404:
                            break  # No more ESI files
                    except Exception as e:
                        self.logger.warning(f"[ESI][RSC] ESI {i} error: {e}")
                        break
        except Exception as e:
            self.logger.warning(f"[ESI][RSC] Client error: {e}")
        return downloaded

    def try_download_esi_direct(self, doi: str, research_pdf_dir: Path) -> bool:
        """Try to download ESI directly via HTTP (no browser automation needed).

        Currently supports RSC (10.1039/) only: cycles {suffix}1.pdf … {suffix}5.pdf
        until 404. XML ESI files are saved with .xml extension and parsed by
        find_esi_files() in step04.

        Returns True if at least one ESI was successfully downloaded.
        """
        research_pdf_dir.mkdir(parents=True, exist_ok=True)
        if doi.startswith("10.1039/"):
            return self._try_rsc_esi(doi, research_pdf_dir)
        return False

    def try_download_esi_playwright(self, doi: str, research_pdf_dir: Path) -> bool:
        """Download ESI files using Playwright Chromium after resolving article page.
        
        Returns True if ESI was successfully downloaded.
        """
        # First try direct download (faster, no browser automation needed)
        if self.try_download_esi_direct(doi, research_pdf_dir):
            return True

        try:
            sync_api = importlib.import_module("playwright.sync_api")
            PlaywrightError = sync_api.Error
            sync_playwright = sync_api.sync_playwright
        except ModuleNotFoundError:
            self.logger.warning("[ESI] playwright not installed; skipping browser-based ESI download. "
                                "Run: python -m playwright install chromium")
            return False
        
        research_pdf_dir.mkdir(parents=True, exist_ok=True)
        
        # Determine article URL based on DOI prefix
        if doi.startswith("10.1002/"):
            article_url = f"https://onlinelibrary.wiley.com/doi/{doi}"
        elif doi.startswith("10.1021/"):
            article_url = f"https://pubs.acs.org/doi/{doi}"
        elif doi.startswith("10.1039/"):
            article_url = f"https://pubs.rsc.org/en/content/articlelanding/{doi}"
        else:
            self.logger.info(f"ESI download not supported for DOI prefix: {doi}")
            return False

        doi_safe = doi.replace("/", "_")
        esi_xpaths = [
            '//a[contains(@href, "/doi/suppl/") and contains(@href, "suppl_file")]',
            '//a[contains(@href, "downloadSupplement")]',
            '//a[contains(@href, "suppdata")]',
            '//a[contains(@href, "supplement") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
            '//a[contains(@href, "Supplement") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
            '//a[contains(@href, "supporting") and not(contains(@href, "?goto=")) and contains(@href, ".pdf")]',
            '//a[contains(text(), "Supporting Information") and not(contains(@href, "?goto="))]',
            '//a[contains(text(), "Supplementary") and not(contains(@href, "?goto="))]',
            '//section[@id="support-info"]//a[contains(@href, ".pdf")]',
        ]

        def _detect_extension(candidate_url: str, content_type: str, content: bytes) -> str:
            lower_url = candidate_url.lower()
            lower_type = content_type.lower()
            if content[:4] == b"%PDF":
                return ".pdf"
            if content[:5] in (b"<?xml", b"<art ") or b"<article" in content[:200]:
                return ".xml"
            for ext in (".docx", ".doc", ".xlsx", ".zip"):
                if lower_url.endswith(ext):
                    return ext
            if "wordprocessingml" in lower_type:
                return ".docx"
            if "msword" in lower_type:
                return ".doc"
            if "spreadsheetml" in lower_type or "excel" in lower_type:
                return ".xlsx"
            if "zip" in lower_type:
                return ".zip"
            return ".pdf"

        try:
            with sync_playwright() as playwright:
                for _headless in (True, False):
                    launch_args = ["--disable-blink-features=AutomationControlled"]
                    if not _headless:
                        launch_args.append("--start-maximized")
                    try:
                        browser = playwright.chromium.launch(
                            headless=_headless,
                            args=launch_args,
                        )
                        break
                    except Exception:
                        continue
                else:
                    return False
                context = browser.new_context(
                    accept_downloads=True,
                    no_viewport=True,
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                )
                page = context.new_page()
                self.logger.info(f"[ESI] Visiting {article_url}...")
                page.goto(article_url, wait_until="domcontentloaded", timeout=60000)

                max_wait_ms = 30000 if doi.startswith("10.1002/") else 15000
                page.wait_for_timeout(max_wait_ms)
                page.wait_for_timeout(3000)

                self.logger.info(f"[ESI] Page loaded: {page.title()[:60]}")

                for xpath in esi_xpaths:
                    locator = page.locator(f'xpath={xpath}')
                    count = locator.count()
                    if count == 0:
                        continue
                    self.logger.info(f"[ESI] Found {count} link(s) with pattern: {xpath[:50]}...")
                    for index in range(count):
                        link = locator.nth(index)
                        href = link.get_attribute('href') or ''
                        esi_url = urljoin(page.url, href) if href else None
                        self.logger.info(
                            f"[ESI] Attempting download from: {(esi_url or 'click-only link')[:80]}..."
                        )

                        try:
                            link.scroll_into_view_if_needed(timeout=5000)
                            with page.expect_download(timeout=30000) as download_info:
                                link.click(force=True, timeout=10000)
                            download = download_info.value
                            suggested_name = download.suggested_filename or (
                                Path(esi_url).name if esi_url else f"{doi_safe}_ESI.pdf"
                            )
                            extension = Path(suggested_name).suffix.lower() or ".pdf"
                            output_path = research_pdf_dir / f"{doi_safe}_ESI{extension}"
                            if output_path.exists():
                                self.logger.info(f"[ESI] Already exists: {output_path.name}")
                                return True

                            download.save_as(str(output_path))
                            self.logger.info(f"[ESI] Downloaded via browser: {output_path.name}")
                            return True
                        except PlaywrightError as download_error:
                            self.logger.warning(
                                f"[ESI] Browser download failed for {doi} ({xpath[:40]}): {download_error}"
                            )

                        if not esi_url:
                            continue

                        response = context.request.get(
                            esi_url,
                            fail_on_status_code=False,
                            timeout=60000,
                            headers={
                                "referer": page.url,
                                "user-agent": page.evaluate("() => navigator.userAgent"),
                            },
                        )
                        if not response.ok:
                            self.logger.warning(
                                f"[ESI] Download request failed for {doi}: {response.status}"
                            )
                            continue

                        content = response.body()
                        if len(content) < 1000:
                            self.logger.warning(f"[ESI] Downloaded content too small for {doi}")
                            continue

                        extension = _detect_extension(
                            esi_url,
                            response.headers.get('content-type', ''),
                            content,
                        )
                        output_path = research_pdf_dir / f"{doi_safe}_ESI{extension}"
                        if output_path.exists():
                            self.logger.info(f"[ESI] Already exists: {output_path.name}")
                            return True

                        output_path.write_bytes(content)
                        self.logger.info(f"[ESI] Downloaded via request fallback: {output_path.name}")
                        return True

                self.logger.info(f"[ESI] No ESI links found for {doi}")
                return False

        except PlaywrightError as e:
            self.logger.error(f"[ESI] Error downloading ESI for {doi}: {e}")
            self.logger.error("[ESI] Ensure Playwright and Chromium are installed: python -m playwright install chromium")
            return False

    def run_esi_fallback(self, compound: str, compound_dir: Path) -> bool:
        """Try to download ESI for every paper with alternatives but incomplete extraction.

        ESI is attempted for ALL papers where material dosage is still missing,
        regardless of whether other papers already have complete extraction.

        Returns True if any ESI was downloaded and step04 was re-run.
        """
        papers = self.get_insufficient_papers_needing_esi(compound_dir)
        if not papers:
            self.logger.info("[ESI Fallback] No papers need ESI download.")
            return False

        self.logger.info(f"[ESI Fallback] Attempting ESI for {len(papers)} paper(s) with incomplete extraction:")
        for p in papers:
            reason = p.get('reason', 'unknown')
            self.logger.info(f"  - {p['doi']}: {p['title']} [{reason}]")
        
        research_pdf_dir = compound_dir / "research_pdf"
        downloaded_dois: list[str] = []

        for paper in papers:
            doi = paper["doi"]
            self.logger.info(f"[ESI Fallback] Attempting ESI download for {doi}...")
            if self.try_download_esi_playwright(doi, research_pdf_dir):
                downloaded_dois.append(doi)

        if downloaded_dois:
            self.logger.info(
                f"[ESI Fallback] Re-running step04 for {len(downloaded_dois)} paper(s) with new ESI content..."
            )
            # Invalidate only the entries that got new ESI — other papers keep their cache
            self._invalidate_step04_dois(compound_dir, downloaded_dois)
            return self.run_step04(compound, compound_dir, write_final=True)

        return False

    def _invalidate_step04_dois(self, compound_dir: Path, dois: list[str]) -> None:
        """Remove specific DOI entries from the step04 results file so step04 re-processes
        only those papers while keeping the cache for all other entries intact."""
        step04_file = compound_dir / self.get_step04_filename()
        if not step04_file.exists():
            return
        try:
            with open(step04_file, encoding="utf-8") as f:
                records = json.load(f)
            dois_set = {d.lower().strip() for d in dois}
            filtered = [
                r for r in records
                if str(r.get("externalIds", {}).get("DOI", "") or r.get("doi", "")).lower().strip()
                not in dois_set
            ]
            removed = len(records) - len(filtered)
            if removed > 0:
                with open(step04_file, "w", encoding="utf-8") as f:
                    json.dump(filtered, f, ensure_ascii=False, indent=2)
                self.logger.info(
                    f"[ESI] Invalidated {removed} step04 cache entry/entries for re-extraction; "
                    f"{len(filtered)} entries kept from cache."
                )
        except Exception as e:
            self.logger.error(f"[ESI] Error invalidating step04 cache: {e}; falling back to full re-run")
            # Fallback: delete the whole file so step04 re-processes everything
            step04_file.unlink(missing_ok=True)

    def extract_phase_c_seeds(self, compound_dir: Path) -> list[dict[str, str]]:
        """Extract (alternative, application_context) pairs from step04 results
        where alternatives were found but dosage could not be extracted.

        Returns a deduplicated list of dicts with keys:
            alternative, target_problem, relationship_type
        """
        step04_file = compound_dir / self.get_step04_filename()
        seeds: dict[str, dict[str, str]] = {}
        try:
            with open(step04_file, encoding="utf-8") as f:
                results = json.load(f)
            for r in results:
                if (
                    r.get("alternatives provided", "").lower() == "yes"
                    and r.get("dosage_info", {}).get("status") in ("insufficient_data", "not_found")
                ):
                    sub_logic = r.get("dosage_info", {}).get("substitution_logic") or {}
                    target_problem: str = sub_logic.get("target_problem", "")
                    relationship_type: str = sub_logic.get("relationship_type", "")
                    for alt in r.get("alternatives", []):
                        if alt and alt not in seeds:
                            seeds[alt] = {
                                "alternative": alt,
                                "target_problem": target_problem,
                                "relationship_type": relationship_type,
                            }
        except Exception as e:
            self.logger.error(f"Error extracting Phase C seeds: {e}")
        return list(seeds.values())

    def _extract_context_keywords(
        self, target_problem: str, target: str, alternative: str
    ) -> str:
        """Extract short application-context keywords from a target_problem string."""
        import re
        text = target_problem
        # Remove mentions of target and alternative compounds (case-insensitive)
        for name in [target, alternative]:
            text = re.sub(re.escape(name), "", text, flags=re.IGNORECASE)
        # Strip leading hazard/substitution verbiage
        strip_phrases = [
            "reliance on", "use of", "toxic", "hazardous", "dangerous",
            "replace", "substitut", "instead of", "in the production of",
            "high environmental impact", "difficulty in",
        ]
        for phrase in strip_phrases:
            text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
        stop_words = {
            "a", "an", "the", "in", "of", "for", "to", "and", "or", "with",
            "on", "at", "by", "from", "as", "is", "are", "was", "were",
            "its", "their", "this", "that", "such", "like",
        }
        words = [
            w.strip(".,;:()[]")
            for w in text.split()
            if len(w.strip(".,;:()[]")) > 2
            and w.strip(".,;:()[]").lower() not in stop_words
        ]
        return " ".join(words[:4])

    def generate_phase_c_queries(
        self, seeds: list[dict[str, str]], compound: str
    ) -> list[str]:
        """Build Semantic Scholar search queries from Phase C seeds.

        For each seed, produces:
        - Primary query: alternative name alone
        - Secondary query: alternative + extracted application context keywords
        """
        queries: list[str] = []
        seen: set[str] = set()
        for seed in seeds:
            alt = seed["alternative"]
            # Primary
            if alt not in seen:
                queries.append(alt)
                seen.add(alt)
            # Secondary: add application context
            ctx = self._extract_context_keywords(
                seed.get("target_problem", ""), compound, alt
            )
            if ctx:
                combined = f"{alt} {ctx}"
                if combined not in seen:
                    queries.append(combined)
                    seen.add(combined)
            # Tertiary: explicit substitution query to surface papers that
            # directly compare the alternative against the original compound.
            sub_query = f"{alt} replace {compound}"
            if sub_query not in seen:
                queries.append(sub_query)
                seen.add(sub_query)
            sub_query2 = f"{alt} alternative {compound}"
            if sub_query2 not in seen:
                queries.append(sub_query2)
                seen.add(sub_query2)
        return queries

    def create_phase_c_step03_shim(
        self,
        compound: str,
        seeds: list[dict[str, str]],
        step02_results_path: Path,
        output_path: Path,
    ) -> bool:
        """Create a synthetic step03-format JSON for Phase C papers.

        Because Phase C papers are about the alternative being *used* (not comparing
        it to the original compound), real step03 LLM analysis would miss them.
        This shim pre-seeds the alternative information so step04 can extract dosage.
        """
        try:
            with open(step02_results_path, encoding="utf-8") as f:
                step02_results = json.load(f)
        except Exception as e:
            self.logger.error(f"Could not load step02 results for Phase C shim: {e}")
            return False

        all_alternatives = [s["alternative"] for s in seeds]
        shim_records: list[dict] = []

        for paper in step02_results:
            abstract: str = paper.get("abstract", "") or ""
            # Prefer alternatives that are actually mentioned in the abstract
            mentioned = [a for a in all_alternatives if a.lower() in abstract.lower()]
            alts_to_use = mentioned if mentioned else all_alternatives

            # Normalise DOI: top-level field may be None when Semantic Scholar stores
            # it only inside externalIds (common for older papers).
            doi: str = paper.get("doi") or (paper.get("externalIds") or {}).get("DOI", "") or ""

            shim_records.append({
                "title": paper.get("title", ""),
                "doi": doi,
                "year": paper.get("year"),
                "abstract": abstract,
                "target": compound,
                "alternatives provided": "yes",
                "alternatives": alts_to_use,
                "reasoning": (
                    f"Phase C shim: paper retrieved by searching for "
                    f"[{', '.join(alts_to_use)}] in application context. "
                    "Dosage extraction attempted directly."
                ),
                "model_used": "phase_c_shim",
                "download_status": paper.get("download_status", ""),
                "fulltext_source": paper.get("fulltext_source", "none"),
            })

        if not shim_records:
            self.logger.warning("Phase C shim produced zero records.")
            return False

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(shim_records, f, ensure_ascii=False, indent=2)
        self.logger.info(f"Phase C shim: wrote {len(shim_records)} records to {output_path.name}")
        return True

    def merge_phase_c_results(
        self, compound_dir: Path, phaseC_dir: Path, compound: str
    ) -> bool:
        """Merge Phase C step04 results into the main step04 results and summary.

        Returns True if at least one dosage record was merged.
        """
        phaseC_step04 = phaseC_dir / self.get_step04_filename()
        main_step04 = compound_dir / self.get_step04_filename()
        main_summary = compound_dir / self.get_step04_summary_filename()

        if not phaseC_step04.exists():
            self.logger.warning("Phase C step04 results not found; nothing to merge.")
            return False

        try:
            with open(phaseC_step04, encoding="utf-8") as f:
                phaseC_results: list[dict] = json.load(f)
        except Exception as e:
            self.logger.error(f"Cannot read Phase C step04 results: {e}")
            return False

        # Only keep records that actually have dosage data
        useful = [
            r for r in phaseC_results
            if r.get("dosage_info", {}).get("status") in ("extracted", "partial_data")
        ]
        if not useful:
            self.logger.info("Phase C found no dosage data; nothing to merge.")
            return False

        # Relevance filter: the substitution logic must show the target compound
        # (or a close variant) was actually what's being replaced.  This guards
        # against LLM hallucinating dosages from off-topic content in the paper.
        compound_lower = compound.lower()
        def _is_relevant(r: dict) -> bool:
            # substitution_logic may be at the top level or nested inside dosage_info
            sub = (
                r.get("substitution_logic")
                or r.get("dosage_info", {}).get("substitution_logic")
                or {}
            )
            replaced = (sub.get("traditional_material_replaced") or "").lower()
            target_prob = (sub.get("target_problem") or "").lower()
            if compound_lower in replaced or compound_lower in target_prob:
                return True

            # Phase C shim papers were retrieved by searching specifically for
            # alternatives to the target compound.  The LLM may phrase
            # traditional_material_replaced differently (e.g. the immediate
            # predecessor reagent rather than the target compound name), so trust
            # Phase C shim records — but only if the dosed material actually
            # matches the alternative name (guards against photoinitiators, solvents, etc.)
            reasoning = (
                r.get("reasoning")
                or r.get("dosage_info", {}).get("reasoning")
                or ""
            ).lower()
            if "phase c shim" in reasoning:
                alts = (
                    r.get("alternatives")
                    or r.get("dosage_info", {}).get("alternatives")
                    or []
                )
                if alts:
                    alts_lower = [a.lower() for a in alts]
                    explicit = r.get("dosage_info", {}).get("explicit_dosages") or []
                    if explicit:
                        # At least one dosed material must contain an alternative name
                        return any(
                            any(alt in (d.get("material") or "").lower() for alt in alts_lower)
                            for d in explicit
                        )
                    return True  # No dosage yet — still a Phase C candidate

            return False

        relevant = [r for r in useful if _is_relevant(r)]
        skipped = len(useful) - len(relevant)
        if skipped:
            self.logger.info(
                f"Phase C relevance filter: dropped {skipped} record(s) whose "
                f"substitution_logic does not reference '{compound}'."
            )
        useful = relevant
        if not useful:
            self.logger.info("Phase C found no relevant dosage data after filtering; nothing to merge.")
            return False

        # Tag each record
        for r in useful:
            r["phase"] = "C"

        # Load and extend main step04 results
        main_results: list[dict] = []
        if main_step04.exists():
            try:
                with open(main_step04, encoding="utf-8") as f:
                    main_results = json.load(f)
            except Exception as e:
                self.logger.warning(f"Could not load main step04 results: {e}")

        # Status priority for upgrade: extracted > partial_data > insufficient_data
        _STATUS_RANK = {"extracted": 3, "partial_data": 2, "insufficient_data": 1, "not_found": 0}

        # Deduplicate by DOI — upgrade existing record if Phase C has a better status
        doi_to_idx: dict[str, int] = {
            r.get("doi"): i for i, r in enumerate(main_results) if r.get("doi")
        }
        new_records: list[dict] = []
        upgraded = 0
        for r in useful:
            doi = r.get("doi")
            if doi and doi in doi_to_idx:
                existing = main_results[doi_to_idx[doi]]
                existing_status = existing.get("dosage_info", {}).get("status", "not_found")
                new_status = r.get("dosage_info", {}).get("status", "not_found")
                if _STATUS_RANK.get(new_status, 0) > _STATUS_RANK.get(existing_status, 0):
                    main_results[doi_to_idx[doi]] = r  # replace in-place
                    upgraded += 1
                    self.logger.info(
                        f"Upgraded record '{doi}' from {existing_status} → {new_status}"
                    )
                else:
                    self.logger.info(
                        f"Skipped duplicate '{doi}' (existing={existing_status} >= new={new_status})"
                    )
            else:
                new_records.append(r)

        if not new_records and upgraded == 0:
            self.logger.info("All Phase C dosage records are duplicates with no upgrade; nothing to add.")
            return False

        merged = main_results + new_records
        with open(main_step04, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        self.logger.info(
            f"Merged {len(new_records)} new + {upgraded} upgraded Phase C record(s) into {main_step04.name}"
        )

        # Regenerate summary from scratch using merged results
        try:
            status_counts: dict[str, int] = {}
            records_with_dosage = []
            for r in merged:
                di = r.get("dosage_info", {})
                status = di.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
                if status in ("extracted", "partial_data"):
                    records_with_dosage.append({
                        "title": (r.get("title") or r.get("Article title") or "")[:80],
                        "doi": r.get("doi"),
                        "alternative": r.get("alternatives"),
                        "reasoning": r.get("reasoning"),
                        "phase": r.get("phase"),
                        "dosage_status": status,
                        "fulltext_source": r.get("fulltext_source") or di.get("fulltext_source"),
                        "extraction_method": di.get("extraction_method"),
                        "download_strategy": di.get("download_strategy"),
                        "substitution_logic": di.get("substitution_logic"),
                        "explicit_dosages": di.get("explicit_dosages"),
                        "synthesis_conditions": di.get("synthesis_conditions"),
                        "material_properties": di.get("material_properties"),
                        "performance_metrics": di.get("performance_metrics"),
                        "partial_data": di.get("partial_data"),
                        "confidence": di.get("confidence"),
                    })

            new_summary = {
                "target": compound,
                "total_records": len(merged),
                "statistics": status_counts,
                "records_with_dosage": records_with_dosage,
            }
            with open(main_summary, "w", encoding="utf-8") as f:
                json.dump(new_summary, f, ensure_ascii=False, indent=2)
            self.logger.info("Summary regenerated from merged results.")
        except Exception as e:
            self.logger.warning(f"Could not regenerate summary: {e}")

        # Write merged results to final_output if CID is available
        cid_val = self.cid_map.get(compound)
        if cid_val:
            from datetime import datetime
            final_output_dir = SCRIPT_DIR / "final_output"
            final_output_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now().strftime("%Y%m%d")
            final_file = final_output_dir / f"{date_str}_{cid_val}.json"
            drop_empty = CONFIG.get("default_settings", {}).get("step04_drop_empty", False)
            final_records = (
                [r for r in merged if r.get("dosage_info", {}).get("status") in ("extracted", "partial_data")]
                if drop_empty else merged
            )
            with open(final_file, "w", encoding="utf-8") as f:
                json.dump(final_records, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Final output updated: {final_file}")

        return True

    def run_phase_c(self, compound: str, compound_dir: Path) -> bool:
        """Orchestrate Phase C: search using known alternatives + application context.

        Flow:
            1. Extract (alternative, context) seeds from step04 insufficient_data records
            2. Write targeted queries to phaseC/step00_queries.json
            3. step01 → step02 (in phaseC/ subdirectory)
            4. Create step03 shim (no LLM needed — alternatives pre-seeded)
            5. step04 (write_final=False)
            6. Merge dosage results back into main step04 output
        """
        self.logger.info(f"[Phase C] Starting for {compound}")

        # 1. Seeds
        seeds = self.extract_phase_c_seeds(compound_dir)
        if not seeds:
            self.logger.warning("[Phase C] No seeds found; aborting.")
            return False
        self.logger.info(
            f"[Phase C] Seeds: {[s['alternative'] for s in seeds]}"
        )

        # 2. Queries → phaseC/step00_queries.json
        phaseC_dir = compound_dir / "phaseC"
        phaseC_dir.mkdir(parents=True, exist_ok=True)
        queries = self.generate_phase_c_queries(seeds, compound)
        self.logger.info(f"[Phase C] Search queries: {queries}")
        with open(phaseC_dir / "step00_queries.json", "w", encoding="utf-8") as f:
            json.dump({"compound": compound, "queries": queries}, f, indent=2, ensure_ascii=False)

        # 3. step01 + step02 in phaseC_dir
        if not self.run_step01(compound, phaseC_dir):
            self.logger.warning("[Phase C] step01 failed.")
            return False
        if not self.run_step02(compound, phaseC_dir):
            self.logger.warning("[Phase C] step02 failed.")
            return False

        # 4. step03 shim
        step03_shim_path = phaseC_dir / self.get_step03_filename()
        if not self.create_phase_c_step03_shim(
            compound, seeds, phaseC_dir / "step02_results.json", step03_shim_path
        ):
            self.logger.warning("[Phase C] step03 shim failed.")
            return False

        # 5. step04 (no final output — merging happens next)
        if not self.run_step04(compound, phaseC_dir, write_final=False):
            self.logger.warning("[Phase C] step04 failed.")
            return False

        # 6. Merge
        return self.merge_phase_c_results(compound_dir, phaseC_dir, compound)

    def run_pipeline_for_compound(self, compound: str, compound_progress: tqdm) -> Dict[str, bool]:
        """Run complete pipeline for a single compound with dynamic time range retry."""
        print(f"\n{'#'*80}")
        print(f"Starting pipeline for compound: {compound}")
        print(f"{'#'*80}")
        
        # Create compound directory
        compound_dir = self.create_compound_directory(compound)
        
        # Track step results
        step_results = {}

        # -------------------------------------------------------------
        # STEP 0: Generate AI-expanded search keywords (always, non-fatal)
        # step01 will union-merge compound name + these queries automatically
        # -------------------------------------------------------------
        print(f"\n{'='*60}")
        print(f"STEP 0: Generating AI search keywords for {compound}")
        print(f"{'='*60}")
        step_results["step00"] = self.run_step00(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 0 (AI keywords) completed")

        # -------------------------------------------------------------
        # UNIFIED SEARCH: compound name + AI keywords → single pool
        # -------------------------------------------------------------
        print(f"\n{'='*60}")
        print(f"UNIFIED SEARCH (Step 1–4) for {compound}")
        print(f"{'='*60}")
        
        step_results["step01"] = self.run_step01(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 1 (Standard) completed")
        
        if step_results["step01"]:
            step_results["step02"] = self.run_step02(compound, compound_dir)
            compound_progress.set_description(f"{compound} - Step 2 completed")
            
            if step_results["step02"]:
                # Use recursive search for step03
                default_years_back = CONFIG.get("default_settings", {}).get("years_back", 10)
                step_results["step03"] = self.recursive_step03_search(compound, compound_dir, default_years_back)
                compound_progress.set_description(f"{compound} - Step 3 completed")
                
                if step_results["step03"]:
                    step_results["step04"] = self.run_step04(compound, compound_dir)
                    compound_progress.set_description(f"{compound} - Step 4 completed")
        
        # Check if unified search found alternatives
        found_alternatives = step_results.get("step03", False)
        if found_alternatives:
            step04_success = self.check_step04_has_data(compound_dir)
            if not step04_success:
                self.logger.warning(f"Step 03 found alternatives, but Step 04 found no useful safety/harm data for {compound}.")
                found_alternatives = False
        
        # -------------------------------------------------------------
        # ESI FALLBACK: Try downloading ESI for papers with incomplete extraction
        # -------------------------------------------------------------
        papers_needing_esi = self.get_insufficient_papers_needing_esi(compound_dir)
        if papers_needing_esi:
            self.logger.info(
                f"ESI Fallback: {len(papers_needing_esi)} paper(s) with incomplete extraction. "
                f"Attempting ESI fallback for {compound}..."
            )
            print(f"\n{'='*60}")
            print(f"ESI FALLBACK: Attempting to download ESI for {compound}")
            print(f"{'='*60}")
            esi_success = self.run_esi_fallback(compound, compound_dir)
            step_results["esi_fallback"] = esi_success
            if esi_success:
                if self.has_any_complete_extraction(compound_dir):
                    found_alternatives = True
                    self.logger.info(f"ESI fallback succeeded - complete extraction for {compound}")
            compound_progress.set_description(f"{compound} - ESI Fallback completed")

        # -------------------------------------------------------------
        # PHASE C: Alternative + Context Fallback Search
        # (Triggered when alternatives exist but no dosage was extracted)
        # -------------------------------------------------------------
        if not found_alternatives and self.check_step04_needs_phase_c(compound_dir):
            self.logger.info(
                f"Alternatives found but no dosage in unified search. "
                f"Triggering Phase C (alternative+context search) for {compound}."
            )
            print(f"\n{'='*60}")
            print(f"PHASE C: Alternative+Context Fallback Search for {compound}")
            print(f"{'='*60}")
            phase_c_success = self.run_phase_c(compound, compound_dir)
            step_results["phase_c"] = phase_c_success
            if phase_c_success:
                found_alternatives = True
            compound_progress.set_description(f"{compound} - Phase C completed")

        if not found_alternatives:
            self.logger.warning(f"All search phases failed to find valid safety context for {compound}, skipping remaining steps")
            compound_progress.update(1)
            return step_results
        
        compound_progress.update(1)
        self.logger.info(f"Pipeline completed for {compound}")
        return step_results
    
    def run_full_pipeline(self) -> dict[str, dict[str, bool]]:
        """Run pipeline for all compounds."""
        print(f"\n{'*'*80}")
        print("STARTING FULL PIPELINE FOR ALL COMPOUNDS")
        print(f"Total compounds: {len(self.compounds)}")
        # Indicate config source for clarity (.env preferred)
        env_path = Path(".env")
        api_json = Path("api_config.json")
        if env_path.exists():
            cfg_src = ".env"
        elif api_json.exists():
            cfg_src = "api_config.json"
        else:
            cfg_src = "environment variables"
        print(f"Using configuration from: {cfg_src}")
        print(f"{'*'*80}")
        
        # Validate API keys
        missing_keys = []
        if not CONFIG.get("semantic_scholar_api_key"):
            missing_keys.append("semantic_scholar_api_key")
        if not CONFIG.get("elsevier_api_key"):
            missing_keys.append("elsevier_api_key")
        if not CONFIG.get("openai_api_key"):
            missing_keys.append("openai_api_key")
        
        if missing_keys:
            print(f"[ERROR] Missing API keys in config file: {', '.join(missing_keys)}")
            return {}
        
        # Create base output directory
        self.output_base_dir.mkdir(parents=True, exist_ok=True)
        
        all_results = {}
        
        # Create overall progress bar for compounds
        with tqdm(total=len(self.compounds), desc="Overall Progress", 
                 position=0, leave=True, colour='green') as overall_pbar:
            
            for i, compound in enumerate(self.compounds, 1):
                overall_pbar.set_description(f"Processing {compound} ({i}/{len(self.compounds)})")
                all_results[compound] = self.run_pipeline_for_compound(compound, overall_pbar)
        
        # Save summary results
        self.save_pipeline_summary(all_results)
        
        return all_results
    
    def save_pipeline_summary(self, results: Dict[str, Dict[str, bool]]):
        """Save pipeline execution summary."""
        summary_file = self.output_base_dir / "pipeline_summary.json"
        
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        # Create summary table
        summary_data = []
        for compound, steps in results.items():
            row = {"compound": compound}
            row.update(steps)
            row["all_steps_success"] = all(steps.values()) if steps else False
            summary_data.append(row)
        
        df_summary = pd.DataFrame(summary_data)
        summary_csv = self.output_base_dir / "pipeline_summary.csv"
        df_summary.to_csv(summary_csv, index=False)
        
        print(f"\nPipeline summary saved to:")
        print(f"  JSON: {summary_file}")
        print(f"  CSV: {summary_csv}")

def main():
    """Main execution function."""
    print("="*80)
    print("MULTI-COMPOUND PIPELINE CONTROLLER")
    print("="*80)
    
    controller = PipelineController(INPUT_CSV, OUTPUT_BASE_DIR)
    results = controller.run_full_pipeline()
    
    if not results:
        print("Pipeline execution failed due to missing API keys.")
        return
    
    # Print final summary
    print(f"\n{'='*80}")
    print("FINAL EXECUTION SUMMARY")
    print(f"{'='*80}")
    
    total_compounds = len(results)
    successful_compounds = 0
    
    for compound, steps in results.items():
        if steps:  # 確保 steps 不為空
            success_count = sum(steps.values())
            total_steps = len(steps)
            all_success = success_count == total_steps
            if all_success:
                successful_compounds += 1
            
            status = " COMPLETE" if all_success else "  PARTIAL"
            print(f"{compound:20} | {success_count:2d}/{total_steps} steps | {status}")
        else:
            print(f"{compound:20} | 0/4 steps |  FAILED")
    
    print(f"{'='*80}")
    print(f"Successfully completed: {successful_compounds}/{total_compounds} compounds")
    print(f"Check pipeline.log for detailed execution logs")
    print(f"Results saved in: {OUTPUT_BASE_DIR}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
