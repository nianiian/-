import sys
sys.path.insert(0, '.')
from pathlib import Path
from step03 import (
    _get_http_client,
    check_semantic_scholar_pdf,
    get_pmcid_from_doi,
    download_europe_pmc_pdf,
    download_springer_jats,
    validate_pdf_content,
    ELSEVIER_API_KEY,
    SPRINGER_API_KEY,
)

# Output directory
OUT_DIR = Path("sample_downloads")
OUT_DIR.mkdir(exist_ok=True)

def save_and_report(name, content, path, is_pdf=True):
    with open(path, 'wb') as f:
        f.write(content)
    size = path.stat().st_size
    print(f"\n{name}")
    print(f"  File: {path.name}")
    print(f"  Size: {size / 1024:.1f} KB")
    if is_pdf:
        is_valid, reason = validate_pdf_content(path)
        print(f"  Valid: {is_valid}")
    # Show first few bytes
    header = content[:50]
    print(f"  Header: {header[:30]}...")

# 1. Elsevier PDF
print("=" * 60)
print("1. Elsevier API PDF")
print("=" * 60)
doi = "10.1016/j.fuel.2020.119883"
url = f"https://api.elsevier.com/content/article/doi/{doi}"
headers = {"X-ELS-APIKey": ELSEVIER_API_KEY, "Accept": "application/pdf"}
res = _get_http_client().get(url, headers=headers, timeout=30)
if res.status_code == 200:
    save_and_report("Elsevier PDF", res.content, OUT_DIR / "elsevier_sample.pdf")

# 2. Semantic Scholar / Nature OA
print("\n" + "=" * 60)
print("2. Semantic Scholar (Nature Open Access)")
print("=" * 60)
doi = "10.1038/s41598-020-79188-z"
pdf_url = check_semantic_scholar_pdf(doi)
if pdf_url:
    print(f"  PDF URL: {pdf_url}")
    res = _get_http_client().get(pdf_url, timeout=30)
    if res.status_code == 200:
        save_and_report("Semantic Scholar PDF", res.content, OUT_DIR / "semantic_scholar_sample.pdf")

# 3. Europe PMC
print("\n" + "=" * 60)
print("3. Europe PMC (MDPI via PMC)")
print("=" * 60)
doi = "10.3390/molecules30132765"
pmcid = get_pmcid_from_doi(doi)
if pmcid:
    print(f"  PMCID: {pmcid}")
    path = OUT_DIR / "europe_pmc_sample.pdf"
    if download_europe_pmc_pdf(pmcid, path):
        with open(path, 'rb') as f:
            content = f.read()
        save_and_report("Europe PMC PDF", content, path)

# 4. Springer JATS XML
print("\n" + "=" * 60)
print("4. Springer Nature JATS XML")
print("=" * 60)
doi = "10.1007/s13762-026-07099-z"
path = OUT_DIR / "springer_sample.xml"
if download_springer_jats(doi, path):
    xml_path = path.with_suffix(".xml")
    with open(xml_path, 'rb') as f:
        content = f.read()
    save_and_report("Springer JATS", content, xml_path, is_pdf=False)
    # Show XML snippet
    text = content[:500].decode('utf-8', errors='ignore')
    print(f"  XML Preview:\n{text[:300]}...")

# 5. Elsevier XML
print("\n" + "=" * 60)
print("5. Elsevier XML Fallback")
print("=" * 60)
doi = "10.1016/j.fuel.2020.119883"
url = f"https://api.elsevier.com/content/article/doi/{doi}"
headers = {"X-ELS-APIKey": ELSEVIER_API_KEY, "Accept": "text/xml"}
res = _get_http_client().get(url, headers=headers, timeout=30)
if res.status_code == 200:
    path = OUT_DIR / "elsevier_sample.xml"
    save_and_report("Elsevier XML", res.content, path, is_pdf=False)
    # Show XML snippet
    text = res.content[:500].decode('utf-8', errors='ignore')
    print(f"  XML Preview:\n{text[:300]}...")

print("\n" + "=" * 60)
print("All samples saved to: sample_downloads/")
print("=" * 60)
