# Phase A：統一搜索（Unified Pool Search）

## 概述

Phase A 是 pipeline 的唯一主搜索階段，對每個化合物**永遠執行**。整合了原 Phase A（精確名稱搜索）與 Phase B（AI 擴展查詢）的功能，採用 **Unified Pool** 策略：step00 永遠先執行以生成 AI 查詢，step01 再將化合物精確名稱與 AI 查詢**聯集合併**，一次性搜索取得最大覆蓋範圍。

> **架構異動說明（2026-05）**：舊版將 Phase A（純精確名稱）與 Phase B（AI 擴展，僅在 Phase A 無結果時觸發）分為兩個獨立階段。實測發現此策略會漏失部分化合物（如 allyl alcohol 只找到 1 篇替代物文獻，達不到觸發 Phase B 的閾值），因此合併為 Unified Pool，Phase B 已廢除。

---

## 觸發條件

- 無條件執行（pipeline 的起點）

---

## 執行流程

```
step00 (永遠執行) → 生成 step00_queries.json
      ↓
step01 (Unified Pool: 化合物名稱 ∪ AI 查詢)
      ↓
step02 → step03 (遞迴) → step04
                               ↓
                       [ESI Fallback]
```

### Step 00 — AI 查詢生成（永遠執行）

- **輸入**：化合物名稱
- **輸出**：`step00_queries.json`（4–6 條優化查詢）
- **LLM**：Gemini（依 `GEMINI_MODEL` 設定）
- **Prompt 策略**：識別同義詞、縮寫、工業商品名，生成含多種用法的搜索字串
- **非致命**：即使 step00 失敗，pipeline 仍繼續（fallback 至純化合物名稱查詢）

**為何重要**：學術文獻常用工業慣用名而非 IUPAC 名。例如以 `"oxirane"` 搜索只得 13 篇，改用 `"ethylene oxide"` 可得 100 篇。

### Step 01 — 文獻搜索（Unified Pool）

- **輸入**：化合物名稱 ＋ `step00_queries.json`（若存在）
- **輸出**：`step01_results.json`（論文清單，含 DOI、標題、作者、引用數等）
- **API**：Semantic Scholar
- **查詢策略（聯集合併）**：
  1. 以化合物精確名稱作為第一條查詢（永遠包含）
  2. 讀取 `step00_queries.json`，逐條追加（已存在者去重）
  3. 所有查詢分批送出，結果依 `paperId` 去重後合併
  ```
  queries = [compound_name] + [q for q in ai_queries if q not in seen]
  ```
- **日誌範例**：`Unified query pool: compound name + 5 AI queries = 6 total`

### Step 02 — 摘要擷取

- **輸入**：`step01_results.json`
- **輸出**：`step02_results.json`（含完整摘要的論文清單）
- **API**：Elsevier、CrossRef、Unpaywall、Semantic Scholar（多來源補充）
- **並行處理**：`STEP02_WORKERS`（預設 16）個 worker 並發

### Step 03 — 替代物識別（遞迴搜索）

- **輸入**：`step02_results.json`
- **輸出**：`step03_results_{model}.json`（每篇論文標記是否提供替代物）
- **LLM**：OpenAI 或 Gemini（依 `LLM_PROVIDER` 設定）
- **遞迴策略**：
  1. 先搜索近 `YEARS_BACK`（預設 10）年的文獻
  2. 若未找到任何替代物 → 擴展至 `YEARS_BACK + YEARS_EXTENSION`（預設 20 年）
  3. 仍未找到 → 擴展至 `MAX_SEARCH_YEARS`（預設 30 年）
  4. 每次擴展前備份當前結果（`step03_results_backup_{model}_{N}y_{timestamp}.json`）
  5. 超過上限仍無結果 → 寫入 `{"no paper found": true}`，於 `final_output/` 建立 `{date}_{CID}.json`

  ```
  10 年 → (無替代物) → 20 年 → (無替代物) → 30 年
  ```

### Step 04 — 劑量萃取

- **輸入**：`step03_results_{model}.json` + `research_pdf/` 中已下載的全文
- **輸出**：
  - `step04_results_{model}.json`（詳細萃取結果）
  - `step04_summary_{model}.json`（摘要統計）
  - `step04_token_usage_{model}.json`（token 用量）
- **全文來源優先順序**：Elsevier PDF → Open Access PDF → Semantic Scholar PDF → Springer JATS XML → OpenAlex → Unpaywall → PMC XML → Selenium 爬取 → Elsevier XML fallback
- **狀態**（`dosage_status`）：`extracted` / `partial_data` / `insufficient_data` / `not_found` / `irrelevant`

---

## ESI Fallback（統一，單次）

> 完整 ESI 機制說明見 [esi_fallback.md](esi_fallback.md)

**觸發條件**：step04 完成後，以下**全部成立**才執行：
- 沒有任何論文完成完整萃取（`has_material_dosage=True`）
- 存在需要 ESI 的論文（`status` 為 `insufficient_data` 或 `partial_data` 且缺少 material dosage）

> 舊版分為 ESI Fallback A（Phase A 後）與 ESI Fallback B（Phase B 後）。架構合併後統一為單次執行。

**流程**：
1. 找出需要 ESI 的論文清單
2. 嘗試直接 HTTP 下載（RSC 有固定 URL 格式）
3. 若直接下載失敗 → Playwright Chromium（**非無頭模式**，繞過 Cloudflare）存取期刊頁面找 supplementary link
4. 任一論文下載成功 → 刪除 step04 快取 → 重跑 step04

**支援的出版商（Playwright）**：Wiley (`10.1002/`)、ACS (`10.1021/`)、RSC (`10.1039/`)

**不支援**：Elsevier (`10.1016/`) — 需手動下載後放入 `research_pdf/`

---

## 成功判定

| 條件 | 說明 |
|------|------|
| `found_alternatives = True` | step03 找到至少 1 篇提供替代物的論文 |
| `step04_success = True` | step04 summary 中 `extracted` 數量 > 0 |
| `has_any_complete_extraction = True` | 至少 1 篇有完整 material dosage |

任一不滿足，且 step04 中有「有替代物但劑量萃取失敗」的記錄 → 觸發 **Phase C**

> Phase B 已廢除。Phase A（Unified Pool）完成後直接進入 Phase C 判定。

---

## 輸出檔案

| 檔案 | 說明 |
|------|------|
| `outputs/{compound}/step01_results.json` | 搜索到的論文清單 |
| `outputs/{compound}/step02_results.json` | 含摘要的論文清單 |
| `outputs/{compound}/step03_results_{model}.json` | 替代物識別結果 |
| `outputs/{compound}/step04_results_{model}.json` | 劑量萃取詳細結果 |
| `outputs/{compound}/step04_summary_{model}.json` | 萃取統計摘要 |
| `outputs/{compound}/research_pdf/` | 下載的全文 PDF/XML |

---

## 相關設定（`.env`）

```
YEARS_BACK=10           # 初始搜索年限
YEARS_EXTENSION=10      # 每次擴展年數
MAX_SEARCH_YEARS=30     # 最大搜索年限
MAX_PAPERS=10000        # 最多撈取論文數
BATCH_SIZE=1000         # 每批次論文數
STEP02_WORKERS=16       # step02 並發 worker 數
STEP03_WORKERS=8        # step03 並發 worker 數
STEP03_DOWNLOAD_PDF=true
```
