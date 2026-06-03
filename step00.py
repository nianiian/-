import json
import logging
from typing import List

from config_loader import get_config

try:
    import google.genai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    genai = None
    GEMINI_AVAILABLE = False

logger = logging.getLogger("step00")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

def generate_search_queries(target_chemical: str) -> List[str]:
    """
    Generate optimal Semantic Scholar search queries for a chemical target
    before actually searching. Uses the Gemini API to ask about the chemical's
    primary/secondary uses and returns 3-5 optimized search queries.
    """
    config = get_config()
    api_key = config.get("gemini_api_key")
    if not api_key:
        logger.error("Gemini API key not found in configuration.")
        raise ValueError("Gemini API key not found in configuration.")
        
    if not GEMINI_AVAILABLE:
        logger.error("google-genai package is required.")
        raise ValueError("google.genai package is required to use the Gemini provider.")
    
    # Initialize client
    client = genai.Client(api_key=api_key)
    
    # Get model configuration
    model = config.get("default_settings", {}).get("gemini_model", "gemini-2.0-flash")
    
    logger.info(f"Generating search queries for '{target_chemical}' using {model}")
    
    prompt = f"""
You are an expert in chemical engineering and toxicology.
We are building a pipeline to find safer alternatives for the following chemical: "{target_chemical}".

Your task:
1. First, identify ALL common names, synonyms, abbreviations, and industrial trade names for "{target_chemical}".
   Examples:
   - "oxirane" → also known as "ethylene oxide", "EO", "1,2-epoxyethane", "oxacyclopropane"
   - "cumene" → also known as "isopropylbenzene", "2-phenylpropane"
   - "vinyl chloride" → also known as "chloroethylene", "VCM", "vinyl chloride monomer"
   - "allyl alcohol" → also known as "2-propen-1-ol", "propenol"
2. Identify the primary and secondary industrial uses, functions, or applications for this chemical.
3. Generate 4 to 6 optimized Semantic Scholar search queries to find scientific literature discussing safer, less toxic, or green alternatives to this chemical.
   - At least 1-2 queries MUST use the most common industrial name/synonym (not necessarily the IUPAC name).
   - Use varied application contexts across the queries (sterilization, synthesis, solvent, etc.).

CRITICAL INSTRUCTION: Semantic Scholar Bulk Search API struggles with long natural language sentences.
You MUST use precise boolean logic with AND / OR and double quotes. Keep the query concise, ideally maximum 3-5 terms.
DO NOT write long sentences like "safer alternatives to ethylbenzene in industrial solvent applications".
DO write database-friendly queries like: "{target_chemical}" AND "alternative" AND "solvent"

Return ONLY a JSON array of strings containing the queries. Do NOT return any markdown formatting like ```json or other text.
Example format for "oxirane":
[
  "\"oxirane\" AND \"alternative\" AND \"green chemistry\"",
  "\"ethylene oxide\" AND \"safer\" AND \"sterilization\"",
  "\"ethylene oxide\" AND \"substitute\" AND \"industrial\"",
  "\"EO\" AND (\"replacement\" OR \"free\") AND \"synthesis\""
]
    """.strip()
    
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai.types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            )
        )
        
        result_text = response.text.strip()
        
        # Parse the JSON response
        queries = json.loads(result_text)
        
        if isinstance(queries, list):
            valid_queries = [str(q).strip() for q in queries if str(q).strip()]
            logger.info(f"Generated {len(valid_queries)} queries successfully.")
            return valid_queries
        elif isinstance(queries, dict):
            # In case the model returns an object with a typical key like 'queries'
            for key in ['queries', 'search_queries']:
                if key in queries and isinstance(queries[key], list):
                    return [str(q).strip() for q in queries[key] if str(q).strip()]
        
        logger.warning(f"Unexpected JSON format returned by Gemini: {result_text}")
        return []

    except Exception as e:
        logger.error(f"Error calling Gemini or parsing response: {e}")
        return []

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = sys.argv[1]
    else:
        target = "Bisphenol A"
    
    print(f"Target Chemical: {target}")
    queries = generate_search_queries(target)
    print("\nGenerated Queries:")
    for i, q in enumerate(queries, 1):
        print(f"{i}. {q}")
