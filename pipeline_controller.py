import pandas as pd
import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
import subprocess
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

# Configuration
INPUT_CSV = "12_chemical_test.csv"  # 包含 26筆化合物資料的 CSV 檔
OUTPUT_BASE_DIR = Path("outputs")
COMPOUND_COLUMN = "name"  # CSV 中化合物名稱的欄位名

# Pipeline steps configuration 
STEP  = {
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
        self.compounds = self.load_compounds()
        self.setup_logging()
        
    def setup_logging(self):
        """Setup logging configuration."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('pipeline.log'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def load_compounds(self) -> List[str]:
        """Load compound names from CSV file."""
        try:
            df = pd.read_csv(self.input_csv)
            compounds = df[COMPOUND_COLUMN].tolist()
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
    
    def run_step01(self, compound: str, compound_dir: Path) -> bool:
        """Run step 01: Fetch papers for a compound."""
        print(f"\n{'='*60}")
        print(f"Step 01: Fetching papers for {compound}")
        print(f"{'='*60}")
        
        command_args = [
            sys.executable, "step01.py",
            "--keyword", compound,
            "--output_dir", str(compound_dir),
            "--output_file", "step01_results.json"
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
            sys.executable, "step02.py",
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / "step02_results.json")
        ]
        # Optional parallel workers from config
        step02_workers = CONFIG.get("default_settings", {}).get("step02_workers")
        if isinstance(step02_workers, int) and step02_workers > 0:
            command_args += ["--workers", str(step02_workers)]
        
        return self.run_step_with_progress("Step 02", command_args, "Fetching abstracts", compound)
    
    def run_step03(self, compound: str, compound_dir: Path) -> bool:
        """Run step 03: Reasoning analysis."""
        print(f"\n{'='*60}")
        print(f"Step 03: Analyzing alternatives for {compound}")
        print(f"{'='*60}")
        
        input_file = compound_dir / "step02_results.json"
        if not input_file.exists():
            self.logger.error(f"Input file not found for step 03: {input_file}")
            return False
        
        command_args = [
            sys.executable, "step03.py",
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / "step03_results.json"),
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
        
        return self.run_step_with_progress("Step 03", command_args, "Analyzing alternatives", compound)
    
    def run_step04(self, compound: str, compound_dir: Path) -> bool:
        """Run step 04: Extract alternatives."""
        print(f"\n{'='*60}")
        print(f"Step 04: Extracting alternatives for {compound}")
        print(f"{'='*60}")
        
        input_file = compound_dir / "step03_results.json"
        if not input_file.exists():
            self.logger.error(f"Input file not found for step 04: {input_file}")
            return False
        
        command_args = [
            sys.executable, "step04.py",
            "--input_file", str(input_file),
            "--output_file", str(compound_dir / "step04_results.json"),
            "--target", compound
        ]
        
        return self.run_step_with_progress("Step 04", command_args, "Extracting alternatives", compound)
    
    def run_pipeline_for_compound(self, compound: str, compound_progress: tqdm) -> Dict[str, bool]:
        """Run complete pipeline for a single compound."""
        print(f"\n{'#'*80}")
        print(f"Starting pipeline for compound: {compound}")
        print(f"{'#'*80}")
        
        # Create compound directory
        compound_dir = self.create_compound_directory(compound)
        
        # Track step results
        step_results = {}
        
        # Run each step sequentially
        step_results["step01"] = self.run_step01(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 1 completed")
        if not step_results["step01"]:
            self.logger.warning(f"Step 01 failed for {compound}, skipping remaining steps")
            compound_progress.update(1)
            return step_results
        
        step_results["step02"] = self.run_step02(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 2 completed")
        if not step_results["step02"]:
            self.logger.warning(f"Step 02 failed for {compound}, skipping remaining steps")
            compound_progress.update(1)
            return step_results
        
        step_results["step03"] = self.run_step03(compound, compound_dir)
        compound_progress.set_description(f"{compound} - Step 3 completed")
        if not step_results["step03"]:
            self.logger.warning(f"Step 03 failed for {compound}, skipping remaining steps")
            compound_progress.update(1)
            return step_results
        
        step_results["step04"] = self.run_step04(compound, compound_dir)
        compound_progress.set_description(f"{compound} - All steps completed")
        
        compound_progress.update(1)
        self.logger.info(f"Pipeline completed for {compound}")
        return step_results
    
    def run_full_pipeline(self) -> Dict[str, Dict[str, bool]]:
        """Run pipeline for all compounds."""
        print(f"\n{'*'*80}")
        print("STARTING FULL PIPELINE FOR ALL COMPOUNDS")
        print(f"Total compounds: {len(self.compounds)}")
        print(f"Using API keys from: {CONFIG_FILE}")
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
            
            status = "✅ COMPLETE" if all_success else "⚠️  PARTIAL"
            print(f"{compound:20} | {success_count:2d}/{total_steps} steps | {status}")
        else:
            print(f"{compound:20} | 0/4 steps | ❌ FAILED")
    
    print(f"{'='*80}")
    print(f"Successfully completed: {successful_compounds}/{total_compounds} compounds")
    print(f"Check pipeline.log for detailed execution logs")
    print(f"Results saved in: {OUTPUT_BASE_DIR}")
    print(f"{'='*80}")

if __name__ == "__main__":
    main()
