import argparse
import json
from pathlib import Path
from typing import Any, Dict, List
from openai import OpenAI
from typing import Optional

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

# Default Configuration from config file
OPENAI_API_KEY = CONFIG.get("openai_api_key", "")
MODEL = CONFIG.get("default_settings", {}).get("openai_model", "gpt-4.1-mini")
JOIN_SEP = "; "

if not OPENAI_API_KEY:
    print("[ERROR] OpenAI API key not found in config file!")
    exit(1)

PROMPT_TEMPLATE = """You are a careful information extractor. Read the fields and extract chemical/material names in the REASONING that are explicitly presented as safer alternatives to the TARGET.

Rules:
- Only return names explicitly framed as safer/greener/less toxic/etc. than TARGET in the REASONING. If it's only implied or not safer, return an empty list.
- Prefer concrete chemical or chemical-class names (e.g., "alcohol ethoxylates"), not vague terms ("surfactants").
- Normalize to common English names; include common abbreviations in parentheses if helpful.
- If multiple valid alternatives appear, return all of them (deduplicated, order by appearance).
- Do not invent information not found in REASONING. Ignore anything not in REASONING.

Return JSON with this schema:
{{
  "alternatives": ["alternative1", "alternative2", ...]
}}

Record:
TITLE: {title}
DOI: {doi}
TARGET: {target}
REASONING:
{reasoning}
"""

def ensure_list(obj):
    """Ensure object is converted to a list of strings."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return [str(x).strip() for x in obj if str(x).strip()]
    if isinstance(obj, str):
        s = obj.replace("ï¼›", ";")
        parts = [p.strip() for p in s.split(";")]
        if len(parts) == 1:
            parts = [p.strip() for p in s.split(",")]
        return [p for p in parts if p]
    return []

# Override with a robust implementation to handle full-width commas
def ensure_list(obj):
    """Ensure object is converted to a list of strings (robust)."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return [str(x).strip() for x in obj if str(x).strip()]
    if isinstance(obj, str):
        s = obj.replace('，', ';')
        parts = [p.strip() for p in s.split(';')]
        if len(parts) == 1:
            parts = [p.strip() for p in s.split(',')]
        return [p for p in parts if p]
    return []
def ensure_list_safe(obj):
    """Robust conversion of obj to list[str]; accepts list/str/None."""
    if obj is None:
        return []
    if isinstance(obj, list):
        return [str(x).strip() for x in obj if str(x).strip()]
    if isinstance(obj, str):
        s = obj.replace('，', ';')
        parts = [p.strip() for p in s.split(';')]
        if len(parts) == 1:
            parts = [p.strip() for p in s.split(',')]
        return [p for p in parts if p]
    return []

# --- Token usage tracking helpers ---
_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

def _add_usage(response: Optional[object]):
    """Accumulate token usage from a chat completion response, if present."""
    try:
        if not response:
            return
        usage = getattr(response, "usage", None)
        if not usage:
            return
        _USAGE["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        _USAGE["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0
        _USAGE["total_tokens"] += getattr(usage, "total_tokens", 0) or 0
    except Exception:
        pass

def call_model(client, title, doi, target, reasoning):
    """Call OpenAI model to extract alternatives."""
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful information extractor. Extract chemical/material names that are explicitly presented as safer alternatives in the provided text. Return only valid JSON."
                },
                {
                    "role": "user",
                    "content": PROMPT_TEMPLATE.format(title=title, doi=doi, target=target, reasoning=reasoning)
                }
            ],
            response_format={
                "type": "json_object"
            },
            temperature=0,
            max_tokens=1000
        )
        _add_usage(response)

        # Extract response content
        response_text = response.choices[0].message.content
        
        # Parse JSON response
        try:
            data = json.loads(response_text)
            return ensure_list_safe(data.get("alternatives", []))
        except json.JSONDecodeError as e:
            print(f"[WARN] JSON parsing failed for response: {response_text[:200]}... Error: {e}")
            return []
            
    except Exception as e:
        print(f"[ERROR] API call failed: {e}")
        return []
