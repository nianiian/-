## 貢獻規則

### 1. 測試策略 (Testing Strategy)
- **核心邏輯:** 資料處理函式必須包含單元測試。
- **外部 API:** 在測試中 Mock 外部呼叫 (Semantic Scholar, OpenAI)；測試期間請勿進行真實 API 呼叫。
- **驗證:** 驗證 JSON 輸出格式是否符合預期 Schema。

### 2. 依賴管理 (Dependencies)
- 未經批准不得新增 AI 函式庫 (嚴格遵守 OpenAI/LangChain 的定義)。
- 維護 `requirements.txt` 並鎖定版本。

### 3. 管線完整性 (Pipeline Integrity)
- 確保 `pipeline_controller.py` 維持作為步驟調度的單一真理來源 (SSOT)。
- 輸出檔案必須使用 `pathlib` 儲存至 `outputs/` 或 `new_output/` 目錄。
