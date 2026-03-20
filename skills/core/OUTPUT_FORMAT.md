# Output Format v1.0

## 決策輸出（嚴格 JSON，無其他文字）
{"a":"action","c":{},"r":"reason(max50char)","conf":0.0,"h":false,"cite":[]}

## 欄位
- a: adjust_params|switch_strategy|adjust_risk|pause_bot|send_alert|no_action
- c: changes dict
- r: 理由（限50字，禁止「可能」「或許」）
- conf: 信心 0.0-1.0
- h: 需人工確認
- cite: 數據引用列表（引用原始數據中的具體數值）
