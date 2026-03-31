# Butyl Acrylate 替代品文獻探勘實驗報告

## 實驗概述

| 項目 | 內容 |
|------|------|
| 目標化合物 | Butyl Acrylate (丙烯酸丁酯) |
| CID | 8846 |
| 分析論文總數 | ~2,251 篇 |
| 實驗日期 | 2026年3月26日 |

---

## 1. 研究目的

利用大型語言模型 (LLM) 自動化分析科學文獻摘要，識別 **butyl acrylate** 的潛在替代品，並比較 OpenAI 與 Gemini 兩種 LLM 在相同 prompt 下的表現差異。

---

## 2. 實驗方法

### 2.1 Pipeline 流程

| 步驟 | 功能 | 說明 |
|------|------|------|
| Step 01 | 論文檢索 | 從 PubChem 與學術資料庫抓取相關論文 |
| Step 02 | 摘要獲取 | 取得論文摘要內容 |
| Step 03 | 替代品分析 | LLM 分析摘要是否提及替代品 |
| Step 04 | 劑量萃取 | 從全文中萃取替代品用量資訊 |

### 2.2 LLM 配置

| Provider | Model |
|----------|-------|
| OpenAI | gpt-4o-mini |
| Gemini | gemini-2.0-flash |

兩者使用 **完全相同的 prompt**，僅 API 呼叫方式不同。

---

## 3. Prompt 優化過程

### 3.1 原始 Prompt 問題

初始測試發現 Gemini 的誤判率較高，原因是 prompt 未明確要求替代品必須「直接取代」目標化合物本身。

**典型誤判案例：**
- 論文 DOI: `10.1016/J.PORGCOAT.2020.105969`
- 內容：CNCs (纖維素奈米晶體) 取代 VOC coalescents
- LLM 判斷：有替代品 ❌
- 實際：CNCs 取代的是「助成膜劑」，**不是** butyl acrylate

### 3.2 V3 Prompt 改進重點

```
**CRITICAL REQUIREMENT**: 
1. The abstract MUST explicitly mention "{target}" by name
2. The alternative must REPLACE or REDUCE "{target}" itself
3. If the alternative replaces a DIFFERENT component (coalescent/solvent/additive), 
   answer NO
```

**關鍵區分規則：**
- ✅ 替代品直接取代 butyl acrylate → 判定為 YES
- ✅ 部分取代 (copolymer 減少用量) → 判定為 YES
- ✅ 生物基替代品取代石化來源的 butyl acrylate → 判定為 YES
- ❌ 替代配方中的其他成分 (助成膜劑/溶劑/添加劑) → 判定為 NO

---

## 4. 實驗結果

### 4.1 Step 03 替代品偵測結果比較

| 版本 | LLM Provider | 判定有替代品的論文數 | 變化 |
|------|--------------|---------------------|------|
| 原始 Prompt | OpenAI | 10 | baseline |
| 原始 Prompt | Gemini | **58** | +480% |
| V3 Prompt | OpenAI | 6 | -40% |
| V3 Prompt | Gemini | **26** | -55% |

**觀察：**
- Gemini 傾向於更寬鬆的解釋，原始 prompt 下誤判率約 55%
- V3 prompt 有效降低 Gemini 誤判，從 58 → 26 篇
- OpenAI 原本就較為保守，V3 調整後從 10 → 6 篇

### 4.2 Step 04 劑量萃取結果

#### OpenAI V3 結果 (較精確)

| 統計項目 | 數量 |
|----------|------|
| 總處理論文 | 6 |
| 成功萃取劑量 | 4 |
| 資料不足 | 2 |
| **判定為無關** | **0** |

#### Gemini 原始 Prompt 結果 (含較多誤判)

| 統計項目 | 數量 |
|----------|------|
| 總處理論文 | 58 |
| 成功萃取劑量 | 37 |
| 部分資料 | 16 |
| **判定為無關** | **3** |

---

## 5. 識別的有效替代品

基於 V3 Prompt OpenAI 分析結果，以下為**確認有效**的 butyl acrylate 替代品：

### 5.1 生物基單體替代品

| 替代品 | DOI | 用量 | 應用 |
|--------|-----|------|------|
| **Tetrahydrogeraniol acrylate (THGA)** | 10.1021/acs.biomac.9b00185 | 1.8-3.6 mol/L | 萜烯衍生物，可完全取代 nBA |
| **β-Farnesene** | 10.1002/masy.202400098 | 20 wt% | 共聚單體，減少 BA 用量 |
| **Dibutyl itaconate (DBI)** | 10.1021/acsomega.5c03586 | 30-50 wt% | 感壓膠應用 |
| **亞麻籽油/芥花籽油衍生物** | 10.1007/s10853-023-08969-4 | 5-30 wt% | 乳膠塗料 |

### 5.2 天然高分子替代品

| 替代品 | DOI | 用量 | 應用 |
|--------|-----|------|------|
| **陽離子木薯澱粉 (CCS)** | 10.1016/j.porgcoat.2020.105693 | 0-20 wt% | 紙張塗布黏著劑 |

### 5.3 其他功能性替代品

| 替代品 | DOI | 用量 | 應用 |
|--------|-----|------|------|
| **2-Methoxyethyl acrylate** | 10.1021/ACSCATAL.8B04740 | 20 mol% | 微凝膠催化劑基材 |

---

## 6. 結論與建議

### 6.1 LLM 比較結論

| 指標 | OpenAI (gpt-4o-mini) | Gemini (gemini-2.0-flash) |
|------|---------------------|---------------------------|
| 判斷傾向 | 較保守、精確 | 較寬鬆、可能過度解讀 |
| 原始誤判率 | ~10% (1/10) | ~55% (約30/58) |
| V3 改善後 | 0% (0/6) | 待驗證 |
| **推薦用途** | 需要高精確度時使用 | 初步篩選、不漏掉潛在候選 |

### 6.2 Prompt 設計建議

1. **明確指定「直接取代」** - 避免模型將配方中其他成分的替代也納入
2. **列舉常見誤判情境** - 如 coalescents、solvents、additives 的替代不算
3. **要求 reasoning 先判斷** - 讓模型先陳述目標化合物是否被提及再做判斷

### 6.3 後續工作

- [ ] 執行 Gemini V3 的 Step 04，驗證 26 篇論文的準確性
- [ ] 比較兩個 LLM 在 V3 prompt 下的交集論文
- [ ] 評估替代品的實際可行性 (成本、性能、供應鏈)

---

## 附錄：檔案對照表

| 檔案名稱 | 說明 |
|----------|------|
| `step03_results.json` | OpenAI 原始 prompt 結果 |
| `step03_results_gemini.json` | Gemini 原始 prompt 結果 |
| `step03_results_v3.json` | OpenAI V3 prompt 結果 |
| `step03_results_gemini_v3.json` | Gemini V3 prompt 結果 |
| `step04_summary.json` | OpenAI 原始 Step04 摘要 |
| `step04_summary_gemini.json` | Gemini 原始 Step04 摘要 |
| `step04_summary_v3.json` | OpenAI V3 Step04 摘要 |

---

*報告產生時間：2026-03-26*
