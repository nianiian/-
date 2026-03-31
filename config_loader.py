import json
import os
from pathlib import Path
from typing import Dict, Any


def _parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return data
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip().strip('"').strip("'")
    return data


def _load_dotenv() -> Dict[str, str]:
    # Try python-dotenv if available
    try:
        from dotenv import dotenv_values  # type: ignore
        env = dotenv_values()
        return {k: str(v) for k, v in env.items() if v is not None}
    except Exception:
        pass
    # Fallback to manual parser
    cwd_env = Path(".env")
    return _parse_env_file(cwd_env) if cwd_env.exists() else {}


def get_config() -> Dict[str, Any]:
    """Build a config dict from environment/.env, with api_config.json fallback.

    Environment variables (.env or process env) supported:
      - SEMANTIC_SCHOLAR_API_KEY
      - ELSEVIER_API_KEY
      - OPENAI_API_KEY
      - OPENAI_MODEL
      - OPENAI_MODELS (comma-separated)
      - STEP02_WORKERS, STEP03_WORKERS (ints)
      - STEP04_DROP_EMPTY (true/false)
      - UNPAYWALL_EMAIL or CONTACT_EMAIL
    """
    env_file = _load_dotenv()
    # Environment variables take precedence over .env file
    env = {**env_file, **os.environ}

    def getenv(name: str, default: str = "") -> str:
        return str(env.get(name, default))

    def getenv_int(name: str) -> int | None:
        val = env.get(name)
        if val is None:
            return None
        try:
            return int(str(val).strip())
        except Exception:
            return None

    def getenv_bool(name: str) -> bool | None:
        val = env.get(name)
        if val is None:
            return None
        s = str(val).strip().lower()
        if s in ("1", "true", "yes", "y"): return True
        if s in ("0", "false", "no", "n"): return False
        return None

    cfg: Dict[str, Any] = {
        "semantic_scholar_api_key": getenv("SEMANTIC_SCHOLAR_API_KEY"),
        "elsevier_api_key": getenv("ELSEVIER_API_KEY"),
        "springer_api_key": getenv("SPRINGER_API_KEY"),
        "openai_api_key": getenv("OPENAI_API_KEY"),
        "gemini_api_key": getenv("GEMINI_API_KEY", getenv("GOOGLE_API_KEY", "")),
        "unpaywall_email": getenv("UNPAYWALL_EMAIL", getenv("CONTACT_EMAIL", "")),
        "default_settings": {
            "llm_provider": getenv("LLM_PROVIDER", "openai"),  # "openai" or "gemini"
            "openai_model": getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            "openai_models": getenv("OPENAI_MODELS", ""),
            "gemini_model": getenv("GEMINI_MODEL", "gemini-2.0-flash"),
        },
    }
    # Optional step01 controls
    mp = getenv_int("MAX_PAPERS")
    if mp is not None:
        cfg["default_settings"]["max_papers"] = mp
    bs = getenv_int("BATCH_SIZE")
    if bs is not None:
        cfg["default_settings"]["batch_size"] = bs
    mr = getenv_int("MAX_RETRIES")
    if mr is not None:
        cfg["default_settings"]["max_retries"] = mr
    s2 = getenv_int("STEP02_WORKERS")
    if s2 is not None:
        cfg["default_settings"]["step02_workers"] = s2
    s3 = getenv_int("STEP03_WORKERS")
    if s3 is not None:
        cfg["default_settings"]["step03_workers"] = s3
    d4 = getenv_bool("STEP04_DROP_EMPTY")
    if d4 is not None:
        cfg["default_settings"]["step04_drop_empty"] = d4
    s3_pdf = getenv_bool("STEP03_DOWNLOAD_PDF")
    if s3_pdf is not None:
        cfg["default_settings"]["step03_download_pdf"] = s3_pdf
    yb = getenv_int("YEARS_BACK")
    if yb is not None and yb > 0:
        cfg["default_settings"]["years_back"] = yb
    
    # New parameters for extended search
    ye = getenv_int("YEARS_EXTENSION")
    if ye is not None and ye > 0:
        cfg["default_settings"]["years_extension"] = ye
    
    msy = getenv_int("MAX_SEARCH_YEARS")
    if msy is not None and msy > 0:
        cfg["default_settings"]["max_search_years"] = msy
    
    # Fallback to api_config.json for any missing values
    api_json = Path("api_config.json")
    if api_json.exists():
        try:
            file_cfg = json.loads(api_json.read_text(encoding="utf-8"))
            # Merge only missing keys
            for k, v in file_cfg.items():
                if k == "default_settings":
                    cfg.setdefault(k, {})
                    for dk, dv in v.items():
                        if cfg[k].get(dk) in (None, ""):
                            cfg[k][dk] = dv
                else:
                    if cfg.get(k) in (None, ""):
                        cfg[k] = v
        except Exception:
            pass
    return cfg
