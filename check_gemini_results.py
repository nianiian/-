import json

# 分析新 prompt 的 step03 結果
with open('outputs/butyl acrylate/step03_results_v3.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

yes_papers = [r for r in data if isinstance(r, dict) and r.get('alternatives provided', '').lower() == 'yes']
print(f'=== 放寬後 Prompt (OpenAI) Step03 結果 ===')
print(f'共 {len(yes_papers)} 篇有替代品\n')

for i, p in enumerate(yes_papers, 1):
    title = p.get('title', 'N/A')
    print(f'{i}. {title[:70]}')
    print(f'   DOI: {p.get("doi", "N/A")}')
    print(f'   替代品: {p.get("alternatives", [])}')
    reasoning = p.get('reasoning', 'N/A')
    print(f'   理由: {reasoning[:250]}')
    print()