def assess_chemical_harm(client, target: str, alternatives: List[str], reasoning: str) -> List[Dict[str, Any]]:
    """Extract what harms each alternative may cause, based ONLY on REASONING.
    Returns a list of objects: { name: str, harms: [str], rationale: str }.
    If no explicit harms are stated, returns empty harms with brief rationale.
    """
    if not alternatives:
        return []
    try:
        prompt = (
            "You are a cautious extractor. Based ONLY on the REASONING text below, "
            "list any explicit harms or hazards that are stated for each ALTERNATIVE (e.g., toxicity types, endocrine disruption, persistence, bioaccumulation, occupational hazards). "
            "If the REASONING does not state harms for an alternative, return an empty list for that item and explain briefly. Do NOT invent information.\n\n"
            f"TARGET: {target}\n"
            f"ALTERNATIVES: {', '.join(alternatives)}\n"
            f"REASONING:\n{reasoning}\n\n"
            "Output only valid JSON with schema: {\n"
            "  \"harm\": [ { \"name\": string, \"harms\": [string], \"rationale\": string } ]\n"
            "}"
        )
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=900,
        )
        _add_usage(response)
        text = response.choices[0].message.content or "{}"
        data = json.loads(text)
        items = data.get("harm", [])
        results: List[Dict[str, Any]] = []
        for it in items:
            name = str(it.get("name", "")).strip()
            harms_val = it.get("harms", [])
            if isinstance(harms_val, str):
                harms = ensure_list_safe(harms_val)
            else:
                harms = [str(h).strip() for h in harms_val if str(h).strip()]
            rationale = str(it.get("rationale", "")).strip()
            if name:
                results.append({"name": name, "harms": harms, "rationale": rationale})
        # Ensure all alternatives present
        known = {r["name"].lower() for r in results}
        for alt in alternatives:
            if alt.lower() not in known:
                results.append({"name": alt, "harms": [], "rationale": "No explicit harms stated in reasoning."})
        return results
    except Exception:
        return [{"name": a, "harms": [], "rationale": "Harm extraction failed."} for a in alternatives]

def assess_target_harm(client, target: str, reasoning: str) -> Dict[str, Any]:
    """Extract explicit harms stated for the TARGET chemical from REASONING only.

    Returns: { harms: [str], rationale: str }
    """
    try:
        prompt = (
            "You are a cautious extractor. Based ONLY on the REASONING text, "
            "list any explicit harms/hazards stated for the TARGET chemical (e.g., toxicity types, endocrine disruption, persistence, bioaccumulation, occupational hazards). "
            "If no harms are explicitly stated, return an empty list and explain briefly. Do NOT add external knowledge.\n\n"
            f"TARGET: {target}\n"
            f"REASONING:\n{reasoning}\n\n"
            "Output only valid JSON with schema: {\\n  \"harms\": [string], \\n   \"rationale\": string\\n}"
        )
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        _add_usage(response)
        text = response.choices[0].message.content or "{}"
        data = json.loads(text)
        harms_val = data.get("harms", [])
        harms = ensure_list_safe(harms_val) if isinstance(harms_val, (list, str)) else []
        rationale = str(data.get("rationale", "")).strip()
        return {"harms": harms, "rationale": rationale}
    except Exception:
        return {"harms": [], "rationale": "Harm extraction failed."}

def load_input_data(input_file):
    """Load input data from JSON file."""
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] Input file not found: {input_file}")
        return None
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse input JSON: {e}")
        return None
        
    if not isinstance(records, list):
        raise ValueError("Input JSON must be a list of objects")
    
    return records

def save_output_files(out_records, output_file):
    """Save output files in JSON and CSV formats."""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Write output JSON
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(out_records, f, ensure_ascii=False, indent=2)
        print(f"JSON output written to: {output_path}")
    except Exception as e:
        print(f"[ERROR] Failed to write JSON: {e}")

    # Write output CSV
    try:
        import pandas as pd
        df = pd.DataFrame(out_records, columns=["title", "doi", "year", "target", "reasoning", "alternatives", "CHEMICAL HARM"])  # CSV includes CHEMICAL HARM column
        csv_path = output_path.with_suffix('.csv')
        df.to_csv(csv_path, index=False, encoding='utf-8')
        print(f"CSV output written to: {csv_path}")
    except ImportError:
        print("[WARN] pandas not available, skipping CSV output")
    except Exception as e:
        print(f"[WARN] Failed to write CSV: {e}")

