# Deep Research & Graph-RAG Agentic System for Toxic Chemical Substitution

## Project Overview (專案概述)
本研究旨在解決工業界尋找「有毒化學物質安全替代物」的痛點。透過 **Deep Research Agent** 與 **Graph-RAG** 技術，自動化搜尋文獻、萃取「功能等效」下的有效濃度/劑量，並建構結構化的知識圖譜（Knowledge Graph）。

## Core Objectives (核心目標)
1.  **Search & Screen**: 針對 SAS System 列出的 37 種高風險化學物質，自動搜尋安全替代文獻。
2.  **Dosage Extraction**: 萃取替代物在達到相同功能時所需的「有效濃度／劑量 (Dosage)」。
3.  **Graph Construction**: 將非結構化文獻轉化為 Neo4j 圖譜，支援複雜查詢。

## System Architecture (系統架構)
本系統由多個 Agent 協作完成：
* **Deep Research Agent**: 執行 Phase 1 (直接萃取) 與 Phase 2 (機制推論) 的文獻分析。
* **Orchestrator**: 協調任務分派。
* **GraphRAG Expert**: 負責圖譜檢索與 RAG 生成。
* **Reviewer**: 審核推論數據的可信度 (Confidence Score)。

## Workflow (工作流程)
1.  **Input**: 有毒物質 CAS Number / Chemical Name。
2.  **Phase 1**: 搜尋文獻，直接萃取實驗支持的濃度數據。
3.  **Phase 2**: 若無直接數據，分析化學機制與官能基特性，推論合理劑量範圍。
4.  **Output**: 生成符合 Schema 的 JSON 檔案並寫入 Graph Database。

## Current Pipeline Implementation (現有流程實作)
本專案目前由 Controller (`pipeline_controller.py`) 管理腳本式流程，並具備智能回退（Fallback）的雙階段搜尋機制：

### 雙階段搜尋架構 (Two-Phase Search Architecture)
* **Phase A (Standard Search):** 預設使用目標化學物質的名稱直接進行文獻檢索（從 Step 01 開始，不執行 Step 00）。
* **Phase B (AI Agentic Search):** 若 Phase A 未能成功找到任何安全替代物，則自動觸發執行 Step 00，透過 AI 生成進階探索策略與關鍵字重新進行完整檢索。

### 流程步驟 (Pipeline Steps)
0. **Step 00:** 生成進階搜尋關鍵字 (Fallback AI Search) - `step00.py`
   - *觸發:* **僅在 Phase A 搜尋失敗時觸發**。
   - 透過 LLM 分析該有毒化學物質的工業用途與化學特性，擴展搜尋策略（如：結合用途與「綠色替代品」等關鍵詞），打破單一名稱搜尋的限制。
1. **Step 01:** 獲取文獻資料 - `step01.py`
   - 透過 Semantic Scholar API 檢索文獻（Phase A 使用原名，Phase B 使用 Step 00 生成之關鍵字）。
2. **Step 02:** 獲取摘要並初步篩選 - `step02.py`
   - 過濾並挑選與「安全替代物 (safer alternatives)」相關的論文摘要。
3. **Step 03:** 分析替代品 (LLM) - `step03.py`
   - 分析摘要，判斷文獻中是否提出明確的替代物化學名稱。
   - 自動啟動並下載關聯文獻的 PDF/XML 全文供後續分析。
4. **Step 04:** 劑量萃取與分析 (Dosage Extraction & Inference) - `step04.py`
   - 自動解析 PDF 與 XML 下載檔。
   - 從**文獻全文**中精準萃取替代物使用的具體劑量、濃度與比例配方。
   - 針對無明確數值但有上下文證據的文獻，採取嚴謹的局部推算分析 (Partial Data Analysis)。

## Getting Started
```bash
pip install -r requirements.txt
```

## Tech Stack (技術堆疊)
* **LLM**: Gemini / OpenAI (Model dependent)
* **Database**: Neo4j (Graph DB)
* **Framework**: LangChain / LangGraph
* **Core Libraries**: Pandas, Requests, Tqdm, httpx
* **Language**: Python 3.10+

## Development Standards (程式碼規範)
- **型別提示 (Typing):** 強制要求嚴格的型別提示 (例如: `list[str]`, `dict[str, Any]`)。
- **非同步 (Async):** I/O 模組優先使用 `async/await`。
- **分層 (Layering):** Controller (`pipeline_controller.py`) -> Steps (`stepXX.py`) -> Config (`config_loader.py`)。
- **路徑處理 (Path Handling):** 所有檔案操作一律使用 `pathlib.Path`。
# -
