"""Polymarket Scanner — 錢包行為畫像生成器.

設計哲學：
    這是一個「錢包畫像生成器」，不是交易工具。它每次運行都為候選錢包產出
    結構化的行為報告，回答「這個錢包是什麼樣的交易者」而非「該不該跟」。
    跟單決策是另一層的事，掃描器只負責誠實描述。

四階段流程（從粗到細，從便宜到昂貴）：
    1. Discovery     — 列出近 N 天活躍的候選錢包池
    2. Coarse Filter — 用最便宜的條件淘汰絕無分析價值的錢包
    3. Features      — 為通過粗篩的錢包計算多維特徵向量
    4. Classify      — 用規則式分類產出 tier（量的閘門）+ archetype（質的畫像）

A/B/C 與 archetype 的分工：
    - A/B/C 是粗粒度的「值不值得進入注意範圍」閘門，依字母順序代表「資料樣本
      基礎的穩固度」，**不是跟單優先序**。一個 C 級的領域專家可能比 A 級通才
      更值得跟。
    - archetype 是多標籤畫像，描述「行為模式類型」（穩健/選擇/爆發/領域專家/
      異常訊息）。

版本管理：
    SCANNER_VERSION 隨計算邏輯演進而升版。同一錢包不同版本的 profile 不可
    直接比較（會混淆「行為改變」與「演算法改變」）。歷史 profile 永不重算。
"""

from __future__ import annotations

# 由 polymarket.config.load_pre_registered() 讀取後與此處對齊
# 若不一致應引發 startup error（避免 yaml 與代碼漂移）
SCANNER_VERSION = "1.5a.0"
