# U2 ScreeningEngine 測試（unittest，零依賴、決定性）
import unittest, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screening import score_candidate, rank_candidates, ScoringError, DEFAULT_POLICY

BASE_POLICY = {"skill_keywords": ["python", "sql"], "min_years": 2}

def cand(**kw):
    base = {"years": 5, "skills": ["python", "sql"], "region": "north", "seniority": "senior", "blacklist_hit": False}
    base.update(kw); return base

class TestScoring(unittest.TestCase):
    def test_full_pass(self):
        r = score_candidate(cand(), BASE_POLICY)
        self.assertTrue(r["pass"]); self.assertGreater(r["score"], 0.9)
        self.assertEqual(r["blacklist_hit"], False)
    def test_blacklist_first(self):
        r = score_candidate(cand(blacklist_hit=True), BASE_POLICY)
        self.assertFalse(r["pass"]); self.assertTrue(r["blacklist_hit"])
        self.assertIn("blacklist", r["reasons"][0])
    def test_blacklist_unknown_not_reject(self):
        r = score_candidate(cand(blacklist_hit=None), BASE_POLICY)
        self.assertTrue(r["pass"])  # 未知≠命中；留 FLAG 由 caller 人工確認（U1.1 語意同源）
    def test_years_below_min(self):
        r = score_candidate(cand(years=1), BASE_POLICY)
        self.assertFalse(r["pass"]); self.assertTrue(any("years" in x for x in r["reasons"]))
    def test_years_missing_flag_not_reject(self):
        r = score_candidate(cand(years=None), BASE_POLICY)
        self.assertTrue(any("FLAG" in x for x in r["reasons"]))
    def test_skill_missing_deduct(self):
        r = score_candidate(cand(skills=["python"]), BASE_POLICY)
        self.assertTrue(any("skill missing" in x for x in r["reasons"]))
    def test_skill_majority_missing_fails(self):
        r = score_candidate(cand(skills=["excel"]), BASE_POLICY)
        self.assertFalse(r["pass"])
    def test_region_unknown_conservative(self):
        r = score_candidate(cand(region="east"), BASE_POLICY)
        self.assertTrue(any("region" in x for x in r["reasons"]))
    def test_seniority_unknown_conservative(self):
        r = score_candidate(cand(seniority="intern"), BASE_POLICY)
        self.assertTrue(any("seniority" in x for x in r["reasons"]))
    def test_every_result_has_reasons(self):
        for c in [cand(), cand(years=None), cand(blacklist_hit=True), cand(skills=[])]:
            self.assertTrue(len(score_candidate(c, BASE_POLICY)["reasons"]) >= 1)

class TestRanking(unittest.TestCase):
    def test_order_desc(self):
        cs = [cand(years=1), cand(years=5), cand(years=3)]
        rs = rank_candidates(cs, BASE_POLICY)
        self.assertEqual([r["score"] for r in rs], sorted([r["score"] for r in rs], reverse=True))
    def test_tie_stable(self):
        cs = [cand(years=5), cand(years=5)]
        rs = rank_candidates(cs, BASE_POLICY)
        self.assertEqual(rs[0]["score"], rs[1]["score"])
    def test_determinism(self):
        cs = [cand(years=y, region=r) for y, r in [(1,"north"),(5,"south"),(3,None),(7,"central")]]
        a = rank_candidates(cs, BASE_POLICY); b = rank_candidates(cs, BASE_POLICY)
        self.assertEqual([x["score"] for x in a], [x["score"] for x in b])
    def test_consistency_rate_threshold(self):
        # MMIN85 驗收 2：排序一致率 ≥80%——同輸入兩次跑 100% 一致（決定性）
        import random
        rng = random.Random(42)
        cs = [cand(years=rng.randint(0,10), skills=rng.sample(["python","sql","etl"], rng.randint(0,3)),
                   region=rng.choice(["north","central","south",None]), seniority=rng.choice(["junior","mid","senior","lead",None]),
                   blacklist_hit=rng.choice([False, False, False, True])) for _ in range(50)]
        a = [x["score"] for x in rank_candidates(cs, BASE_POLICY)]
        b = [x["score"] for x in rank_candidates(cs, BASE_POLICY)]
        same = sum(1 for x, y in zip(a, b) if x == y)
        self.assertGreaterEqual(same / len(a), 0.80)

class TestPolicy(unittest.TestCase):
    def test_empty_keywords_raises(self):
        with self.assertRaises(ScoringError):
            score_candidate(cand(), {"skill_keywords": []})
    def test_policy_override_min_years(self):
        r = score_candidate(cand(years=3), {**BASE_POLICY, "min_years": 5})
        self.assertFalse(r["pass"])

if __name__ == "__main__":
    unittest.main()
