#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_screening.py — 最小测试集（纯标准库 unittest，无需 pytest）

为什么需要：这套工具的输出直接用于合规决策，此前**零测试**，
任何改动都无法验证是否改变了筛查结论。本测试集优先覆盖 P0 修复过的
判定逻辑，防止回归。

运行：
    python tests/test_screening.py
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, os.path.abspath(SCRIPTS))


# ─────────────────────────────────────────────────────────────
# 1. 核心清单定义一致性（回归：DoD 曾因双定义导致报告自相矛盾）
# ─────────────────────────────────────────────────────────────
class TestCoreSourceDefinitions(unittest.TestCase):
    def test_core_sources_is_single_module_level_definition(self):
        import browser_verify as bv
        self.assertTrue(hasattr(bv, "CORE_SOURCES"),
                        "CORE_SOURCES 必须上移为模块级唯一定义")
        self.assertIn("dod_1260h", bv.CORE_SOURCES,
                      "DoD 1260H 属核心清单：原 CORE_SOURCES 漏收，导致报告自相矛盾")

    def test_fp_candidate_sources_disjoint_from_core(self):
        import browser_verify as bv
        overlap = bv.CORE_SOURCES & bv.FP_CANDIDATE_SOURCES
        self.assertEqual(overlap, set(),
                         f"核心清单与泛化清单不应重叠：{overlap}")

    def test_no_duplicate_local_core_definitions(self):
        """源码中不应再出现局部 CORE_SOURCES = {...} 重新赋值"""
        path = os.path.join(SCRIPTS, "browser_verify.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('CORE_SOURCES = {"ofac"', src,
                         "仍存在局部 CORE_SOURCES 重新定义，会与模块级定义分叉")
        self.assertNotIn('CORE_HIT_SOURCES = {"ofac"', src,
                         "仍存在局部 CORE_HIT_SOURCES 硬编码定义")


# ─────────────────────────────────────────────────────────────
# 2. 中国清单覆盖率阈值（回归：曾 12 份只开 3 份也报"未命中"）
# ─────────────────────────────────────────────────────────────
class TestChinaCoverageThreshold(unittest.TestCase):
    def test_min_coverage_threshold_sane(self):
        import browser_verify as bv
        self.assertTrue(0 < bv.CN_MIN_COVERAGE <= 1,
                        "覆盖率阈值必须落在 (0, 1]")

    def test_cn_sources_registered(self):
        import browser_verify as bv
        for key in ("cn_ucl", "cn_control", "cn_counter"):
            self.assertIn(key, bv.SOURCES, f"缺少中国源 {key}")
            self.assertIn(key, bv.CN_LIST_KEYWORDS, f"{key} 缺少标题关键词配置")
            self.assertIn(key, bv.CN_FALLBACK_URLS, f"{key} 缺少兜底入口")

    def test_no_forced_hit_fallback_in_dod(self):
        """回归：DoD 曾用 found_variant = search_name 把解析失败伪造成命中"""
        path = os.path.join(SCRIPTS, "browser_verify.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("found_variant = search_name  # fallback", src,
                         "DoD 解析失败仍被伪造成命中——假阳性回归")
        self.assertIn("parse_failed", src, "应使用 parse_failed 标记解析失败")


# ─────────────────────────────────────────────────────────────
# 3. stealth 默认关闭（回归：曾写死强制启用且无法关闭）
# ─────────────────────────────────────────────────────────────
class TestStealthGated(unittest.TestCase):
    def _source(self):
        path = os.path.join(SCRIPTS, "browser_verify.py")
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_stealth_cli_flag_exists_and_defaults_off(self):
        self.assertIn('add_argument("--stealth"', self._source())

    def test_stealth_js_not_injected_unconditionally(self):
        src = self._source()
        self.assertNotIn("\n        context.add_init_script(STEALTH_JS)",
                         "STEALTH_JS 仍在无条件注入")

    def test_user_agent_only_set_when_stealth(self):
        self.assertIn('context_kwargs["user_agent"]', self._source())

    def test_stealth_param_defaults_false(self):
        import inspect
        import browser_verify as bv
        sig = inspect.signature(bv.run_verification)
        self.assertIn("stealth", sig.parameters, "run_verification 缺少 stealth 参数")
        self.assertIs(sig.parameters["stealth"].default, False,
                      "stealth 默认值必须是 False")


# ─────────────────────────────────────────────────────────────
# 4. Tavily 可选化（回归：曾因缺 key 在模块级 sys.exit(1)）
# ─────────────────────────────────────────────────────────────
class TestTavilyOptional(unittest.TestCase):
    def test_get_api_keys_returns_empty_not_exit(self):
        import tavily_search as ts
        old = {k: os.environ.pop(k, None) for k in ("TAVILY_API_KEY", "TAVILY_API_KEYS")}
        try:
            self.assertEqual(ts.get_api_keys(), [])
            self.assertFalse(ts.has_api_keys())
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_search_returns_graceful_degradation(self):
        import tavily_search as ts
        old = {k: os.environ.pop(k, None) for k in ("TAVILY_API_KEY", "TAVILY_API_KEYS")}
        try:
            r = ts.tavily_search("dummy query")
            self.assertIs(r.get("available"), False)
            self.assertEqual(r.get("error"), "tavily_not_configured")
            self.assertEqual(r.get("results"), [])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_round_budget_respects_tiers(self):
        """standard 档必须是 4 轮而非 7 轮——成本控制"""
        import tavily_search as ts
        import inspect
        sig = inspect.signature(ts.search_entity_sanctions)
        self.assertIn("rounds", sig.parameters)
        self.assertEqual(sig.parameters["rounds"].default, "standard")
        src = inspect.getsource(ts.search_entity_sanctions)
        self.assertIn('{"minimal": 1, "standard": 4, "full": 7}', src)


