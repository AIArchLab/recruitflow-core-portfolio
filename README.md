# RecruitFlow-Core 展示報告

> 由 U1（九態狀態機）＋U2（動態權重評分）引擎實跑產出——本報告非手寫，是程式輸出。

## 一、評分與排序（U2 ScreeningEngine）

| 排名 | 候選 | 年資 | 技能 | 評分 | 結果 | 解釋（為什麼） |
|---|---|---|---|---|---|---|
| 1 | 黃建國 | 10 | python,sql,etl,automation | 1.000 | 通過 | PASS: all criteria met |
| 2 | 王小明 | 6 | python,sql,etl | 0.880 | 通過 | DEDUCT: skill missing ['automation'] |
| 3 | 吳曉君 | 3 | python,automation | 0.710 | 通過 | DEDUCT: skill missing ['sql', 'etl'] |
| 4 | 謝金燕 | 2 | python,sql | 0.710 | 通過 | DEDUCT: skill missing ['etl', 'automation'] |
| 5 | 林美美 | 4 | sql,etl | 0.650 | 通過 | DEDUCT: skill missing ['python', 'automation'] |
| 6 | 李小華 | 2 | python | 0.610 | 淘汰 | DEDUCT: skill missing ['sql', 'etl', 'automation'] |
| 7 | 蔡英文 | 5 | — | 0.580 | 淘汰 | DEDUCT: skill missing ['python', 'sql', 'etl', 'automation'] |
| 8 | 劉德華 | None | sql | 0.440 | 淘汰 | FLAG: years missing — manual check (缺資料不誤拒，計入保守分); DEDUCT: skill missing ['python', 'etl', 'automation'] |
| 9 | 陳大文 | 1 | excel | 0.330 | 淘汰 | DEDUCT: years 1 < min_years 2; DEDUCT: skill missing ['python', 'sql', 'etl', 'automation'] |
| 10 | 張黑名單 | 8 | python,sql | 0.000 | 秒退 | REJECT: blacklist hit (phone/name/email matched,秒退) |

## 二、流程狀態機（U1 九態引擎，主流程演示）

| from | event | to | 判定 |
|---|---|---|---|
| NEW | E_SCREEN_PASS | PENDING_CONFIRMATION | OK |
| PENDING_CONFIRMATION | E_INTENT_ACCEPT | INTERVIEW_SCHEDULED | OK |
| INTERVIEW_SCHEDULED | E_CHECKIN | COMPLETED | OK |

非法轉移示例：COMPLETED × E_SCREEN_PASS → COMPLETED（TransitionRejected: no rule for (COMPLETED, E_SCREEN_PASS)）

## 三、誠信聲明

- 本 demo 僅涵蓋純邏輯核心（評分＋狀態機），**未接** FB/LINE/電話/DB——外部介面屬 Phase 2，未實作不宣稱。
- 全部數字由引擎實跑輸出；引擎通過 35+16 個單元測試與 10.9萬+6,000 例紅隊。
- 重跑方式：python run_demo.py（決定性，同輸入同輸出）。