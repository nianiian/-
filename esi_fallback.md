# ESI Fallback 機制 (Electronic Supplementary Information Fallback)

## 概述

ESI Fallback 是 pipeline 的**劑量補充機制**，在 step04 完成後、下一階段開始前自動執行。當所有已下載全文均無法提供完整的 material dosage 時，嘗試從期刊網站下載論文的補充資料（Supplementary Information / Supporting Information）並重新萃取。

ESI Fallback 共有兩個觸發點：
- **ESI Fallback A**：Phase A step04 完成後
- **ESI Fallback B**：Phase B step04 完成後

---

## 觸發條件（`run_esi_fallback`）

以下**全部成立**才觸發：

1. **沒有任何一篇**完整萃取（`has_material_dosage = True`）
2. **至少一篇**論文需要 ESI（status 為 `insufficient_data` 或 `partial_data` 且缺少 material dosage）

```python
# 若任一論文已有完整萃取 → 整個 ESI Fallback 跳過
if has_any_complete_extraction(compound_dir):
    return False  # 跳過
```

> **設計邏輯**：若已有論文完整萃取，代表資料已足夠，不值得花時間開 Playwright 瀏覽器下載 ESI。

---

## 需要 ESI 的論文判定（`get_insufficient_papers_needing_esi`）

從 `step04_results_{model}.json` 篩選：

| 條件 | 說明 |
|------|------|
| `alternatives provided == "yes"` | 論文確實有提到替代物 |
| `has_material_dosage != True` | 尚未取得 material dosage |
| `dosage_info.completeness != "complete"` | 萃取不完整 |
| `status != "irrelevant"` | 論文本身相關 |
| ESI 尚未存在本地 | `_find_existing_esi()` 未找到 |

---

## 本地 ESI 偵測（`_find_existing_esi`）

在 `research_pdf/` 中尋找已存在的 ESI 檔案。

**識別規則**：從 DOI 提取 article_id（DOI 後綴去掉 `.` 和 `-`，取前 8 字元），再比對檔名：

| 模式 | 範例 | 說明 |
|------|------|------|
| `-s00*` | `CSSC-18-e202402051-s001.pdf` | RSC / Wiley 格式 |
| `-sup-` | `ejoc202400158-sup-0001-misc.pdf` | Wiley Supplementary |
| `_esi` | `10.1002_cssc.202402051_ESI.pdf` | 本地命名慣例 |
| `support*` | `supporting_information.pdf` | 通用格式 |
| `supplement*` | `supplementary_data.pdf` | 通用格式 |

---

## 下載策略

### 策略 1：直接 HTTP 下載（`try_download_esi_direct`）

速度快，不需要瀏覽器自動化，**僅支援有固定 URL 格式的期刊**。

目前支援：

| 出版商 | URL 格式 | 範例 |
|--------|---------|------|
| RSC (`10.1039/`) | `https://www.rsc.org/suppdata/{xx}/{jj}/{suffix}/{suffix}1.pdf` | `10.1039/d0py00545b` → `/suppdata/d0/py/d0py00545b/d0py00545b1.pdf` |

其他出版商（Wiley、ACS）需要 Playwright Chromium。

**輸出檔名**：`{doi_safe}_ESI.pdf`（`/` 替換為 `_`）

**成功條件**：HTTP 200 且內容長度 > 1000 bytes

---

### 策略 2：Playwright Chromium 下載（`try_download_esi_playwright`）

先嘗試策略 1，失敗後才啟動 Playwright。

**支援的出版商**（依 DOI 前綴）：

| DOI 前綴 | 目標 URL |
|---------|---------|
| `10.1002/` (Wiley) | `https://onlinelibrary.wiley.com/doi/{doi}` |
| `10.1021/` (ACS) | `https://pubs.acs.org/doi/{doi}` |
| `10.1039/` (RSC) | `https://pubs.rsc.org/en/content/articlelanding/{doi}` |
| 其他 | ❌ 不支援，直接返回 False |

