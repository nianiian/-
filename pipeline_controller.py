import pandas as pd
import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
import subprocess
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
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(SCRIPT_DIR / 'pipeline.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)

    @property
    def current_model_name(self) -> str:
        # Get the model string, handle commas if multiple models are specified
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
            # Now we just check if any safety/harm analysis was successfully extracted
            useful_data_count = stats.get("harm_extracted", 0)
            
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
            backup_step03_file = compound_dir / f"step03_results_backup_{current_years}y_{timestamp}.json"
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
    
    def run_step04(self, compound: str, compound_dir: Path) -> bool:
        """Run step 04: Extract alternatives."""
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
        # If CID available, pass through along with final output directory
        cid_val = self.cid_map.get(compound)
        if cid_val:
            command_args += ["--cid", str(cid_val), "--final_dir", str(SCRIPT_DIR / "final_output")]
        
        return self.run_step_with_progress("Step 04", command_args, "Extracting alternatives", compound)
    
    def run_pipeline_for_compound(self, compound: str, compound_progress: tqdm) -> Dict[str, bool]:
        """Run complete pipeline for a single compound with dynamic time range retry."""
        print(f"\n{'#'*80}")
        print(f"Starting pipeline for compound: {compound}")
        print(f"{'#'*80}")
        
        # Create compound directory
        compound_dir = self.create_compound_directory(compound)
        
        # Remove old AI queries if they exist so standard search uses only the compound name
        step00_file = compound_dir / "step00_queries.json"
        if step00_file.exists():
            try:
                step00_file.unlink()
            except Exception as e:
                self.logger.warning(f"Failed to delete old step00_queries.json: {e}")
        
        # Track step results
        step_results = {}
        
        # -------------------------------------------------------------
        # PHASE A: Standard Search (Using exact compound name)
        # -------------------------------------------------------------
        print(f"\n{'='*60}")
        print(f"PHASE A: Standard Search for {compound}")
        print(f"{'='*60}")
        
        step_results["step01"] = self.run_step01(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 1 (Standard) completed")
        
        if step_results["step01"]:
            step_results["step02"] = self.run_step02(compound, compound_dir)
            compound_progress.set_description(f"{compound} - Step 2 (Standard) completed")
            
            if step_results["step02"]:
                # Use recursive search for step03
                default_years_back = CONFIG.get("default_settings", {}).get("years_back", 10)
                step_results["step03"] = self.recursive_step03_search(compound, compound_dir, default_years_back)
                compound_progress.set_description(f"{compound} - Step 3 (Standard) completed")
                
                if step_results["step03"]:
                    step_results["step04"] = self.run_step04(compound, compound_dir)
                    compound_progress.set_description(f"{compound} - Step 4 (Standard) completed")
        
        # Check if Phase A was successful in finding alternatives
        found_alternatives = step_results.get("step03", False)
        if found_alternatives:
            step04_success = self.check_step04_has_data(compound_dir)
            if not step04_success:
                self.logger.warning(f"Step 03 found alternatives, but Step 04 found no useful safety/harm data for {compound}. Treating Phase A as failed.")
                found_alternatives = False
        
        # -------------------------------------------------------------
        # PHASE B: AI Agentic Search (If Phase A failed to find alternatives)
        # -------------------------------------------------------------
        if not found_alternatives:
            self.logger.info(f"Standard search yielded no alternatives for {compound}. Triggering AI Agentic Search (Phase B).")
            print(f"\n{'='*60}")
            print(f"PHASE B: AI Agentic Search for {compound}")
            print(f"{'='*60}")
            
            step_results["step00"] = self.run_step00(compound, compound_dir)
            compound_progress.set_description(f"{compound} - Step 0 (AI) completed")
            
            if step_results.get("step00", False):
                step_results["step01"] = self.run_step01(compound, compound_dir)
                compound_progress.set_description(f"{compound} - Step 1 (AI) completed")
                
                if step_results["step01"]:
                    step_results["step02"] = self.run_step02(compound, compound_dir)
                    compound_progress.set_description(f"{compound} - Step 2 (AI) completed")
                    
                    if step_results["step02"]:
                        default_years_back = CONFIG.get("default_settings", {}).get("years_back", 10)
                        step_results["step03"] = self.recursive_step03_search(compound, compound_dir, default_years_back)
                        compound_progress.set_description(f"{compound} - Step 3 (AI) completed")
                        
                        if step_results["step03"]:
                            step_results["step04"] = self.run_step04(compound, compound_dir)
                            compound_progress.set_description(f"{compound} - Step 4 (AI) completed")
            
            # Check success of Phase B
            found_alternatives = step_results.get("step03", False)
            if found_alternatives:
                step04_success = self.check_step04_has_data(compound_dir)
                if not step04_success:
                    self.logger.warning(f"Phase B Step 04 also found no useful safety/harm data for {compound}.")
                    found_alternatives = False

        if not found_alternatives:
            self.logger.warning(f"Both search phases failed to find valid safety context for {compound}, skipping remaining steps")
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