def process_records(records, client, target, drop_empty: bool = False):
    """Process all records to extract alternatives."""
    out_records = []
    total_records = len(records)
    
    for i, rec in enumerate(records, 1):
        print(f"Processing record {i}/{total_records} for target: {target}")
        
        title = str(rec.get("title", "")).strip()
        doi = str(rec.get("doi", "")).strip()
        year = rec.get("year", "")
        reasoning = str(rec.get("reasoning", "")).strip()
        abstract = str(rec.get("abstract", "")).strip()


        if not reasoning:
            print(f"[WARN] Empty reasoning field for record {i}")
            alts_list = []
        else:
            alts_list = call_model(client, title=title, doi=doi, target=target, reasoning=reasoning)

        # Extract CHEMICAL HARM for the TARGET chemical (prefer ABSTRACT, fallback to reasoning)
        source_text = abstract if abstract else reasoning
        target_harm = assess_target_harm(client, target=target, reasoning=source_text) if source_text else {"harms": [], "rationale": ""}
        harm_str = ", ".join(target_harm.get("harms", [])) if target_harm.get("harms") else ""

        # Optionally skip records where no concrete alternatives were extracted
        if drop_empty and not alts_list:
            print(f"[INFO] Skipping record {i}: no concrete alternatives extracted")
            continue

        out_records.append({
            "title": title,
            "doi": doi,
            "year": year,
            "target": target,
            "reasoning": reasoning,  # 新增 reasoning 欄位
            "alternatives": JOIN_SEP.join(alts_list),
            # Target chemical harm details and CSV-friendly summary
            "target_chemical_harm": target_harm,
            "CHEMICAL HARM": harm_str
        })
    
    return out_records

def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Extract safer alternative names from reasoning field")
    parser.add_argument("--input_file", required=True, help="Input JSON file path")
    parser.add_argument("--output_file", required=True, help="Output JSON file path")
    parser.add_argument("--target", required=True, help="Target compound name")
    parser.add_argument("--api_key", help="OpenAI API key (optional, overrides config file)")
    parser.add_argument("--model", help="OpenAI model name (optional, overrides config file)")
    parser.add_argument("--process_all", action="store_true", help="Process all records (default only those with alternatives provided == 'yes')")
    parser.add_argument("--drop_empty", action="store_true", help="Drop records where no concrete alternatives were extracted")
    return parser.parse_args()

def main():
    """Main execution function."""
    args = parse_args()
    
    # Use provided API key or config file key
    api_key = args.api_key or OPENAI_API_KEY
    model = args.model or MODEL
    
    print(f"Extracting alternatives for target: {args.target}")
    print(f"Input file: {args.input_file}")
    print(f"Output file: {args.output_file}")
    print(f"Using OpenAI API key from config file")
    
    # Load input data
    records = load_input_data(args.input_file)
    if records is None:
        return

    # Filter records by default to those with alternatives provided == "yes".
    # Use --process_all to override and process every record.
    if args.process_all:
        filtered_records = records
        print(f"Processing {len(filtered_records)} records for target harm extraction (out of {len(records)} total)")
    else:
        filtered_records = [r for r in records if r.get("alternatives provided") == "yes"]
        if not filtered_records:
            print(f"[WARN] No records with alternatives found for {args.target}")
            # Still create empty output file
            save_output_files([], args.output_file)
            return
        print(f"Processing {len(filtered_records)} records with alternatives (out of {len(records)} total)")

    # Initialize OpenAI client
    client = OpenAI(api_key=api_key)

    # Process filtered records
    out_records = process_records(filtered_records, client, args.target, drop_empty=args.drop_empty)

    # Save output files
    save_output_files(out_records, args.output_file)

    # Print summary
    alternatives_found = sum(1 for r in out_records if r.get("alternatives", "").strip())
    print(f"\n=== SUMMARY ===")
    print(f"Target compound: {args.target}")
    print(f"Total records processed: {len(out_records)}")
    print(f"Records with extracted alternatives: {alternatives_found}")
    print(f"Results saved to: {args.output_file}")
    if args.drop_empty:
        print("Note: Records without extracted alternatives were dropped (--drop_empty).")

    # Persist token usage summary next to output
    try:
        usage_path = Path(args.output_file).with_name("step04_token_usage.json")
        with open(usage_path, "w", encoding="utf-8") as f:
            json.dump(_USAGE, f, ensure_ascii=False, indent=2)
        print(f"Token usage saved to: {usage_path}")
        print(f"Token usage total: {_USAGE.get('total_tokens', 0)} (prompt={_USAGE.get('prompt_tokens', 0)}, completion={_USAGE.get('completion_tokens', 0)})")
    except Exception as e:
        print(f"[WARN] Failed to write token usage file: {e}")

if __name__ == "__main__":
    main()
