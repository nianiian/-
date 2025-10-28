import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

import pandas as pd
from tqdm import tqdm

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

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

# Default Configuration
@dataclass
class Config:
    input_file: Path = None
    output_file: Path = None
    openai_api_key: str = CONFIG.get("openai_api_key", "")
    model: str = CONFIG.get("default_settings", {}).get("openai_model", "gpt-4.1-mini")
    models: List[str] = field(default_factory=list)
    temperature: float = 0.0
    max_tokens: int = 1000
    max_retries: int = 3
    target: str = None
    workers: int = 8

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

Task: Determine whether the abstract explicitly presents a **safer alternative** than **{target}**.

Decision rules (follow strictly):
1) Answer **yes** only if the abstract clearly indicates some substance/material/method is **safer than {target}**, or it clearly states safety advantages (e.g., lower toxicity, lower environmental risk/persistence/bioaccumulation/endocrine disruption, better occupational safety) that can reasonably be interpreted as a safer **alternative to {target}**.
2) If it merely discusses improvements without a clear safety comparison, different use-cases, or lacks a connection to replacing {target}, answer **no**.
3) Do not infer or speculate beyond what the abstract states.
4) If the abstract does not name {target} but clearly claims a safer replacement relative to the typical/conventional {target} context, you may answer **yes**; otherwise **no**.

Output format (must be valid JSON; no extra text):
{{
  "reasoning": "<1â€"3 sentences with evidence-based explanation for yes/no. If yes, mention the replacement name.>",
  "alternatives provided": "<'yes' or 'no'>"
}}

Paper info:
- Title: {title}
- DOI: {doi or 'N/A'}

ABSTRACT:
{abstract}
""".strip()

class SaferAlternativeAnalyzer:
    """Analyzer for safer alternatives using OpenAI API."""
    
    def __init__(self, cfg, logger):
        self.cfg = cfg
        self.logger = logger
        if OpenAI is None:
            raise RuntimeError("OpenAI Python SDK unavailable.")
        if not self.cfg.openai_api_key:
            raise RuntimeError("OpenAI API key not found in config file!")
        self.client = OpenAI(api_key=self.cfg.openai_api_key)
        # Resolve model list (fallback to single model)
        self.models = self.cfg.models if self.cfg.models else [self.cfg.model]
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

    def _call_one_model(self, prompt: str, model: str) -> Dict[str, Any]:
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
                return {"model": model, "reasoning": reasoning, "alternatives provided": alt_provided}
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
        # Choose reasoning from a model that matched the final vote, else any
        chosen_reason = ""
        for v in votes:
            if v.get("alternatives provided") == final_alt and v.get("reasoning"):
                chosen_reason = v["reasoning"]
                break
        if not chosen_reason and votes:
            chosen_reason = votes[0].get("reasoning", "")

        result: Dict[str, Any] = {
            "title": title,
            "doi": doi,
            "year": year,
            "abstract": abstract,
            "target": self.cfg.target,
            "alternatives provided": final_alt,
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
                "model_used": model,
            }
        prompt = build_prompt(title, doi, abstract, self.cfg.target)
        res = self._call_one_model(prompt, model)
        return {
            "title": title,
            "doi": doi,
            "year": year,
            "abstract": abstract,
            "target": self.cfg.target,
            "alternatives provided": res.get("alternatives provided", "no"),
            "reasoning": res.get("reasoning", ""),
            "model_used": model,
        }

    def run(self, records):
        """Run analysis on all records in parallel using distribute mode.

        Records are assigned to models in round-robin (one model per record).
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        total = len(records)
        results: List[Optional[Dict[str, Any]]] = [None] * total

        def task_distribute(idx_record):
            idx, rec = idx_record
            # Round-robin choose model for this record
            model = self.models[idx % len(self.models)] if self.models else self.cfg.model
            return idx, self.analyze_one_with_model(rec, model)

        with ThreadPoolExecutor(max_workers=max(1, int(self.cfg.workers))) as ex:
            futures = {ex.submit(task_distribute, (i, rec)): i for i, rec in enumerate(records)}
            for fut in tqdm(as_completed(futures), total=total, desc=f"Analyzing abstracts for {self.cfg.target}"):
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
                        "reasoning": f"Analysis failed: {e}",
                        "errors": {"record": str(e)},
                    }

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
    parser.add_argument("--max_retries", type=int, default=3, help="Maximum retry attempts")
    parser.add_argument("--workers", type=int, default=8, help="Thread workers for parallelism")
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
    )

    logger.info(f"Target: {cfg.target}")
    logger.info(f"Input: {cfg.input_file}")
    logger.info(f"Output: {cfg.output_file}")
    logger.info(f"Using OpenAI API key from config file")
    if cfg.models:
        logger.info(f"Models: {', '.join(cfg.models)} | mode=distribute")
    else:
        logger.info(f"Model: {cfg.model} | mode=distribute")

    # Load and process records
    records = load_input_json(cfg.input_file)
    analyzer = SaferAlternativeAnalyzer(cfg, logger)
    results = analyzer.run(records)
    
    # Save outputs - only main JSON file
    save_outputs(results, cfg.output_file)

    # Print summary
    total_records = len(results)
    alternatives_found = sum(1 for r in results if r.get("alternatives provided") == "yes")

    logger.info(f"Analysis completed for {cfg.target}")
    logger.info(f"Total records processed: {total_records}")
    logger.info(f"Records with alternatives: {alternatives_found} ({alternatives_found/total_records*100:.1f}%)")
    logger.info(f"Results saved to: {cfg.output_file}")

    # Save token usage summary next to output
    try:
        usage_path = cfg.output_file.with_name("step03_token_usage.json")
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
