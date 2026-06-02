---
title: Vault Index
date: 2026-01-01
tags: [index]
project: 通用
---

# Vault Index

這是公開示範用的 dummy vault，模擬個人 second-brain 結構，給 obsidian-line-bot 的 retrieval pipeline 跑 demo 與 regression 用。

## 結構

| 資料夾 | 用途 |
|---|---|
| `01_Assets/` | 剪藏、參考素材 |
| `02_Projects/` | 工作專案（本範例只有 `SampleProjectA/`） |
| `03_Daily/` | 每日紀錄（`YYYYMMDD-daily-report.md`） |
| `04_Knowledge/` | 消化後的結構化知識 |

## 高價值入口

- [SampleProjectA 問題追蹤](02_Projects/SampleProjectA/issue-tracking.md) — 樣本專案 open item 與決策紀錄
- [WMS 通用查詢概念](04_Knowledge/wms-query-concepts.md) — 倉儲管理系統查詢思路（generic、非客戶資料）
- [今日 daily report](03_Daily/20260101-daily-report.md) — 範例 daily 格式

## Retrieval 規則摘要

- 命中 `index` / `首頁` / `目錄` → 回本檔
- 命中 `SampleProjectA` → 走 [02_Projects/SampleProjectA/](02_Projects/SampleProjectA/)
- 命中 `daily` / `今天` → 走 [03_Daily/](03_Daily/)
- 命中 `WMS` / `查詢` / `概念` → 走 [04_Knowledge/](04_Knowledge/)
