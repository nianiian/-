import json
with open(r'outputs/bisphenol A/step04_summary.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
skipped_dois = ['10.1016/j.ijadhadh.2017.10.003', '10.1186/s12903-023-03759-5', '10.1039/d2nj06225a']
skipped_dois = [d.lower() for d in skipped_dois]
count = 0
for r in data.get('records_with_dosage', []):
    if r.get('dosage_status') in ['extracted', 'partial_data'] and r.get('doi', '').lower() not in skipped_dois:
        print('DOI:', r.get('doi'))
        print('Status:', r.get('dosage_status'))
        print('Metrics:', json.dumps(r.get('dosage_metrics', [])))
        count += 1
        if count == 5: break