> **注意**：Elsevier (`10.1016/`) 目前**不支援** Playwright ESI 下載。

**Chromium 設定**（非無頭模式以繞過 Cloudflare）：

```python
browser = playwright.chromium.launch(
    headless=False,
    args=['--disable-blink-features=AutomationControlled', '--start-maximized'],
)
```

**頁面載入流程**：
1. 訪問期刊文章頁面
2. 等待 Cloudflare challenge 通過（最多 30 秒 / Wiley；其他 15 秒）
3. 額外等待 3 秒確保頁面完整載入

**ESI 連結搜尋（XPath，按優先順序）**：
```xpath
//a[contains(@href, "Supplement") or contains(@href, "supplement")]
//a[contains(@href, "Supporting") or contains(@href, "supporting")]
//a[contains(text(), "Supporting Information")]
//a[contains(text(), "Supplementary")]
//section[@id="support-info"]//a[contains(@href, ".pdf")]
```

**下載完成判定**：
- 解析 article page 上的 supplementary link
- 以 Playwright 共享 cookie 的 request context 抓取檔案內容
- 成功寫入 `research_pdf/` = 下載成功

---

## ESI Fallback 完整流程

```
run_esi_fallback()
    │
    ├─ has_any_complete_extraction()  → True  → 跳過（return False）
    │
    ├─ get_insufficient_papers_needing_esi()  → 空  → 跳過（return False）
    │
    ├─ 對每篇需要 ESI 的論文：
    │       try_download_esi_playwright(doi)
    │           ├─ try_download_esi_direct()  → RSC 直接 HTTP
    │           └─ Playwright Chromium       → Wiley / ACS / RSC
    │
    └─ 若任一下載成功：
            刪除 step04_results_{model}.json（清除快取）
            重新執行 run_step04()
            → 傳回 True（成功）
```

---

## step04 中的 ESI 讀取（`find_esi_files` in step04.py）

step04 在萃取劑量前，會在 `research_pdf/` 中尋找該論文的 ESI 並附加至全文：

**journal_abbrev 提取邏輯（修正後）**：
```python
doi_parts = doi_suffix.split(".")
alpha_parts = [p for p in doi_parts if p.isalpha() and len(p) >= 2]
journal_abbrev = max(alpha_parts, key=len) if alpha_parts else ""
```

| DOI | doi_suffix | journal_abbrev |
|-----|-----------|---------------|
| `10.1002/ejoc.202400158` | `ejoc.202400158` | `ejoc` ✅ |
| `10.1016/J.RADPHYSCHEM.2017.07.008` | `j.radphyschem.2017.07.008` | `radphyschem` ✅ |
| `10.1002/cssc.202402051` | `cssc.202402051` | `cssc` ✅ |

> 修正前使用第一段（如 `"j"`），導致 Elsevier `j.xxxxx` 格式的 DOI 以單字母誤匹配其他期刊的 ESI 檔案。

**fulltext_source 標記**：
| 情況 | `fulltext_source` |
|------|------------------|
| 只有主文 PDF | `"pdf"` |
| 只有主文 XML | `"xml"` |
| 只有 ESI | `"esi"` |
| 主文 PDF + ESI | `"pdf+esi"` |
| 主文 XML + ESI | `"xml+esi"` |

---

## 限制與已知問題

| 限制 | 說明 |
|------|------|
| Elsevier 不支援 | `10.1016/` 的論文 Playwright ESI 下載未實作 |
| RSC 直接下載只有第一個 ESI | 固定嘗試 `{suffix}1.pdf`，若有多個 ESI 只抓第一個 |
| 意外下載 | step03 Selenium 下載主文時可能意外抓到 supplementary PDF |
| 非 PDF ESI | 表格 Excel、影片等非 PDF 補充資料不處理 |