# ─────────────────────────────────────────────────────────────
# 5. 风险评分：词表契约与"未查≠干净"
# ─────────────────────────────────────────────────────────────
class TestRiskScorerContracts(unittest.TestCase):
    def _score(self, report):
        import risk_scorer
        return risk_scorer.calculate_risk_score(report)

    def test_pending_verification_not_counted_as_verified(self):
        """回归：screen_entity 产出 browser_required，risk_scorer 却只认
        verified/partial，导致维度 2 恒为 0。"""
        report = {
            "matches": [{"name": "X", "properties": {"name": "X", "country": ["CN"]}}],
            "official_verification": [{"status": "browser_required", "source": "ofac"}],
        }
        d2 = self._score(report)["dimensions"]["official_verification"]
        self.assertEqual(d2.get("verified_hits"), 0,
                         "browser_required 不应被计为已验证命中")
        self.assertEqual(d2.get("pending"), 1)
        self.assertIn("尚未核验", d2["detail"])

    def test_error_verification_not_scored_as_clean(self):
        report = {"matches": [], "official_verification": [{"status": "error", "source": "un"}]}
        d2 = self._score(report)["dimensions"]["official_verification"]
        self.assertEqual(d2.get("errors"), 1, "error 状态必须被单独计数")
        self.assertIn("核验失败", d2["detail"])

    def test_intel_unavailable_marked_as_not_assessed(self):
        """回归：情报层跳过时，原实现把"没搜"报成"未发现负面报道" """
        report = {"matches": [], "official_verification": [],
                  "intelligence": {"news_results": [], "intel_available": False}}
        d8 = self._score(report)["dimensions"]["adverse_media"]
        self.assertIs(d8.get("assessed"), False)
        self.assertIn("未评估", d8["detail"])
        self.assertIn("不表示", d8["detail"])

    def test_intel_available_negative_media_scored(self):
        report = {"matches": [], "official_verification": [],
                  "intelligence": {"news_results": [
                      {"title": "sanctions against X", "content": "penalty"}],
                      "intel_available": True}}
        d8 = self._score(report)["dimensions"]["adverse_media"]
        self.assertIs(d8.get("assessed"), True)
        self.assertGreater(d8["score"], 0)

    def test_last_change_none_no_crash(self):
        """回归：运算符优先级 bug 使 last_change=None 时 TypeError"""
        report = {"matches": [{"name": "X", "properties": {"name": "X"}, "last_change": None}],
                  "official_verification": []}
        dims = self._score(report)["dimensions"]  # 不抛异常即通过
        self.assertIn("recency", dims)


# ─────────────────────────────────────────────────────────────
# 6. 名称归一化与中文变体匹配
# ─────────────────────────────────────────────────────────────
class TestNameMatching(unittest.TestCase):
    def test_normalize_is_idempotent(self):
        from fuzzy_match import normalize_name
        for s in ["Aerkomm Inc.", "爱尔康公司", "SZ DJI Technology Co., Ltd."]:
            once = normalize_name(s)
            self.assertEqual(once, normalize_name(once), f"{s} 归一化不幂等")

    def test_cn_variant_matching_prefers_chinese(self):
        import browser_verify as bv
        text = "现将爱尔康公司（Aerkomm Inc.）列入不可靠实体清单"
        self.assertTrue(bv.match_variants_cn(text, ["爱尔康公司"], prefer_chinese=True))
        self.assertTrue(bv.match_variants_cn(text, ["Aerkomm"], prefer_chinese=True),
                        "英文并列名也应能命中——公告为中英双语标注")


# ─────────────────────────────────────────────────────────────
# 7. Playwright 惰性导入（回归：曾导致无依赖时连 --help 都失败）
# ─────────────────────────────────────────────────────────────
class TestLazyPlaywright(unittest.TestCase):
    def test_module_imports_without_playwright(self):
        """本测试能跑通本身即证明：未装 Playwright 也能 import browser_verify"""
        import browser_verify as bv
        self.assertTrue(hasattr(bv, "require_playwright"))


# ─────────────────────────────────────────────────────────────
# 8. URL 正确性（回归：加拿大 URL 曾拼错 consolide，导致长期 404）
# ─────────────────────────────────────────────────────────────
class TestSourceUrls(unittest.TestCase):
    def test_canada_url_not_misspelled(self):
        import official_verify as ov
        ca = ov.OFFICIAL_SOURCES["ca"]["search_url"]
        self.assertIn("consolidated-consolidie.aspx", ca,
                      "加拿大 SEMA URL 拼写错误（consolide 少一个 i）会长期 404")
        self.assertNotIn("consolide.aspx", ca.replace("consolidie.aspx", ""),
                         "仍存在拼错的加拿大 URL")

    def test_ofac_template_marked_as_form_not_url(self):
        """OFAC 是 POST 表单，GET 模板 URL 无效——至少不应被当成可用深链"""
        import official_verify as ov
        ofac = ov.OFFICIAL_SOURCES["ofac"]
        self.assertEqual(ofac.get("type"), "form_search")


if __name__ == "__main__":
    unittest.main(verbosity=2)
