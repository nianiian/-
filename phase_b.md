# Phase B：已廢棄 — 功能已合併至 Phase A

> **⚠️ 自 2026-05 起 Phase B 已從 pipeline 中移除。**
> 此文件僅供歷史參考。目前架構請參閱 [phase_a.md](phase_a.md)。

---

## 廢棄原因

舊版將 Phase A（純化合物名稱搜索）與 Phase B（AI 擴展查詢）設計為**兩個獨立階段**，Phase B 僅在 Phase A 結果為零時觸發。

實測中發現此設計存在盲區：若 Phase A 找到少量（非零）替代物但仍不足夠（例如 allyl alcohol 只找到 1 篇），Phase B 永遠不會被觸發。

---

## 取代方案：Unified Pool（Phase A 現行架構）

Phase B 的核心功能（step00 AI 查詢擴展）已整合進 Phase A，採用**聯集合並**而非「失敗時才啟動」的策略：

```
舊架構：
  Phase A (compound name only) → 失敗 → Phase B (step00 + AI queries)

新架構：
  step00 (永遠執行) → step01 (compound name ∪ AI queries) → step02 → step03 → step04
```

| 項目 | 舊 Phase A | 舊 Phase B | 新架構（Unified Pool） |
|------|-----------|-----------|----------------------|
| step00 | ❌ 不執行 | ✅ 執行 | ✅ **永遠執行（非致命）** |
| step01 查詢 | 化合物名稱（1 條） | AI 生成 4–6 條 | 化合物名稱 **∪** AI 生成（N+1 條） |
| 執行時機 | 永遠 | 僅 Phase A 失敗時 | **永遠**（合為一次） |
| ESI Fallback | ESI Fallback A | ESI Fallback B | 單次統一 ESI Fallback |

---

## 舊版觸發條件（已廢棄）

Phase A 完成後，以下全部成立時觸發 Phase B：

1. Phase A step03 未找到任何替代物  
   **或** step04 未萃取到任何 dosage（`extracted == 0`）  
   **或** ESI Fallback A 後仍無 `has_material_dosage=True` 的記錄

---

## 舊版執行流程（已廢棄）

Phase A 完成後，以下全部成立時觸發 Phase B：

1. Phase A step03 未找到任何替代物  
   **或** step04 未萃取到任何 dosage（`extracted == 0`）  
   **或** ESI Fallback A 後仍無 `has_material_dosage=True` 的記錄

---

## 執行流程

```
step00 → step01 → step02 → step03 (遞迴) → step04
                                                ↓
                                      [ESI Fallback B]
```

### Step 00 — AI 查詢生成

- **輸入**：化合物名稱
- **輸出**：`step00_queries.json`（4–6 條優化查詢）
- **LLM**：Gemini（依 `GEMINI_MODEL` 設定）
- **Prompt 策略**：
  1. 先識別所有常用名、同義詞、縮寫、工業商品名（例：oxirane → ethylene oxide / EO / 1,2-epoxyethane）
  2. 至少 1–2 條查詢**必須使用最常見工業用名**（而非 IUPAC 名稱）
  3. 查詢格式：`"{synonym}" AND "{application_context}" AND "alternative|safer|replacement"`

**為何重要**：學術文獻常使用工業慣用名而非 IUPAC 名。例如以 `"oxirane"` 搜索只得 13 篇，改用 `"ethylene oxide"` 可得 100 篇。

**輸出範例**（oxirane）：
```json
{
  "queries": [
    "\"ethylene oxide\" AND \"safer alternative\" AND \"sterilization\"",
    "\"ethylene oxide\" AND \"green chemistry\" AND \"replacement\"",
    "\"EO sterilization\" AND \"alternative method\"",
    "\"ethylene glycol\" AND \"ethylene oxide\" AND \"substitute\""
  ]
}
```

### Step 01 — 文獻搜索

- **輸入**：`step00_queries.json`（使用 AI 生成的多條查詢）
- **輸出**：`step01_results.json`（論文清單）
- **API**：Semantic Scholar
- **差異**：Phase B 的 step01 會讀取 `step00_queries.json` 並逐條搜索，結果合併去重

### Step 02 — 摘要擷取

與 Phase A 完全相同：
- **輸入**：`step01_results.json`
- **輸出**：`step02_results.json`
- **API**：Elsevier、CrossRef、Unpaywall、Semantic Scholar

### Step 03 — 替代物識別（遞迴搜索）

與 Phase A 相同的遞迴策略（10 → 20 → 30 年）：
- **輸入**：`step02_results.json`
- **輸出**：`step03_results_{model}.json`
- **注意**：覆蓋 Phase A 的同名檔案（Phase A 結果被新結果取代）

### Step 04 — 劑量萃取

與 Phase A 完全相同：
- **輸入**：`step03_results_{model}.json` + `research_pdf/`
- **輸出**：`step04_results_{model}.json`、`step04_summary_{model}.json`

---

## ESI Fallback B

> 完整 ESI 機制說明見 [esi_fallback.md](esi_fallback.md)

與 ESI Fallback A 邏輯**完全相同**，針對 Phase B 的 step04 結果執行。

**觸發條件**：
- Phase B step04 完成後，仍無 `has_material_dosage=True` 的記錄
- 存在 `status` 為 `insufficient_data` 或 `partial_data` 的論文

**流程**：直接 HTTP 下載（RSC）→ 失敗則 Playwright Chromium（Wiley / ACS / RSC）→ 成功則重跑 step04

**支援的出版商（Playwright）**：Wiley (`10.1002/`)、ACS (`10.1021/`)、RSC (`10.1039/`)

**不支援**：Elsevier (`10.1016/`)

---

## 成功判定

| 條件 | 說明 |
|------|------|
| `found_alternatives = True` | step03 找到替代物 |
| `step04_success = True` | step04 `extracted` > 0 |

任一不滿足 → 進一步檢查是否觸發 **Phase C**

---

## Phase B vs Phase A 差異對照

| 項目 | Phase A | Phase B |
|------|---------|---------|
| 搜索查詢 | 化合物精確名稱（1 條） | AI 生成 4–6 條（含同義詞/工業名） |
| step00 | ❌ 不執行 | ✅ 執行（Gemini） |
| 其餘 step01–04 | 相同邏輯 | 相同邏輯 |
| 輸出目錄 | `outputs/{compound}/` | `outputs/{compound}/`（覆蓋） |

---

## 輸出檔案

| 檔案 | 說明 |
|------|------|
| `outputs/{compound}/step00_queries.json` | AI 生成的搜索查詢 |
| `outputs/{compound}/step01_results.json` | 搜索到的論文清單（覆蓋 Phase A） |
| `outputs/{compound}/step02_results.json` | 含摘要的論文清單（覆蓋 Phase A） |
| `outputs/{compound}/step03_results_{model}.json` | 替代物識別結果（覆蓋 Phase A） |
| `outputs/{compound}/step04_results_{model}.json` | 劑量萃取詳細結果（覆蓋 Phase A） |
| `outputs/{compound}/step04_summary_{model}.json` | 萃取統計摘要（覆蓋 Phase A） |

---

## 相關設定（`.env`）

```
LLM_PROVIDER=gemini     # step00 使用 Gemini
GEMINI_MODEL=gemini-2.0-flash
YEARS_BACK=10
YEARS_EXTENSION=10
MAX_SEARCH_YEARS=30
```
