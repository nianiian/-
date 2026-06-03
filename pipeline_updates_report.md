# Pipeline 更新報告

**日期：** 2026-06-02  
**化學物質：** 1,3-butadiene（測試案例）  
**模型：** Gemini gemini-3.1-flash-lite-preview

---

## 一、搜尋策略升級：AI 預生成關鍵字

### 背景

舊版 pipeline 僅以化合物的 IUPAC 名稱（如 `1,3-butadiene`）作為 Semantic Scholar 單一查詢詞，容易遺漏使用俗名、縮寫或工業用名撰寫的文獻。

### 新流程：Step 00 + 統一查詢池

```
Step 00（Gemini）
  └─ 輸入：化合物名稱
  └─ 輸出：4–6 條最佳化查詢字串（含同義詞、應用情境）
       ↓
Step 01（Semantic Scholar）
  └─ 查詢池 = 化合物原名 + Step 00 全部查詢字串
  └─ 去重合併後輸出單一論文清單
```

Gemini 提示詞要求模型完成三項任務：
1. 列出所有常用名、俗名、工業縮寫（例如 `1,3-butadiene` → `BD`、`butadiene`、`synthetic rubber monomer`）
2. 識別主要／次要工業用途
3. 產生覆蓋不同應用情境的查詢字串（聚合、替代品、毒理、生質來源等）

### 1,3-butadiene 實測結果

Step 00 自動產生的查詢範例：

| # | 查詢字串 |
|---|---------|
| 1 | `"1,3-butadiene" AND "alternative" AND "green chemistry"` |
| 2 | `"butadiene" AND "safer" AND "polymerization"` |
| 3 | `"1,3-butadiene" AND "substitute" AND "synthetic rubber"` |
| 4 | `"butadiene" AND "replacement" AND "elastomer"` |
| 5 | `"1,3-butadiene" AND "toxicology" AND "bio-based"` |

論文收集量：

| 查詢來源 | 收集篇數 |
|---------|---------|
| 原始化合物名稱 | 8,224 |
| 5 條 AI 查詢合計 | 112（新增去重後） |
| **總計** | **8,323** |

### 後續搜尋階段

若統一搜尋找到替代物但無劑量，pipeline 自動觸發 **Phase C**：以替代物名稱＋應用情境關鍵字再次搜尋，進一步補充文獻來源。

---

## 二、瀏覽器自動化：Selenium → Playwright 遷移

### 背景

舊版使用 Selenium + ChromeDriver 進行全文 PDF 與 ESI（電子補充資料）的下載。Selenium 需要獨立維護 ChromeDriver 版本，且無法使用 browser context 層級的網路攔截，難以處理 JavaScript 觸發的下載。

### 遷移內容

| 項目 | 舊版（Selenium） | 新版（Playwright） |
|------|----------------|-------------------|
| PDF 下載函式 | `download_via_selenium_doi()` | `download_via_playwright_doi()` |
| ESI 下載方法 | `try_download_esi_selenium()` | `try_download_esi_playwright()` |
| 瀏覽器管理 | ChromeDriver（需手動版本管理） | `playwright install chromium`（自動管理） |
| 預設模式 | headful（開啟視窗） | **headless 優先**，失敗再 fallback headful |
| 網路攔截 | 不支援 | `context.request.get()` + `response` 事件攔截 |
| 下載事件 | `expected_conditions` | `page.expect_download()` |

### PDF 下載策略（`download_via_playwright_doi`）

```
Strategy A：context.request.get() 帶 Referer
  ├─ 嘗試 meta[citation_pdf_url] 連結
  ├─ 嘗試頁面內 .pdf 超連結
  └─ 嘗試出版商專屬 URL 推導
       ├─ Wiley：/doi/pdf/{doi}、/doi/epdf/{doi}
       └─ ACS：/doi/pdf/{doi}、/doi/pdfplus/{doi}

Strategy B：瀏覽器導航 + network response 攔截
  └─ 適用 Wiley、ACS 等透過 JavaScript Viewer 嵌入 PDF 的出版商
```

### headless 模式驗證結果

| 出版商 | DOI 範例 | headless 結果 |
|--------|---------|--------------|
| RSC | `10.1039/d0ob02371j` | ✅ 成功（1.9 MB） |
| RSC ESI | `10.1039/c7cp04601d` | ✅ 成功（1.1 MB） |
| Springer OA | `10.1038/s41467-025-62409-2` | ✅ 成功（XML） |
| ACS | `10.1021/acs.joc.3c00288` | ❌ 403（需機構 Cookie） |
| Wiley | `10.1002/app.55401` | ❌ Cloudflare 攔截 |

> **備註：** ACS 及 Wiley 部分論文仍需機構授權 Cookie，VPN 本身不足以突破其下載驗證。需在已登入的瀏覽器中手動下載後放至 `research_pdf/` 目錄，pipeline 將自動在下次 Step 04 時讀取。

### step04 extraction_method 欄位更新

| 舊值 | 新值 |
|------|------|
| `fulltext_pdf_selenium_scraper` | `fulltext_pdf_playwright_scraper` |

---

## 附錄：1,3-butadiene 最終提取結果摘要

| 替代物 | 狀態 | 全文來源 |
|--------|------|---------|
| isoprene | ✅ 完整劑量 | Springer OA XML |
| acrylonitrile | ⚠️ 部分資料 | 僅摘要（會議論文） |
| myrcene / farnesene | ⚠️ 部分資料 | PMC XML + ESI |
| naphthalene / 2-MN | ⚠️ 部分資料 | OA PDF |
