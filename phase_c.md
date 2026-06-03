# Phase C：替代物上下文回退搜索 (Alternative+Context Fallback Search)

## 概述

Phase C 是 pipeline 的最後一個階段。當 Phase A（Unified Pool）已找到替代物但**無法從文獻中萃取出任何劑量**時觸發。其核心思路是：既然已知替代物名稱，就直接以「替代物」為主角搜索文獻，找出該替代物本身的使用劑量資料。

---

## 觸發條件

Phase A（Unified Pool）完成後，以下**全部成立**時觸發 Phase C：

1. `found_alternatives = False`（Phase A 未萃取到任何 dosage）
2. step04 中存在至少一筆「有替代物但劑量萃取失敗」的記錄：
   - `alternatives provided == "yes"`
   - `dosage_info.status in ("insufficient_data", "not_found")`

> 若 Phase A 中任一筆已有 `status = "extracted"` 或 `"partial_data"`，Phase C **不觸發**。

---

## 執行流程

```
1. extract_phase_c_seeds       → (alternative, context) 種子
        ↓
2. generate_phase_c_queries    → phaseC/step00_queries.json
        ↓
3. step01 (phaseC/)            → phaseC/step01_results.json
        ↓
4. step02 (phaseC/)            → phaseC/step02_results.json
        ↓
5. create_phase_c_step03_shim  → phaseC/step03_results_{model}.json
        ↓
6. step04 (phaseC/, write_final=False) → phaseC/step04_results_{model}.json
        ↓
7. merge_phase_c_results       → 合併回主 step04_results / step04_summary
```

---

## 各步驟說明

### 步驟 1 — 提取種子 (`extract_phase_c_seeds`)

從 Phase A/B 的 `step04_results_{model}.json` 中，抽取所有「有替代物但劑量不足」記錄的關鍵資訊：

| 欄位 | 來源 | 說明 |
|------|------|------|
| `alternative` | `alternatives` | 替代物名稱（去重） |
| `target_problem` | `dosage_info.substitution_logic.target_problem` | 使用原有化合物的問題描述 |
| `relationship_type` | `dosage_info.substitution_logic.relationship_type` | 替代關係類型（如 process_substitution） |

### 步驟 2 — 生成搜索查詢 (`generate_phase_c_queries`)

每個種子產生最多 4 條查詢：

| 查詢類型 | 格式 | 範例（alternative = "electron beam irradiation"） |
|---------|------|--------------------------------------------------|
| Primary | `{alternative}` | `electron beam irradiation` |
| Secondary | `{alternative} {context_keywords}` | `electron beam irradiation sterilization decontamination` |
| Tertiary | `{alternative} replace {compound}` | `electron beam irradiation replace oxirane` |
| Quaternary | `{alternative} alternative {compound}` | `electron beam irradiation alternative oxirane` |

Context keywords 從 `target_problem` 提取：移除化合物名稱、移除套路詞（toxic/hazardous/replace...），取剩餘前 4 個關鍵詞。

寫入：`phaseC/step00_queries.json`

### 步驟 3 — step01（在 phaseC/ 目錄）

- 與主流程 step01 完全相同的邏輯
- 但**輸入輸出都在** `phaseC/` 子目錄
- 讀取 `phaseC/step00_queries.json` → 輸出 `phaseC/step01_results.json`

### 步驟 4 — step02（在 phaseC/ 目錄）

- 與主流程 step02 完全相同的邏輯
- 輸出：`phaseC/step02_results.json`

### 步驟 5 — Step03 Shim（合成替代，無 LLM）

Phase C 的論文是「關於替代物本身」的文獻，不是「比較替代物與目標化合物」的文獻，因此真實 step03 LLM 會誤判為無關。Shim 直接繞過 LLM，預先注入替代物資訊：

```python
# 每篇 step02 論文都被標記為：
{
  "alternatives provided": "yes",
  "alternatives": [alt],            # 種子替代物名稱
  "reasoning": "Phase C shim: ...",
  "model_used": "phase_c_shim"
}
```

優先選取摘要中**有提及**替代物名稱的論文；若無則使用所有替代物。

輸出：`phaseC/step03_results_{model}.json`

### 步驟 6 — step04（`write_final=False`）

- 與主流程 step04 相同邏輯
- `write_final=False`：不更新 `final_output/`（留給 merge 統一處理）
- 輸出：`phaseC/step04_results_{model}.json`

### 步驟 7 — 合併 (`merge_phase_c_results`)

將 Phase C 結果合併回主 step04 結果，含以下邏輯：

**過濾**：只保留 `status in ("extracted", "partial_data")` 的記錄

**相關性過濾**：排除與目標化合物無關的記錄（guards against hallucination）
- 若 `traditional_material_replaced` 或 `target_problem` 含目標化合物名稱 → 保留
- 若為 shim 記錄 → 驗證 `explicit_dosages` 中至少一個 material 名稱與替代物匹配

**去重與升級**：
- 若 DOI 已存在於主結果：比較 status 優先級（`extracted > partial_data > insufficient_data > not_found`），高優先級者覆蓋低優先級者
- 新 DOI 直接追加

**標記**：所有 Phase C 合併的記錄加上 `"phase": "C"`

**重建 summary**：從合併後的完整結果重新計算 `step04_summary_{model}.json`

**更新 final_output**：若有 CID，更新 `final_output/{date}_{CID}.json`

---

## 成功判定

`merge_phase_c_results` 返回 `True`（至少 1 筆相關 dosage 記錄成功合併）

---

## 輸出檔案

| 檔案 | 說明 |
|------|------|
| `outputs/{compound}/phaseC/step00_queries.json` | Phase C 搜索查詢 |
| `outputs/{compound}/phaseC/step01_results.json` | Phase C 搜索論文清單 |
| `outputs/{compound}/phaseC/step02_results.json` | Phase C 含摘要論文清單 |
| `outputs/{compound}/phaseC/step03_results_{model}.json` | Phase C shim（預注入替代物） |
| `outputs/{compound}/phaseC/step04_results_{model}.json` | Phase C 劑量萃取原始結果 |
| `outputs/{compound}/step04_results_{model}.json` | **合併後**的主 step04 結果 |
| `outputs/{compound}/step04_summary_{model}.json` | **重建後**的統計摘要 |
| `final_output/{date}_{CID}.json` | 最終輸出（若有 CID） |

---

## 與 Phase A 的關鍵差異

| 面向 | Phase A | Phase C |
|------|-----------|---------|
| 搜索主角 | 目標化合物（有毒物） | **替代物**（安全物） |
| step03 | LLM 判斷是否提供替代物 | **Shim 預注入**，跳過 LLM |
| 結果目錄 | `outputs/{compound}/` | `outputs/{compound}/phaseC/` |
| 最終寫入 | `write_final=True` | `write_final=False`（合併時處理） |
| 觸發時機 | 主流程 | 只在 Phase A/B 有替代物但無劑量時 |
