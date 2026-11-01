# RecruitFlow-Core U2 — ScreeningEngine 動態權重評分（純函數核心）
# 對應 ADD §1 ScreeningEngine（黑名單門檻＋動態權重評分）與 MMIN85 Phase 1 驗收條款
# 紀律同 U1：零 I/O、零網路、零 DB；權重經 screening_config 注入；每筆評分附解釋（"為什麼"）
from __future__ import annotations
from dataclasses import dataclass, field

# ── 錯誤型別 ─────────────────────────────────────────────
class ScoringError(Exception):
    """評分無法執行（缺配置等）——caller 補件後重試，不猜。"""

# ── 預設權重（screening_config 缺省時的合規預設；DB 可覆寫）──
DEFAULT_POLICY = {
    "weights": {"years": 0.30, "skill": 0.40, "region": 0.20, "seniority": 0.10},
    "min_years": 2,
    "skill_keywords": ["python", "sql", "etl", "automation"],
    "region_bonus": {"north": 1.0, "central": 0.8, "south": 0.8, None: 0.5},
    "seniority_map": {"lead": 1.0, "senior": 0.8, "mid": 0.5, "junior": 0.2, None: 0.3},
    "blacklist_reject": True,   # 黑名單命中即拒（MMIN85: 100%）
    "reject_explain": True,     # 每筆排除可解釋（MMIN85: 100%）
}

def _clamp01(x): return max(0.0, min(1.0, x))

def score_candidate(candidate: dict, policy: dict | None = None) -> dict:
    """純函數評分。回傳 {"score": float, "pass": bool, "reasons": [...], "blacklist_hit": ...}
    reasons 每筆必非空——滿足「每筆排除都能回答為什麼」。"""
    p = {**DEFAULT_POLICY, **(policy or {})}
    reasons = []
    if not isinstance(candidate, dict):
        raise ScoringError("candidate must be dict")
    # 1) 黑名單優先（MMIN85 驗收 1：100% 命中即拒，先於一切）
    bl = candidate.get("blacklist_hit")
    if p["blacklist_reject"] and bl is True:
        return {"score": 0.0, "pass": False, "blacklist_hit": True,
                "reasons": ["REJECT: blacklist hit (phone/name/email matched,秒退)"]}
    # 2) 年資
    years = candidate.get("years")
    if years is None:
        reasons.append("FLAG: years missing — manual check (缺資料不誤拒，計入保守分)")
        years_score = 0.5
    else:
        my = p["min_years"]
        years_score = 1.0 if years >= my else _clamp01(years / my)
        if years < my:
            reasons.append(f"DEDUCT: years {years} < min_years {my}")
    # 3) 技能關鍵字
    skills = [s.lower() for s in (candidate.get("skills") or [])]
    kw = [k.lower() for k in p["skill_keywords"]]
    if not kw:
        raise ScoringError("policy.skill_keywords empty — 補配置")
    matched = [k for k in kw if k in skills]
    skill_score = len(matched) / len(kw)
    if skill_score < 1.0:
        missing = [k for k in kw if k not in matched]
        reasons.append(f"DEDUCT: skill missing {missing}")
    # 4) 地區
    region = candidate.get("region")
    region_score = p["region_bonus"].get(region, p["region_bonus"][None])
    if region not in p["region_bonus"]:
        reasons.append(f"FLAG: region '{region}' not in map — 用缺省保守分")
    # 5) 資深度
    sen = candidate.get("seniority")
    sen_score = p["seniority_map"].get(sen, p["seniority_map"][None])
    if sen not in p["seniority_map"]:
        reasons.append(f"FLAG: seniority '{sen}' unknown — 用缺省保守分")
    w = p["weights"]
    total = w["years"] * years_score + w["skill"] * skill_score + w["region"] * region_score + w["seniority"] * sen_score
    # 門檻：技能過半＋年資達標（years < min_years 一律不合格＝ADD T2 婉拒線；
    # years 缺失不誤拒——FLAG 記錄由 caller 人工確認，年資項不擋）
    passed = skill_score >= 0.5 and (years is None or years >= p["min_years"])
    if not passed and not reasons:
        reasons.append("REJECT: composite below threshold")
    return {"score": round(total, 4), "pass": passed, "blacklist_hit": bl is True,
            "reasons": reasons or ["PASS: all criteria met"]}

def rank_candidates(cands: list, policy: dict | None = None) -> list:
    """評分+排序。回傳按 score 降序（同分按輸入序穩定）——排序一致率可測。"""
    scored = [score_candidate(c, policy) | {"_i": i} for i, c in enumerate(cands)]
    scored.sort(key=lambda x: (-x["score"], x["_i"]))
    return [{k: v for k, v in s.items() if k != "_i"} for s in scored]
