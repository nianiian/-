# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Pipeline

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full pipeline for all chemicals in chemicals_test.csv
python pipeline_controller.py

# Run a single step manually (all steps accept --compound and --output-dir)
python step01.py --compound "formaldehyde" --output-dir outputs/formaldehyde
python step02.py --compound "formaldehyde" --output-dir outputs/formaldehyde
python step03.py --compound "formaldehyde" --output-dir outputs/formaldehyde
python step04.py --compound "formaldehyde" --output-dir outputs/formaldehyde
```

Logs are written to `pipeline.log` and stdout simultaneously.

## Architecture

The system is a **scripted pipeline** managed by a single controller. There is no framework like LangChain/LangGraph in the current code (it appears in README as a future goal).

**Layering rule (enforced):** `pipeline_controller.py` → `stepXX.py` → `config_loader.py`. Steps must not import from each other.

### Two-Phase Search

`pipeline_controller.py` runs each compound through:
- **Phase A:** `step01 → step02 → step03 → step04` using the chemical name directly.
- **Phase B (fallback):** If Phase A yields zero alternatives, runs `step00` first to generate AI-expanded keywords, then repeats `step01–04` with those keywords.

Step03 also triggers recursive time-range expansion: 10 → 20 → 30 years (`YEARS_BACK` / `YEARS_EXTENSION` / `MAX_SEARCH_YEARS`) if not enough results are found.

### Step responsibilities

| Step | Input | Output | Key APIs |
|------|-------|--------|----------|
| `step00` | compound name | `step00_queries.json` — expanded search keywords | Gemini |
| `step01` | compound name or keywords | `step01_results.json` — paper list | Semantic Scholar |
| `step02` | step01 results | `step02_results.json` — filtered abstracts | Elsevier, CrossRef, Unpaywall, Semantic Scholar |
| `step03` | step02 results | `step03_results.json` + downloaded PDFs/XMLs | OpenAI or Gemini, Selenium |
| `step04` | step03 results + PDFs | `step04_results.json` — dosages extracted from full text | OpenAI or Gemini, PyMuPDF, pdfplumber |

Intermediate outputs per compound are stored in `outputs/<compound_name>/`. Final deliverables go to `final_output/`.

## Configuration

All config is loaded by `config_loader.get_config()`. Priority: `.env` → system env → `api_config.json` fallback.

Key `.env` variables:

```
# API Keys
SEMANTIC_SCHOLAR_API_KEY
ELSEVIER_API_KEY
OPENAI_API_KEY
GEMINI_API_KEY
SPRINGER_API_KEY
UNPAYWALL_EMAIL

# LLM selection
LLM_PROVIDER=gemini        # "openai" or "gemini"
OPENAI_MODEL=gpt-4.1-mini
GEMINI_MODEL=gemini-2.0-flash

# Pipeline tuning
YEARS_BACK=10
YEARS_EXTENSION=10
MAX_SEARCH_YEARS=30
MAX_PAPERS=10000
BATCH_SIZE=1000
STEP02_WORKERS=16
STEP03_WORKERS=8
STEP04_DROP_EMPTY=true
STEP03_DOWNLOAD_PDF=true
```

## Code Standards

- **Type hints:** Required on all function signatures. Use `list[str]`, `dict[str, Any]`, `typing.Optional`.
- **Async:** I/O-intensive operations use `async def` + `httpx`; never use synchronous `requests` inside an `async` function.
- **Error handling:** No bare `except:`. All external API calls (OpenAI, Semantic Scholar, Elsevier) must be wrapped in `try/except` with logging.
- **Paths:** Always `pathlib.Path`; never `os.path` or hardcoded strings.
- **Config access:** Always via `config_loader.get_config()`; never read `.env` or `api_config.json` directly in step files.
- **Pydantic:** Use v2 syntax only if introduced.

## Testing

- Unit-test data-processing functions; mock all external API calls (OpenAI, Semantic Scholar).
- Validate that JSON outputs conform to their expected schema.
- Do not make real API calls during tests.
