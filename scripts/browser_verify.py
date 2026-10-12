#!/usr/bin/env python3
"""
官方制裁名单浏览器自动校验脚本（增强版 v2）
使用 Playwright 自动访问全球 10 个官方制裁搜索网站，执行搜索并截图取证。

v2 增强:
  - 自动名称变体：中文实体自动生成英文变体，每个源用英文名搜索
  - 浏览器隐身：隐藏 navigator.webdriver，伪装真实 Chrome，绕过反爬
  - 智能等待：基于选择器/网络空闲等待
  - 截图质量检查 + 重试
  - Cookie banner 自动关闭
  - 失败源标记输出

用法:
    python scripts/browser_verify.py "华为" -o screenshots/
    python scripts/browser_verify.py "Huawei Technologies" --sources ofac uk eu
    python scripts/browser_verify.py "DJI" --headed --type Entity

依赖:
    pip install playwright
    python -m playwright install chromium
"""

import argparse
import json
import os
import re
import random
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Playwright 改为**惰性导入**（原先在模块级 import 并 sys.exit(1)）：
# 未安装 Playwright 时，本模块连 `--help` 都打不出来，也无法被测试代码导入。
# 现在只有真正需要驱动浏览器时才要求它，CLI 自省与单元测试不再被 150MB 依赖挡住。
playwright_import_error = None
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
except ImportError as _e:  # noqa: F401
    sync_playwright = None
    PwTimeout = None
    playwright_import_error = _e


def require_playwright():
    """在真正需要启动浏览器时调用；缺失则给出可执行的安装指引。"""
    if sync_playwright is None:
        print("[ERROR] playwright is required", file=sys.stderr)
        print("  pip install playwright", file=sys.stderr)
        print("  python -m playwright install chromium", file=sys.stderr)
        sys.exit(1)
    return sync_playwright

# 报告渲染静态资源（CSS/JS），原内嵌于 main()，已外提以分离职责
from report_assets import REPORT_CSS

# 导入模糊匹配模块（自动变体生成）
try:
    from fuzzy_match import generate_name_variants, KNOWN_TRANSLATIONS, FULL_NAME_MAP
except ImportError:
    print("[WARN] fuzzy_match module not found, variant generation disabled")
    def generate_name_variants(name):
        return [name]
    KNOWN_TRANSLATIONS = {}
    FULL_NAME_MAP = {}


# ============================================================
# 全部 10 个官方源定义
# ============================================================

SOURCES = {
    "ofac": {
        "name": "OFAC SDN",
        "flag": "[US]",
        "url": "https://sanctionssearch.ofac.treas.gov/",
    },
    "bis_csl": {
        "name": "BIS CSL",
        "flag": "[US]",
        "url": "https://www.trade.gov/data-visualization/csl-search",
    },
    "dod_1260h": {
        "name": "DoD 1260H",
        "flag": "[US]",
        "url": "https://www.defense.gov/News/",
    },
    "sam": {
        "name": "SAM.gov Exclusions",
        "flag": "[US]",
        "url": "https://sam.gov/search/?index=ei&page=1&sort=-relevance&sfm%5Bstatus%5D%5Bis_active%5D=true",
    },
    "fcc": {
        "name": "FCC Covered List",
        "flag": "[US]",
        "url": "https://www.fcc.gov/supplychain/coveredlist",
    },
    "uk": {
        "name": "UK Sanctions",
        "flag": "[UK]",
        "url": "https://search-uk-sanctions-list.service.gov.uk/",
    },
    "un": {
        "name": "UN SC Sanctions",
        "flag": "[UN]",
        "url": "https://search.sanctions.un.org/",
    },
    "eu": {
        "name": "EU Sanctions Map",
        "flag": "[EU]",
        "url": "https://www.sanctionsmap.eu/#/main",
    },
    "au": {
        "name": "Australia DFAT",
        "flag": "[AU]",
        "url": "https://www.dfat.gov.au/international-relations/security/sanctions/consolidated-list",
    },
    "ca": {
        "name": "Canada SEMA",
        "flag": "[CA]",
        "url": "https://www.international.gc.ca/world-monde/international_relations-relations_internationales/sanctions/consolidated-consolidie.aspx",
    },

    # ============================================================
    # 中国管制清单（本 fork 新增）
    # 法律依据：《出口管制法》《不可靠实体清单规定》《反外国制裁法》
    # 说明：中国管制清单以「商务部公告 / 商务部令」形式分散发布，
    #       不存在统一的在线查询数据库，因此采用「栏目列表页 →
    #       公告正文 → 全文比对」的检索路径。
    # ============================================================
    "cn_control": {
        "name": "出口管制管控名单 / 关注名单",
        "flag": "[CN]",
        "url": "https://aqygzj.mofcom.gov.cn/flzc/gzjgfxwj/index.html",
    },
    "cn_ucl": {
        "name": "不可靠实体清单",
        "flag": "[CN]",
        "url": "https://aqygzj.mofcom.gov.cn/flzc/gzjgfxwj/index.html",
    },
    "cn_counter": {
        "name": "反制清单（商务部令）",
        "flag": "[CN]",
        "url": "https://aqygzj.mofcom.gov.cn/flzc/gzjgfxwj/index.html",
    },
}

ALL_SOURCES = list(SOURCES.keys())


# 中国清单标题关键词 —— 决定从「法律政策」栏目中筛出哪些公告进入全文比对
CN_LIST_KEYWORDS = {
    "cn_control": ["出口管制管控名单", "管控名单", "关注名单"],
    "cn_ucl":     ["不可靠实体清单"],
    "cn_counter": ["采取反制措施", "反制清单", "反制措施"],
}

# 兜底入口：栏目改版 / 深层栏目打不开时退回上层栏目页
CN_FALLBACK_URLS = {
    "cn_control": "https://aqygzj.mofcom.gov.cn/flzc/index.html",
    "cn_ucl":     "https://aqygzj.mofcom.gov.cn/flzc/index.html",
    "cn_counter": "https://aqygzj.mofcom.gov.cn/flzc/index.html",
}

# 中国清单核验的最低公告覆盖率。低于此值不得判 clean——
# 打不开的公告等于没查，未覆盖部分不能被「已核 N 份，未命中」掩盖。
CN_MIN_COVERAGE = 0.6

# 核心清单源：命中即高度可信，通常不是同名误中。
# ⚠️ 必须全库唯一定义。原实现 CORE_HIT_SOURCES（误中判定）与 CORE_SOURCES（风险分级）
#    各写一份且差集含 dod_1260h，导致同一份报告 MD 正文说"确认命中"、
#    HTML 封面说"需复核"，自相矛盾。
CORE_SOURCES = frozenset({"ofac", "bis_csl", "dod_1260h", "sam", "uk", "un"})

# 泛化清单：条目覆盖面广、同名碰撞多，命中须交叉验证后才能定性
FP_CANDIDATE_SOURCES = frozenset({"eu", "au", "ca"})


# ============================================================
# 浏览器隐身 JS（注入到每个页面，让 Playwright 伪装成真实 Chrome）
# ============================================================

STEALTH_JS = """
// 隐藏 navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 伪造 navigator.plugins（真实 Chrome 有插件）
Object.defineProperty(navigator, 'plugins', {
    get: () => [
        {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
        {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
        {name: 'Native Client', filename: 'internal-nacl-plugin'},
    ],
});

// 伪造 navigator.languages
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});

// 伪造 chrome runtime（真实 Chrome 有这个对象）
if (!window.chrome) {
    window.chrome = {};
}
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        connect: function() {},
        sendMessage: function() {},
    };
}

// 覆盖 permissions query（防止检测到自动化权限特征）
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
    parameters.name === 'notifications'
        ? Promise.resolve({state: Notification.permission})
        : originalQuery(parameters);
"""


# ============================================================
# 名称变体工具
# ============================================================

def _get_search_names(entity):
    """
    获取搜索用的名称列表。
    中文输入自动生成英文变体，英文输入也尝试扩展。
    返回: (primary_name, all_variants)
      - primary_name: 首选英文搜索名
      - all_variants: 全部变体列表
    """
    variants = generate_name_variants(entity)
    has_chinese = bool(re.search(r'[\u4e00-\u9fff]', entity))

    if has_chinese:
        # 中文输入：首选英文简称
        for v in variants:
            if not re.search(r'[\u4e00-\u9fff]', v):
                primary = v
                break
        else:
            primary = entity
    else:
        primary = entity

    print(f"  [VARIANTS] Input: {entity}")
    print(f"  [VARIANTS] Primary search name: {primary}")
    if len(variants) > 1:
        print(f"  [VARIANTS] All variants: {variants}")

    return primary, variants


def _ts():
    return str(int(time.time() * 1000))


def _human_delay(min_ms=200, max_ms=800):
    """模拟人类操作间的随机延迟"""
    time.sleep(random.randint(min_ms, max_ms) / 1000.0)


# ============================================================
# 增强工具函数
# ============================================================

COOKIE_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "[id*='cookie'] button[class*='accept']",
    "button[id*='accept-cookie']",
    ".cookie-banner button.accept",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Accept Cookies')",
    "button:has-text('I Accept')",
    "button:has-text('Got it')",
    "button:has-text('OK')",
    "button:has-text('Agree')",
]

RESULT_INDICATORS = {
    "ofac": ["#scrollResults", "#ctl00_MainContent_grvSearchResults", "#ctl00_MainContent_pnlData"],
    "bis_csl": [".results-count", "[data-results]", ".search-results", ".result-item"],
    "sam": ["sds-table", ".results", "[class*='result']"],
    "uk": [".search-results", ".govuk-table", "#search-results"],
    "un": [".results", ".search-results", "table"],
    "eu": [".sanctions-list", ".regime-list", "[class*='result']"],
}


def _dismiss_cookie_banner(page):
    """自动关闭 cookie/consent 弹窗"""
    for sel in COOKIE_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000)
                page.wait_for_timeout(500)
                print("  [INFO] Cookie banner dismissed")
                return True
        except Exception:
            continue
    return False


def _smart_wait(page, source_key=None, custom_selectors=None, timeout=15000):
    """智能等待页面结果加载完成"""
    selectors = custom_selectors or RESULT_INDICATORS.get(source_key, [])
    for sel in selectors:
        try:
            page.wait_for_selector(sel, timeout=min(timeout, 8000))
            page.wait_for_timeout(500)
            return True
        except Exception:
            continue
    try:
        page.wait_for_load_state("networkidle", timeout=min(timeout, 10000))
        return True
    except Exception:
        pass
    page.wait_for_timeout(3000)
    return False


def _check_screenshot_quality(path, min_size_kb=5):
    """检查截图是否有效"""
    if not os.path.exists(path):
        return False
    size_kb = os.path.getsize(path) / 1024
    if size_kb < min_size_kb:
        print(f"  [WARN] Screenshot too small ({size_kb:.1f}KB), likely blank")
        return False
    return True


def _render_pdf_highlighted(pdf_data, variants, out_dir, source_key, search_name):
    """
    用 PyMuPDF 精确渲染 PDF 每一页，在匹配实体名处添加黄色高亮 + 红色矩形框。
    返回 (found_variant, matched_pages, highlight_screenshots)
    - found_variant: 第一个匹配到的变体名
    - matched_pages: 命中页码列表（0-indexed）
    - highlight_screenshots: 高亮渲染后的截图路径列表
    """
    found_variant = None
    matched_pages = []
    highlight_screenshots = []

    try:
        import fitz
        import tempfile
        tmp_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_pdf.write(pdf_data)
        tmp_pdf.close()
        doc = fitz.open(tmp_pdf.name)

        for pg_idx in range(len(doc)):
            page = doc[pg_idx]
            page_has_match = False
            match_count_on_page = 0

            for v in variants:
                if re.search(r'[\u4e00-\u9fff]', v):
                    continue
                rects = page.search_for(v)
                if rects:
                    if not found_variant:
                        found_variant = v
                    page_has_match = True
                    match_count_on_page += len(rects)
                    # 给每个匹配添加高亮标注
                    for rect in rects:
                        # 黄色高亮
                        highlight = page.add_highlight_annot(rect)
                        highlight.set_colors(stroke=(1, 1, 0))
                        highlight.update()
                        # 红色矩形框
                        # 稍微扩大矩形，更醒目
                        expanded = fitz.Rect(rect.x0 - 2, rect.y0 - 2,
                                             rect.x1 + 2, rect.y1 + 2)
                        page.draw_rect(expanded, color=(1, 0, 0), width=2)

            if page_has_match:
                matched_pages.append(pg_idx)
                # 渲染该页为高清图片
                pix = page.get_pixmap(dpi=150)
                ts = _ts()
                safe_name = search_name.replace(" ", "_").replace("/", "_")[:30]
                fname = f"{source_key}_{safe_name}_highlight_p{pg_idx + 1}_{ts}.png"
                fpath = os.path.join(out_dir, fname)
                pix.save(fpath)
                highlight_screenshots.append(fpath)
                print(f"  [HIGHLIGHT] Page {pg_idx + 1}: {match_count_on_page} matches highlighted → {fname}")

        doc.close()
        try:
            os.unlink(tmp_pdf.name)
        except Exception:
            pass

        if found_variant:
            print(f"  [INFO] PDF highlight: '{found_variant}' on pages {[p+1 for p in matched_pages]}")
    except Exception as e:
        print(f"  [WARN] PDF highlight rendering failed: {e}")

    return found_variant, matched_pages, highlight_screenshots


def _screenshot(page, out_dir, source_key, entity, label="", timeout=60000,
                full_page=True, max_retries=2):
    """增强截图：全页面 + 质量检查 + 重试"""
    ts = _ts()
    suffix = f"_{label}" if label else ""
    safe_entity = entity.replace(" ", "_").replace("/", "_")[:30]
    fname = f"{source_key}_{safe_entity}{suffix}_{ts}.png"
    path = os.path.join(out_dir, fname)

    for attempt in range(max_retries + 1):
        try:
            page.screenshot(path=path, full_page=full_page, timeout=timeout)
            if _check_screenshot_quality(path):
                print(f"  [SCREENSHOT] {fname} ({os.path.getsize(path) / 1024:.0f}KB)")
                return path
            elif attempt < max_retries:
                print(f"  [RETRY] Screenshot quality low, retrying ({attempt + 1})...")
                page.wait_for_timeout(2000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(500)
        except Exception as e:
            if attempt < max_retries:
                print(f"  [RETRY] Screenshot failed ({e}), retrying ({attempt + 1})...")
                page.wait_for_timeout(2000)
            else:
                print(f"  [ERROR] Screenshot failed: {e}")
                return path

    print(f"  [SCREENSHOT] {fname} (quality uncertain)")
    return path


def _screenshot_element(page, out_dir, source_key, entity, selector, label="detail"):
    """截取特定元素的截图"""
    try:
        el = page.query_selector(selector)
        if el and el.is_visible():
            ts = _ts()
            safe_entity = entity.replace(" ", "_").replace("/", "_")[:30]
            fname = f"{source_key}_{safe_entity}_{label}_{ts}.png"
            path = os.path.join(out_dir, fname)
            el.screenshot(path=path, timeout=15000)
            if _check_screenshot_quality(path):
                print(f"  [SCREENSHOT-DETAIL] {fname}")
                return path
    except Exception:
        pass
    return None


def _safe_goto(page, url, timeout=30000):
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        _human_delay(800, 1500)
        _dismiss_cookie_banner(page)
        return True
    except PwTimeout:
        print(f"  [WARN] Page load timeout, continuing...")
        _dismiss_cookie_banner(page)
        return True
    except Exception as e:
        print(f"  [ERROR] Navigation failed: {e}")
        return False


def _safe_fill_and_click(page, input_selector, value, button_selector=None,
                         press_enter=False, source_key=None):
    """增强版：模拟人类输入 + 智能等待"""
    try:
        el = page.query_selector(input_selector)
        if not el:
            page.wait_for_selector(input_selector, timeout=8000)
        # 先点击聚焦
        page.click(input_selector, timeout=5000)
        _human_delay(100, 300)
        # 清空已有内容
        page.fill(input_selector, "", timeout=3000)
        _human_delay(50, 150)
        # 输入新内容
        page.fill(input_selector, value, timeout=8000)
        _human_delay(300, 600)
        if button_selector:
            page.click(button_selector, timeout=8000)
        elif press_enter:
            page.press(input_selector, "Enter")
        _smart_wait(page, source_key=source_key, timeout=10000)
        return True
    except Exception as e:
        print(f"  [WARN] Form operation failed: {e}")
        return False


def _extract_text(page, max_chars=2000):
    try:
        text = page.inner_text("body", timeout=5000)
        return text[:max_chars] if text else ""
    except Exception:
        return ""


def _highlight_text(page, search_term, out_dir, source_key, entity, label="highlight"):
    """
    在页面中高亮搜索词（模拟 Ctrl+F），然后截图。
    用 JS 注入 <mark> 标签高亮所有匹配文本，并滚动到第一个匹配位置。
    """
    try:
        # 注入 JS 高亮所有匹配文本
        safe_term = search_term.replace("'", "\\'")
        highlight_count = page.evaluate(f"""(() => {{
            const text = '{safe_term}';
            const regex = new RegExp(text.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'gi');
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let count = 0;
            const nodesToHighlight = [];
            while (walker.nextNode()) {{
                if (regex.test(walker.currentNode.textContent)) {{
                    nodesToHighlight.push(walker.currentNode);
                    regex.lastIndex = 0;
                }}
            }}
            nodesToHighlight.forEach(node => {{
                try {{
                    const parent = node.parentElement;
                    if (parent && parent.tagName !== 'SCRIPT' && parent.tagName !== 'STYLE') {{
                        const html = node.textContent.replace(regex, '<mark style="background-color: #FFFF00; padding: 2px 4px; border: 2px solid #FF0000; font-weight: bold;">$&</mark>');
                        const span = document.createElement('span');
                        span.innerHTML = html;
                        parent.replaceChild(span, node);
                        count++;
                    }}
                }} catch(e) {{}}
            }});
            // 滚动到第一个高亮位置
            const firstMark = document.querySelector('mark');
            if (firstMark) {{
                firstMark.scrollIntoView({{block: 'center', behavior: 'smooth'}});
            }}
            return count;
        }})()""")

        if highlight_count and highlight_count > 0:
            page.wait_for_timeout(1000)
            ts = _ts()
            safe_entity = entity.replace(" ", "_").replace("/", "_")[:30]
            fname = f"{source_key}_{safe_entity}_{label}_{ts}.png"
            path = os.path.join(out_dir, fname)
            page.screenshot(path=path, full_page=True, timeout=15000)
            if _check_screenshot_quality(path):
                print(f"  [HIGHLIGHT] {fname} ({highlight_count} matches highlighted)")
                return path
    except Exception as e:
        print(f"  [WARN] Highlight failed: {e}")
    return None


def _save_pandas_screenshot(page, df_html, out_dir, source_key, entity, label="pandas_result"):
    """
    将 pandas 分析结果渲染为 HTML 表格，在浏览器中打开并截图。
    """
    try:
        import tempfile
        html_content = f"""
        <html><head><meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }}
            h2 {{ color: #333; border-bottom: 2px solid #0066cc; padding-bottom: 10px; }}
            table {{ border-collapse: collapse; width: 100%; background: white; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            th {{ background: #0066cc; color: white; padding: 10px; text-align: left; font-size: 13px; }}
            td {{ padding: 8px 10px; border-bottom: 1px solid #eee; font-size: 12px; }}
            tr:hover {{ background: #f0f7ff; }}
            .source {{ color: #666; font-size: 11px; }}
            mark {{ background-color: #FFFF00; padding: 1px 3px; border: 1px solid #FF0000; }}
        </style></head>
        <body>
            <h2>📊 Australia DFAT Consolidated Sanctions List - Analysis Result</h2>
            <p>Source: Australian_Sanctions_Consolidated_List.xlsx</p>
            {df_html}
        </body></html>
        """
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode='w', encoding='utf-8') as f:
            f.write(html_content)
            tmp_path = f.name

        page.goto(f"file:///{tmp_path.replace(os.sep, '/')}", wait_until="domcontentloaded", timeout=10000)
        page.wait_for_timeout(1000)

        ts = _ts()
        safe_entity = entity.replace(" ", "_").replace("/", "_")[:30]
        fname = f"{source_key}_{safe_entity}_{label}_{ts}.png"
        path = os.path.join(out_dir, fname)
        page.screenshot(path=path, full_page=True, timeout=15000)

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if _check_screenshot_quality(path):
            print(f"  [SCREENSHOT] Pandas result: {fname}")
            return path
    except Exception as e:
        print(f"  [WARN] Pandas screenshot failed: {e}")
    return None


# ============================================================
# 各源校验函数（自动使用英文变体搜索）
# ============================================================

def verify_ofac(page, entity, entity_type, out_dir, search_name, variants):
    result = {"source": "ofac", "status": "error", "screenshots": [], "searched": search_name}
    if not _safe_goto(page, SOURCES["ofac"]["url"]):
        return result

    try:
        # 只填 Name 框，不要碰 Address 框
        name_field = page.query_selector("#ctl00_MainContent_txtLastName")
        if name_field:
            name_field.click()
            _human_delay(100, 200)
            name_field.fill(search_name)
        _human_delay()
        if entity_type == "Individual":
            page.select_option("#ctl00_MainContent_ddlType", "Individual")
        else:
            page.select_option("#ctl00_MainContent_ddlType", "Entity")
        # 设置模糊匹配阈值为 70
        score_field = page.query_selector("#ctl00_MainContent_txtScore")
        if score_field:
            score_field.click()
            score_field.fill("")
            score_field.fill("70")
        _human_delay()
        page.click("#ctl00_MainContent_btnSearch", timeout=5000)
        _smart_wait(page, source_key="ofac", timeout=10000)
    except Exception as e:
        print(f"  [WARN] OFAC standard selectors failed ({e}), trying fallback...")
        try:
            # fallback：只用第一个 text input (Name)
            name_input = page.query_selector("#ctl00_MainContent_txtLastName")
            if name_input:
                name_input.fill(search_name)
            else:
                inputs = page.query_selector_all("input[type='text']")
                if inputs:
                    inputs[0].fill(search_name)
            buttons = page.query_selector_all("input[type='submit'], button[type='submit']")
            if buttons:
                buttons[0].click()
            _smart_wait(page, source_key="ofac", timeout=10000)
        except Exception as e2:
            print(f"  [ERROR] OFAC fallback also failed: {e2}")

    # 高亮匹配文本并截完整页面（唯一截图）
    hl = _highlight_text(page, search_name, out_dir, "ofac", search_name, "highlight")
    if hl:
        result["screenshots"].append(hl)
    else:
        # 高亮失败时，兜底截取完整结果页
        s1 = _screenshot(page, out_dir, "ofac", search_name, "results")
        if s1:
            result["screenshots"].append(s1)

    text = _extract_text(page)
    if "0 results" in text.lower() or "no results" in text.lower():
        result["status"] = "clean"
        result["detail"] = "No match found"
    elif "result" in text.lower():
        result["status"] = "hit"
        result["detail"] = "Match found"
    else:
        result["status"] = "manual_review"
        result["detail"] = "Manual review needed"
    # 兜底：如果截图列表空，强制截当前页面
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "ofac", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_bis_csl(page, entity, out_dir, search_name, variants):
    """
    BIS CSL 校验：浏览器搜索为主 + 翻页截全部页面。
    CSL 搜索结果有分页，必须逐页截图作为完整证据。
    """
    result = {"source": "bis_csl", "status": "error", "screenshots": [], "searched": search_name}

    # 浏览器访问 CSL 搜索引擎（iframe 源 URL）
    csl_url = "https://mdspublicprod.z13.web.core.windows.net/csl-search/"
    print(f"  [INFO] Opening CSL search engine: {csl_url}")
    if _safe_goto(page, csl_url, timeout=45000):
        page.wait_for_timeout(8000)

        # 搜索
        search_filled = False
        for sel in ["input#name", "input.explorer__form__input", "input[type='text']"]:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    _human_delay(200, 400)
                    el.fill(search_name)
                    _human_delay(300, 600)
                    btn = page.query_selector("button.explorer__form__submit")
                    if btn:
                        btn.click()
                    else:
                        page.keyboard.press("Enter")
                    search_filled = True
                    print(f"  [INFO] CSL search filled via {sel}")
                    break
            except Exception:
                continue

        if search_filled:
            page.wait_for_timeout(8000)

        # 提取结果数
        text = _extract_text(page, max_chars=5000)
        import re as _re
        match_count = _re.search(r'(\d+)\s*results?\.?', text)
        total_results = int(match_count.group(1)) if match_count else 0
        print(f"  [INFO] CSL search results: {total_results}")

        if total_results > 0:
            result["status"] = "hit"
            result["detail"] = f"{total_results} results found on CSL search engine"
        else:
            result["status"] = "clean"
            result["detail"] = "No results found on CSL search engine"

        # 第1页截图
        s1 = _screenshot(page, out_dir, "bis_csl", search_name, "page1")
        if s1:
            result["screenshots"].append(s1)

        # 翻页截图（CSL 分页按钮是 div.explorer__result__page-item[data-page="N"]）
        if total_results > 0:
            page_num = 2
            max_pages = 20  # 安全上限
            while page_num <= max_pages:
                try:
                    # CSL 实际 DOM 结构：div.explorer__result__page-item[data-page="N"]
                    next_btn = page.query_selector(f'.explorer__result__page-item[data-page="{page_num}"]')
                    if not next_btn or not next_btn.is_visible():
                        print(f"  [INFO] CSL pagination: reached last page (page {page_num - 1})")
                        break

                    # 滚动到分页按钮位置先
                    next_btn.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    next_btn.click()
                    page.wait_for_timeout(3000)

                    sp = _screenshot(page, out_dir, "bis_csl", search_name, f"page{page_num}")
                    if sp:
                        result["screenshots"].append(sp)
                    print(f"  [SCREENSHOT] CSL page {page_num}")
                    page_num += 1
                except Exception as e:
                    print(f"  [INFO] CSL pagination stopped at page {page_num}: {e}")
                    break

    # 兜底截图
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "bis_csl", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_dod_1260h(page, entity, out_dir, search_name, variants):
    """
    DoD 1260H 校验：浏览器打开 PDF，用 Ctrl+F 搜索，逐页截图。
    Chrome PDF embed 不暴露 innerText，必须用 Ctrl+F 搜索判断命中。
    """
    result = {"source": "dod_1260h", "status": "error", "screenshots": [], "searched": search_name}

    # DoD 1260H 名单 PDF 已知 URL（年度更新，最新在前）
    pdf_urls = [
        "https://media.defense.gov/2025/Jan/07/2003625471/-1/-1/1/ENTITIES-IDENTIFIED-AS-CHINESE-MILITARY-COMPANIES-OPERATING-IN-THE-UNITED-STATES.PDF",
        "https://media.defense.gov/2024/Jan/31/2003387320/-1/-1/1/DOD-RELEASES-LIST-OF-PEOPLES-REPUBLIC-OF-CHINA-PRC-MILITARY-COMPANIES-IN-ACCORDANCE-WITH-SECTION-1260H-OF-THE-NATIONAL-DEFENSE-AUTHORIZATION-ACT-FOR-FISCAL-YEAR-2021.PDF",
    ]

    for pdf_url in pdf_urls:
        try:
            print(f"  [INFO] Opening DoD 1260H PDF: {pdf_url[:80]}...")
            if _safe_goto(page, pdf_url, timeout=45000):
                page.wait_for_timeout(5000)

                # 先点击 PDF 内容区域聚焦（Chrome PDF embed 必须先聚焦才能接收键盘事件）
                page.mouse.click(600, 400)
                page.wait_for_timeout(500)

                # 截图每一页 PDF（通过 PageDown 翻页）
                s1 = _screenshot(page, out_dir, "dod_1260h", search_name, "pdf_page1")
                if s1:
                    result["screenshots"].append(s1)
                for pg in range(2, 8):  # PDF 一般 5-7 页
                    page.mouse.click(600, 400)  # 每次翻页前重新聚焦
                    page.wait_for_timeout(200)
                    page.keyboard.press("PageDown")
                    page.wait_for_timeout(1500)
                    sp = _screenshot(page, out_dir, "dod_1260h", search_name, f"pdf_page{pg}")
                    if sp:
                        result["screenshots"].append(sp)

                # Chrome PDF 是 out-of-process 渲染，Playwright 无法控制 Ctrl+F
                # 用浏览器 fetch 下载 PDF → PyMuPDF 精确高亮每个匹配项 → 渲染高清图
                found_variant = None
                parse_failed = False
                parse_error = ""
                try:
                    import base64
                    pdf_b64 = page.evaluate("""async () => {
                        const resp = await fetch(window.location.href);
                        const buf = await resp.arrayBuffer();
                        const arr = new Uint8Array(buf);
                        let binary = '';
                        for (let i = 0; i < arr.length; i++) binary += String.fromCharCode(arr[i]);
                        return btoa(binary);
                    }""")
                    pdf_data = base64.b64decode(pdf_b64)
                    found_variant, matched_pages, hl_screenshots = _render_pdf_highlighted(
                        pdf_data, variants, out_dir, "dod_1260h", search_name)
                    result["screenshots"].extend(hl_screenshots)
                except Exception as e:
                    print(f"  [WARN] PDF highlight failed: {e}")
                    # 解析失败 != 命中。原实现在此处 fallback 成 found_variant = search_name，
                    # 导致 PyMuPDF 未安装 / PDF 损坏 / fetch 失败时**必然误报命中**。
                    # 改为标记解析失败，结论落到 manual_review。
                    parse_failed = True
                    parse_error = str(e)

                if parse_failed:
                    result["status"] = "manual_review"
                    result["detail"] = (
                        "DoD 1260H PDF 已打开，但文本解析失败，无法判定是否命中。"
                        "常见原因：未安装 PyMuPDF(fitz)、PDF 损坏、或页面 fetch 失败。"
                        "须人工打开 PDF 正文核对。原始错误：" + (parse_error or "unknown")
                    )
                    result["pdf_url"] = pdf_url
                    print(f"  [REVIEW] PDF parse failed, manual review required: {parse_error}")
                elif found_variant:
                    result["status"] = "hit"
                    result["detail"] = f"Found '{found_variant}' in DoD 1260H PDF"
                    result["pdf_url"] = pdf_url
                    print(f"  [HIT] Found '{found_variant}' in 1260H list")
                else:
                    result["status"] = "clean"
                    result["detail"] = "Not found in DoD 1260H PDF"
                break
        except Exception as e:
            print(f"  [WARN] Browser PDF load failed: {e}")
            continue

    # 兜底截图
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "dod_1260h", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_sam(page, entity, out_dir, search_name, variants):
    result = {"source": "sam", "status": "error", "screenshots": [], "searched": search_name}
    encoded = search_name.replace(" ", "+")
    url = (
        f"https://sam.gov/search/?index=ei&page=1&sort=-relevance"
        f"&sfm%5Bstatus%5D%5Bis_active%5D=true"
        f"&sfm%5BsimpleSearch%5D%5BkeywordRadioButton%5D=ALL"
        f"&sfm%5BsimpleSearch%5D%5BkeywordTags%5D%5B0%5D%5Bkey%5D={encoded}"
        f"&sfm%5BsimpleSearch%5D%5BkeywordTags%5D%5B0%5D%5Bvalue%5D={encoded}"
    )
    if not _safe_goto(page, url, timeout=45000):
        return result

    _smart_wait(page, source_key="sam", timeout=20000)
    page.wait_for_timeout(3000)

    s1 = _screenshot(page, out_dir, "sam", search_name, "results")
    if s1:
        result["screenshots"].append(s1)

    for sel in ["sds-table", ".usa-table", "table"]:
        detail = _screenshot_element(page, out_dir, "sam", search_name, sel, "results_detail")
        if detail:
            result["screenshots"].append(detail)
            break

    text = _extract_text(page)
    if "no results" in text.lower() or "0 result" in text.lower():
        result["status"] = "clean"
        result["detail"] = "No match found"
    elif any(v.lower() in text.lower() for v in variants if not re.search(r'[\u4e00-\u9fff]', v)):
        result["status"] = "hit"
        result["detail"] = "Exclusion record found"
    else:
        result["status"] = "manual_review"
        result["detail"] = "Manual review needed"
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "sam", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_fcc(page, entity, out_dir, search_name, variants):
    """
    FCC Covered List 校验：直接访问 FCC 页面，等待完整加载后搜索文本。
    类似手动 Ctrl+F 搜索。
    """
    result = {"source": "fcc", "status": "error", "screenshots": [], "searched": search_name}

    # 用 _safe_goto 加载页面（domcontentloaded）
    if not _safe_goto(page, SOURCES["fcc"]["url"], timeout=60000):
        return result

    # FCC 页面加载较慢，等待更长时间让内容完全渲染
    print("  [INFO] Waiting for FCC page to fully render...")
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    page.wait_for_timeout(5000)
    _dismiss_cookie_banner(page)

    # 截图
    s1 = _screenshot(page, out_dir, "fcc", search_name, "page", timeout=60000)
    if s1:
        result["screenshots"].append(s1)

    # 提取页面文本（类似 Ctrl+F 搜索）
    text = _extract_text(page, max_chars=80000)
    print(f"  [INFO] FCC page text length: {len(text)} chars")

    # 用所有英文变体检查页面文本
    found_variant = None
    for v in variants:
        if not re.search(r'[\u4e00-\u9fff]', v) and v.lower() in text.lower():
            found_variant = v
            break

    if found_variant:
        result["status"] = "hit"
        result["detail"] = f"Found '{found_variant}' in FCC Covered List"
        # 高亮匹配文本并截图
        hl = _highlight_text(page, found_variant, out_dir, "fcc", search_name, "highlight")
        if hl:
            result["screenshots"].append(hl)
    elif len(text) > 1000:
        result["status"] = "clean"
        result["detail"] = f"Not found (page loaded, {len(text)} chars)"
    else:
        result["status"] = "manual_review"
        result["detail"] = f"Limited content ({len(text)} chars)"
    # 兜底截图
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "fcc", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_uk(page, entity, out_dir, search_name, variants):
    """
    UK 制裁名单校验：搜索 + 勾选 fuzzy match + 翻页截全部页面 + 智能判定。
    UK GOV.UK Sanctions List 搜索结果有分页，每页约20条。
    """
    result = {"source": "uk", "status": "error", "screenshots": [], "searched": search_name}
    if not _safe_goto(page, SOURCES["uk"]["url"]):
        return result

    # 填写搜索框
    for sel in ["#search", "input[name='search']", "input[type='search']", "input[type='text']"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _human_delay()
                el.fill(search_name)
                _human_delay(300, 500)
                break
        except Exception:
            continue

    # 勾选 fuzzy match 复选框
    try:
        fuzzy_selectors = [
            "input[name*='fuzzy']", "input[id*='fuzzy']",
            "input[type='checkbox'][name*='match']",
            "label:has-text('Fuzzy') input[type='checkbox']",
            "label:has-text('fuzzy') input[type='checkbox']",
        ]
        for sel in fuzzy_selectors:
            try:
                cb = page.query_selector(sel)
                if cb:
                    if not cb.is_checked():
                        cb.check()
                        print("  [INFO] Fuzzy match checkbox checked")
                    break
            except Exception:
                continue
    except Exception:
        pass

    # 提交搜索
    page.keyboard.press("Enter")
    _smart_wait(page, source_key="uk", timeout=10000)

    # 提取结果计数
    text = _extract_text(page, max_chars=8000)
    import re as _re_uk
    result_count = 0

    # UK GOV.UK 结果页面通常显示 "X results" 或 "showing X results"
    count_match = _re_uk.search(r'(\d+)\s*results?', text, _re_uk.IGNORECASE)
    if count_match:
        result_count = int(count_match.group(1))
        print(f"  [INFO] UK Sanctions results: {result_count}")

    # 第1页截图
    s1 = _screenshot(page, out_dir, "uk", search_name, "page1")
    if s1:
        result["screenshots"].append(s1)

    # 高亮匹配文本
    hl = _highlight_text(page, search_name, out_dir, "uk", search_name, "highlight")
    if hl:
        result["screenshots"].append(hl)

    # 翻页截图（GOV.UK 分页通常用 .govuk-pagination__next 或 a[rel='next']）
    if result_count > 0:
        page_num = 2
        max_pages = 15
        while page_num <= max_pages:
            try:
                # GOV.UK 分页按钮
                next_selectors = [
                    "a.govuk-pagination__link[rel='next']",
                    ".govuk-pagination__next a",
                    "a[rel='next']",
                    ".pagination a.next",
                    "a:has-text('Next')",
                ]
                next_link = None
                for sel in next_selectors:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            next_link = el
                            break
                    except Exception:
                        continue

                if not next_link:
                    print(f"  [INFO] UK Sanctions: reached last page (page {page_num - 1})")
                    break

                next_link.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                next_link.click()
                page.wait_for_timeout(3000)

                sp = _screenshot(page, out_dir, "uk", search_name, f"page{page_num}")
                if sp:
                    result["screenshots"].append(sp)
                print(f"  [SCREENSHOT] UK page {page_num}")
                page_num += 1
            except Exception as e:
                print(f"  [INFO] UK Sanctions pagination stopped at page {page_num}: {e}")
                break

    # 智能判定状态
    # ⚠️ 关键区分：「结果数解析成功且为 0」≠「结果数没解析出来」。
    # 原实现 result_count 初值 0，正则未匹配时保持 0，被 `or result_count == 0`
    # 直接判 clean —— 页面改版或加载失败时会静默产生**假阴性**。
    text_lower = text.lower()
    explicit_no_result = ("no results" in text_lower) or ("0 results" in text_lower)
    count_parsed = count_match is not None

    if explicit_no_result:
        result["status"] = "clean"
        result["detail"] = "No match found（页面明确显示无结果）"
    elif not count_parsed:
        result["status"] = "manual_review"
        result["detail"] = (
            "UK 结果页未能解析出结果计数（未匹配到 'N results' 模式），"
            "无法区分「确实无结果」与「页面结构变更 / 加载失败 / 被拦截」。"
            "**不得据此判定未命中**，须人工打开 UK Sanctions List 核对。"
        )
        print("  [REVIEW] UK result count unparsed -> manual review")
    elif result_count > 0:
        # 有结果，检查实体名是否出现在结果中
        matched_variant = None
        for v in variants:
            if re.search(r'[\u4e00-\u9fff]', v):
                continue  # 跳过中文变体
            if v.lower() in text_lower:
                matched_variant = v
                break
        if matched_variant:
            result["status"] = "hit"
            result["detail"] = f"Match found: {matched_variant} ({result_count} results)"
        else:
            # 有结果但名称不完全匹配（fuzzy match 结果），给出结果数让 LLM 判断
            result["status"] = "hit"
            result["detail"] = f"{result_count} results found (fuzzy match enabled, review recommended)"
    else:
        # 结果数解析成功且明确为 0 —— 此时判 clean 才是安全的
        result["status"] = "clean"
        result["detail"] = "No match found（结果计数解析成功且为 0）"

    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "uk", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_un(page, entity, out_dir, search_name, variants):
    """
    UN 安理会制裁搜索：填写搜索框 → 点击 SEARCH 按钮 → 等待加载完成。
    直接使用干净 context（不带 Sec-Fetch headers 和 stealth JS）。
    原因：主 context 的 extra_http_headers 会把 Sec-Fetch-Dest: document
    强加到 SPA 的 AJAX 调用上，导致 UN 服务器拒绝/忽略 API 请求。
    """
    result = {"source": "un", "status": "error", "screenshots": [], "searched": search_name}
    un_search_url = SOURCES["un"]["url"]

    def _un_search_on_page(p):
        """在指定 page 上执行 UN 搜索，返回是否加载成功"""
        p.wait_for_timeout(5000)
        _dismiss_cookie_banner(p)

        search_input = p.query_selector("input[placeholder*='Search']") or p.query_selector("input[type='text']")
        if not (search_input and search_input.is_visible()):
            print("  [INFO] UN search: no search box found")
            return False

        search_input.click()
        _human_delay()
        search_input.fill("")
        p.wait_for_timeout(300)
        search_input.type(search_name, delay=50)
        _human_delay(500, 800)

        # JS 额外触发 input 事件
        p.evaluate("""
            () => {
                const input = document.querySelector("input[placeholder*='Search']") || document.querySelector("input[type='text']");
                if (input) {
                    input.dispatchEvent(new Event('input', {bubbles: true}));
                    input.dispatchEvent(new Event('change', {bubbles: true}));
                    input.dispatchEvent(new Event('keyup', {bubbles: true}));
                }
            }
        """)
        p.wait_for_timeout(1000)

        search_btn = p.query_selector(".search-btn") or p.query_selector("button:has-text('SEARCH')")
        if search_btn:
            for i in range(10):
                if search_btn.is_enabled():
                    break
                p.wait_for_timeout(500)
            search_btn.click()
            print(f"  [INFO] UN search: clicked SEARCH button")
        else:
            search_input.press("Enter")
            print(f"  [INFO] UN search: pressed Enter")

        # 等 Loading 消失
        print("  [INFO] Waiting for UN search results to load...")
        p.wait_for_timeout(2000)
        for wait_round in range(10):  # 最多等 30 秒
            p.wait_for_timeout(3000)
            body_text = p.inner_text("body")[:1000]
            has_loading = "loading" in body_text.lower()
            has_results = "no results" in body_text.lower() or "total:" in body_text.lower()
            if not has_loading or has_results:
                print(f"  [INFO] UN results loaded after ~{(wait_round + 1) * 3 + 2}s")
                return True
        return False  # 超时

    # 直接用干净 context 搜索（绕过主 context 的 Sec-Fetch headers 干扰）
    un_loaded = False
    un_load_error = ""
    try:
        un_browser = page.context.browser
        fresh_context = un_browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            ignore_https_errors=True,
        )
        fresh_page = fresh_context.new_page()
        fresh_page.goto(un_search_url, timeout=30000)
        un_loaded = _un_search_on_page(fresh_page)

        if un_loaded:
            fresh_page.wait_for_timeout(2000)

        s1 = _screenshot(fresh_page, out_dir, "un", search_name, "results")
        if s1:
            result["screenshots"].append(s1)

        text = _extract_text(fresh_page, max_chars=30000)
        fresh_context.close()
    except Exception as e:
        print(f"  [ERROR] UN fresh context failed: {e}, falling back to main page")
        un_load_error = str(e)
        # 兜底：用主 page
        if not _safe_goto(page, un_search_url, timeout=30000):
            result["status"] = "manual_review"
            result["detail"] = (
                "UN 检索页无法打开（独立 context 与主 page 均失败），未取得任何结果，"
                "须人工打开 UN 名单核对。原始错误：" + un_load_error
            )
            return result
        un_loaded = _un_search_on_page(page)
        s1 = _screenshot(page, out_dir, "un", search_name, "results")
        if s1:
            result["screenshots"].append(s1)
        text = _extract_text(page, max_chars=30000)

    # ── 判定前置保护：未加载 / 正文过短，都不得判 clean ──
    # 原实现此处直接 else → clean，导致 SPA 未加载、结果表为空时静默产生**假阴性**。
    # UN 是最权威清单之一，此处原本反而最不设防，优先修补。
    MIN_UN_TEXT = 1000
    text_l = (text or "").lower()

    if not un_loaded:
        result["status"] = "manual_review"
        result["detail"] = (
            "UN 检索结果未确认加载（结果区持续 loading 或等待超时）。"
            "**未加载不等于未命中**，须人工打开 UN 名单核对。"
            + ((" 原始错误：" + un_load_error) if un_load_error else "")
        )
        print("  [REVIEW] UN results not confirmed loaded -> manual review")
    elif len(text or "") < MIN_UN_TEXT:
        result["status"] = "manual_review"
        result["detail"] = (
            f"UN 结果页正文仅 {len(text or '')} 字符（低于 {MIN_UN_TEXT} 阈值），疑似未完整渲染。"
            "**不得据此判定未命中**，须人工核对。"
        )
        print("  [REVIEW] UN result page text too short -> manual review")
    elif "no results" in text_l or "no match" in text_l or "total: 0" in text_l or "0 results" in text_l:
        result["status"] = "clean"
        result["detail"] = "No match found"
    elif any(v.lower() in text_l for v in variants if not re.search(r'[\u4e00-\u9fff]', v)):
        result["status"] = "hit"
        result["detail"] = "Match found"
        hl = _highlight_text(page, search_name, out_dir, "un", search_name, "highlight")
        if hl:
            result["screenshots"].append(hl)
    else:
        # 结果已加载且正文充足、仅是变体均未命中 —— 此时判 clean 才是安全的
        result["status"] = "clean"
        result["detail"] = "No match found in search results"
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "un", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_eu(page, entity, out_dir, search_name, variants):
    """
    EU Sanctions Map 校验：搜索 → 点击 List 列回形针图标 → 确保 Lists tab 勾选 → 逐页截图。
    EU Sanctions Map 是 Angular SPA，DOM 不是标准 table 而是 div 布局。
    关键交互：
    1. 搜索结果页用 a.icon-card.gray 定位回形针图标（每行 3 个，每 3 个取第 1 个 = List 列）
    2. 点击回形针进入详情页面
    3. 仅确保 "Lists of persons, entities and items"（input#dt0）处于勾选状态
    4. 逐页 PageDown 截图（最多 10 页）
    5. 返回搜索结果：a.close.hidden-print 或 go_back()
    """
    result = {"source": "eu", "status": "error", "screenshots": [], "searched": search_name}

    # 构造带搜索参数的 hash URL
    import urllib.parse
    search_json = json.dumps({
        "value": search_name,
        "searchType": {"id": 1, "title": "regimes, persons, entities"}
    })
    eu_url = f"https://www.sanctionsmap.eu/#/main?search={urllib.parse.quote(search_json)}"
    print(f"  [INFO] EU Sanctions Map URL: {eu_url[:100]}...")

    if not _safe_goto(page, eu_url, timeout=45000):
        return result

    # SPA 加载需要时间
    page.wait_for_timeout(8000)
    _dismiss_cookie_banner(page)

    s1 = _screenshot(page, out_dir, "eu", search_name, "results")
    if s1:
        result["screenshots"].append(s1)

    text = _extract_text(page, max_chars=10000)
    has_results = "no results found" not in text.lower() and "no results" not in text.lower()

    if not has_results:
        result["status"] = "clean"
        result["detail"] = "No results found in EU Sanctions Map"
    else:
        # EU 搜索返回的是 regime 列表（如 Central African Republic），
        # 其文本通常不包含搜索实体名（如 DJI），所以不依赖文本匹配判断 hit/clean。
        # 只要有搜索结果就标记为 hit，由 LLM 阶段 3.5 做误中判断。
        result["status"] = "hit"
        result["detail"] = "Search returned results in EU Sanctions Map — requires LLM assessment"

        # 尝试高亮
        hl = _highlight_text(page, search_name, out_dir, "eu", search_name, "highlight")
        if hl:
            result["screenshots"].append(hl)

        # =====================================================
        # EU 详情交互：点击每行 List 列的回形针图标 → 进入详情页 → 勾选 tab → 逐页截图
        # DOM 结构（经浏览器验证）：
        #   - 不是标准 <table>，而是 div 布局（Angular SPA）
        #   - 回形针图标：<a class="icon-card gray" href="javascript:;"><i class="icon-clip"></i></a>
        #   - 每行有 3 个 a.icon-card（List / Legal acts / Guidelines），第 0 个是 List
        # =====================================================
        try:
            # 获取所有回形针图标
            all_icons = page.query_selector_all('a.icon-card.gray')
            if not all_icons:
                all_icons = page.query_selector_all('a.icon-card')

            # 每行有 3 个图标（List, Legal, Guidelines），List 是每组的第 1 个
            # 所以 List 图标的索引是 0, 3, 6, 9, ...
            list_icons = [all_icons[i] for i in range(0, len(all_icons), 3) if i < len(all_icons)]
            print(f"  [EU] Found {len(all_icons)} total icons, {len(list_icons)} List icons (every 3rd)")

            detail_count = 0
            for idx, icon in enumerate(list_icons[:5]):  # 最多处理 5 个 regime
                try:
                    print(f"  [EU] Clicking List icon (paperclip) for row {idx+1}...")
                    icon.click()

                    # 等待详情页面加载（SPA 页面过渡需较长时间）
                    page.wait_for_timeout(8000)

                    # 确保只勾选 Lists tab（dt0），不碰 Legal acts（dt1）和 Guidelines（dt3）
                    # dt0 可能默认已勾选，点击会取消勾选，所以先检查状态
                    try:
                        dt0_checked = page.evaluate('document.getElementById("dt0") ? document.getElementById("dt0").checked : false')
                        if not dt0_checked:
                            dt0_label = page.query_selector('label[for="dt0"]')
                            if dt0_label:
                                dt0_label.click()
                                _human_delay(300, 500)
                                print(f"  [EU] Checked Lists tab (dt0)")
                        else:
                            print(f"  [EU] Lists tab (dt0) already checked")
                    except Exception:
                        # 备选：直接用 JS 勾选
                        page.evaluate('var cb = document.getElementById("dt0"); if(cb && !cb.checked) cb.click();')

                    # 等待 tab 内容加载完成
                    page.wait_for_timeout(3000)

                    # 截图详情页面（逐页滚动截取完整内容）
                    page.evaluate("window.scrollTo(0, 0)")
                    page.wait_for_timeout(500)

                    page_screenshots = 0
                    prev_scroll = -1
                    for scroll_idx in range(10):  # 最多截 10 页
                        s = _screenshot(page, out_dir, "eu", search_name, f"detail_{idx+1}_p{scroll_idx+1}")
                        if s:
                            result["screenshots"].append(s)
                            page_screenshots += 1
                            if scroll_idx == 0:
                                detail_count += 1

                        cur_scroll = page.evaluate("window.scrollY")
                        max_scroll = page.evaluate("document.body.scrollHeight - window.innerHeight")
                        if cur_scroll >= max_scroll - 10:
                            break
                        if cur_scroll == prev_scroll:
                            break
                        prev_scroll = cur_scroll

                        page.keyboard.press("PageDown")
                        page.wait_for_timeout(1000)

                    print(f"  [EU] Detail {idx+1}: captured {page_screenshots} page screenshots")

                    # 返回搜索结果页面
                    close_btn = page.query_selector('a.close.hidden-print')
                    if close_btn:
                        close_btn.click()
                        page.wait_for_timeout(2000)
                    else:
                        page.go_back()
                        page.wait_for_timeout(3000)

                except Exception as e:
                    print(f"  [EU] Detail view for row {idx+1} failed: {e}")
                    try:
                        page.go_back()
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

            if detail_count > 0:
                print(f"  [EU] Captured {detail_count} regime detail views")
            else:
                print(f"  [EU] No detail views captured, using search results screenshot")

        except Exception as e:
            print(f"  [EU] Detail capture failed: {e}")

    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "eu", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_au(page, entity, out_dir, search_name, variants):
    """
    Australia DFAT 校验：下载官方 XLSX + pandas 分析 + 截图分析结果。
    """
    result = {"source": "au", "status": "error", "screenshots": [], "searched": search_name}

    import urllib.request
    import tempfile

    xlsx_url = "https://www.dfat.gov.au/sites/default/files/Australian_Sanctions_Consolidated_List.xlsx"
    print(f"  [INFO] Downloading DFAT XLSX...")

    try:
        req = urllib.request.Request(xlsx_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        # 保存到输出目录（永久保留作为证据）
        xlsx_local = os.path.join(out_dir, "Australian_Sanctions_Consolidated_List.xlsx")
        with open(xlsx_local, "wb") as f:
            with urllib.request.urlopen(req, timeout=30) as resp:
                f.write(resp.read())
        tmp_path = xlsx_local

        print(f"  [INFO] XLSX downloaded → {xlsx_local}")

        try:
            import pandas as pd
            df_all = pd.read_excel(tmp_path, sheet_name=None)
            found_variant = None
            found_sheet = None
            matching_df = None

            for sheet_name, sheet_df in df_all.items():
                sheet_str = sheet_df.to_string().lower()
                for v in variants:
                    if not re.search(r'[\u4e00-\u9fff]', v) and v.lower() in sheet_str:
                        found_variant = v
                        found_sheet = sheet_name
                        # 提取匹配行
                        mask = sheet_df.apply(lambda row: v.lower() in str(row).lower(), axis=1)
                        matching_df = sheet_df[mask]
                        break
                if found_variant:
                    break

            if found_variant and matching_df is not None and len(matching_df) > 0:
                result["status"] = "hit"
                result["detail"] = f"Found '{found_variant}' in DFAT XLSX (sheet: {found_sheet}, {len(matching_df)} rows)"
                print(f"  [HIT] Found '{found_variant}' in sheet '{found_sheet}' ({len(matching_df)} rows)")

                # 截图 pandas 分析结果：逐行生成聚焦的高亮截图
                try:
                    # 高亮匹配文本（大小写不敏感替换）
                    import re as _re_au
                    def _highlight_variant(html_str, variant):
                        """大小写不敏感地高亮所有匹配文本"""
                        pattern = _re_au.compile(_re_au.escape(variant), _re_au.IGNORECASE)
                        return pattern.sub(lambda m: f'<mark style="background:#FFFF00;border:2px solid red;padding:2px 4px;font-weight:bold;">{m.group()}</mark>', html_str)

                    # 每条匹配行单独生成截图（聚焦视图）
                    for row_idx, (_, row) in enumerate(matching_df.iterrows()):
                        if row_idx >= 5:  # 最多截 5 条匹配行
                            break
                        row_df = pd.DataFrame([row])
                        row_html = row_df.to_html(index=False, escape=False)
                        row_html = _highlight_variant(row_html, found_variant)
                        # 添加匹配行标题
                        row_html = f'<h3 style="color:#c00000;">🔴 Match {row_idx+1} of {len(matching_df)}: \'{found_variant}\' found in sheet \'{found_sheet}\'</h3>' + row_html
                        s_row = _save_pandas_screenshot(
                            page, row_html, out_dir, "au", search_name, f"hit_row_{row_idx+1}"
                        )
                        if s_row:
                            result["screenshots"].append(s_row)
                            print(f"  [SCREENSHOT] AU match row {row_idx+1} captured")

                    # 同时保留完整匹配表截图（带高亮）
                    styled_html = matching_df.to_html(index=False, escape=False)
                    styled_html = _highlight_variant(styled_html, found_variant)
                    styled_html = f'<h3>All {len(matching_df)} matching rows for \'{found_variant}\' in sheet \'{found_sheet}\'</h3>' + styled_html
                    s_pandas = _save_pandas_screenshot(
                        page, styled_html, out_dir, "au", search_name, "pandas_hit_all"
                    )
                    if s_pandas:
                        result["screenshots"].append(s_pandas)
                except Exception as e:
                    print(f"  [WARN] Pandas screenshot failed: {e}")
            else:
                result["status"] = "clean"
                result["detail"] = "Searched all sheets in DFAT XLSX, no match"
                print(f"  [CLEAN] Not found in DFAT XLSX")

            # 无论命中或未命中，都生成 pandas 分析摘要截图作为证据
            try:
                summary_rows = []
                for sn, sdf in df_all.items():
                    summary_rows.append({
                        "Sheet": sn,
                        "Rows": len(sdf),
                        "Columns": len(sdf.columns),
                        "Searched": ", ".join([v for v in variants if not re.search(r'[\u4e00-\u9fff]', v)]),
                        "Match": "✅ HIT" if (found_sheet and sn == found_sheet) else "❌ Clean",
                    })
                import pandas as pd
                summary_df = pd.DataFrame(summary_rows)
                summary_html = summary_df.to_html(index=False, escape=False)
                # 添加本地 XLSX 文件链接和源 URL
                xlsx_abs = os.path.abspath(xlsx_local).replace(os.sep, '/')
                summary_html += f"<br><p class='source'>📁 Local file: <a href='file:///{xlsx_abs}'>{os.path.basename(xlsx_local)}</a></p>"
                summary_html += f"<p class='source'>🌐 Source URL: <a href='{xlsx_url}'>{xlsx_url}</a></p>"
                if found_variant:
                    summary_html += f"<br><p style='color:red;font-weight:bold;'>🔍 Match found: '{found_variant}' in sheet '{found_sheet}'</p>"
                else:
                    summary_html += f"<br><p style='color:green;font-weight:bold;'>✅ No match found for any search variant</p>"
                s_summary = _save_pandas_screenshot(
                    page, summary_html, out_dir, "au", search_name, "pandas_summary"
                )
                if s_summary:
                    result["screenshots"].append(s_summary)
            except Exception as e:
                print(f"  [WARN] Pandas summary screenshot failed: {e}")

        except ImportError:
            result["status"] = "manual_review"
            result["detail"] = "XLSX downloaded but pandas not installed (pip install pandas openpyxl)"
        except Exception as e:
            result["status"] = "manual_review"
            result["detail"] = f"XLSX analysis error: {e}"
        finally:
            pass  # XLSX 保留在 out_dir 作为证据

    except Exception as e:
        result["status"] = "error"
        result["detail"] = f"XLSX download failed: {e}"
        print(f"  [ERROR] XLSX download failed: {e}")

    # 浏览器访问 DFAT 页面截图作为证据
    try:
        if _safe_goto(page, SOURCES["au"]["url"], timeout=30000):
            page.wait_for_timeout(3000)
            s1 = _screenshot(page, out_dir, "au", search_name, "page")
            if s1:
                result["screenshots"].append(s1)
    except Exception:
        pass

    # 兜底截图
    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "au", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


def verify_ca(page, entity, out_dir, search_name, variants):
    """
    Canada SEMA 制裁名单校验：使用 DataTable "Filter items" 输入框过滤。
    关键：页面有两个 input[type='search']，顶部是 Canada.ca 搜索栏，
    DataTable 的过滤框需要用 aria-controls='sanctions-table' 精确匹配。
    """
    result = {"source": "ca", "status": "error", "screenshots": [], "searched": search_name}
    if not _safe_goto(page, SOURCES["ca"]["url"], timeout=45000):
        return result

    # 等待 DataTable 初始化（5378 条数据，需要较长时间）
    page.wait_for_timeout(8000)
    _dismiss_cookie_banner(page)

    # 精确定位 DataTable 的 Filter items 输入框
    filter_filled = False
    # 按优先级排列：最精确的 aria-controls 选择器在最前面
    filter_selectors = [
        "input[aria-controls='sanctions-table']",  # 精确匹配 DataTable filter
        "#sanctions-table_filter input",
        ".dataTables_filter input",
    ]
    for sel in filter_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                # 滚动到 filter 框位置
                el.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                el.click()
                _human_delay()
                el.fill(search_name)
                _human_delay(500, 800)
                filter_filled = True
                print(f"  [INFO] CA filter filled via {sel}")
                break
        except Exception:
            continue

    if not filter_filled:
        print("  [WARN] CA filter box not found, trying JS fallback")
        # JS 兜底：直接用 aria-controls 查找
        # 用 page.evaluate 的参数传递替代字符串插值——search_name 里出现
        # 单引号/反斜杠/换行（如 "O'Brien"）会直接破坏 JS 语法并被静默吞掉。
        try:
            page.evaluate(
                """(val) => {
                    const input = document.querySelector("input[aria-controls='sanctions-table']");
                    if (input) {
                        input.scrollIntoView({behavior: 'smooth', block: 'center'});
                        input.focus();
                        input.value = val;
                        input.dispatchEvent(new Event('input', {bubbles: true}));
                        input.dispatchEvent(new Event('keyup', {bubbles: true}));
                    }
                }""",
                search_name,
            )
            page.wait_for_timeout(2000)
            # 参数化不等于一定成功：需回读输入框确认值确实落进去了
            try:
                actual = page.evaluate(
                    """() => {
                        const i = document.querySelector("input[aria-controls='sanctions-table']");
                        return i ? i.value : null;
                    }"""
                )
            except Exception:
                actual = None
            if actual and str(actual).strip() == str(search_name).strip():
                filter_filled = True
                print("  [INFO] CA filter filled via JS fallback")
            else:
                result["status"] = "manual_review"
                result["detail"] = (
                    "CA 检索框填写失败（JS 兜底后回读值与目标不一致），未能执行检索。"
                    "须人工在加拿大 SEMA 名单核对。"
                )
                print(f"  [REVIEW] CA JS fallback value mismatch: {actual!r} != {search_name!r}")
        except Exception as e:
            print(f"  [WARN] CA JS fallback failed: {e}")
            result["status"] = "manual_review"
            result["detail"] = f"CA 检索框 JS 兜底执行异常，未能执行检索，须人工核对。原始错误：{e}"

    # 等待 DataTable 过滤完成
    page.wait_for_timeout(3000)

    # 截图过滤结果
    s1 = _screenshot(page, out_dir, "ca", search_name, "filtered")
    if s1:
        result["screenshots"].append(s1)

    # 提取过滤后的文本和条目计数
    text = _extract_text(page, max_chars=30000)

    # DataTable 过滤后显示 "Showing X to Y of Z entries (filtered from N total entries)"
    import re as _re_ca
    # 检查 "0 of 0 entries" 或 "no matching records"
    zero_match = _re_ca.search(r'showing\s+0\s+to\s+0\s+of\s+0\s+entries', text, _re_ca.IGNORECASE)
    filtered_match = _re_ca.search(r'showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s+entries', text, _re_ca.IGNORECASE)
    no_entries = "no matching records" in text.lower() or zero_match

    if no_entries:
        result["status"] = "clean"
        result["detail"] = "No match found in Canadian sanctions list"
    elif filtered_match:
        filtered_count = int(filtered_match.group(1))
        if filtered_count > 0:
            matched_variant = None
            for v in variants:
                if re.search(r'[\u4e00-\u9fff]', v):
                    continue
                if v.lower() in text.lower():
                    matched_variant = v
                    break
            if matched_variant:
                result["status"] = "hit"
                result["detail"] = f"Match found: {matched_variant} ({filtered_count} entries)"
            else:
                result["status"] = "hit"
                result["detail"] = f"{filtered_count} filtered entries found (review recommended)"
            hl = _highlight_text(page, search_name, out_dir, "ca", search_name, "highlight")
            if hl:
                result["screenshots"].append(hl)
        else:
            result["status"] = "clean"
            result["detail"] = "No match found"
    else:
        if any(v.lower() in text.lower() for v in variants if not re.search(r'[\u4e00-\u9fff]', v)):
            result["status"] = "hit"
            result["detail"] = "Found on page"
        else:
            result["status"] = "clean"
            result["detail"] = "No match found"

    if not any(result["screenshots"]):
        fb = _screenshot(page, out_dir, "ca", search_name, "fallback")
        if fb:
            result["screenshots"].append(fb)
    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


# 注意：签名改为接收 search_name 和 variants

# ============================================================
# 中文清单适配层（本 fork 新增）
# ============================================================

def match_variants_cn(text, variants, prefer_chinese=True):
    """
    名称变体匹配 —— 中文清单专用。

    上游实现在 verify_uk / verify_un / verify_ca / verify_sam 等多处以
        `if not re.search(r'[\\u4e00-\\u9fff]', v): continue  # 跳过中文变体`
    显式丢弃中文名称变体，其隐含前提是「被检索的名单页为英文」。
    当检索对象为中国官方公告时该前提不成立：中文变体反而是最可靠的匹配锚点。
    本函数补上这一层，并按「中文优先、英文兜底」排序。
    """
    cn = [v for v in variants if v and re.search(r'[\u4e00-\u9fff]', v)]
    en = [v for v in variants if v and not re.search(r'[\u4e00-\u9fff]', v)]
    ordered = (cn + en) if prefer_chinese else (en + cn)
    for v in ordered:
        if v.lower() in text.lower():
            return v
    return None


def collect_cn_list_links(page, base_url, keywords, limit=5):
    """
    在中国官方栏目页中筛出标题命中关键词的公告链接。

    中国管制清单以「商务部公告 / 商务部令」形式逐次发布，
    不存在可供直接查询的统一数据库，因此需先定位相关公告，再进正文比对。
    """
    from urllib.parse import urljoin

    found = []
    try:
        anchors = page.query_selector_all("a")
    except Exception:
        return found

    for a in anchors:
        try:
            title = (a.inner_text() or "").strip()
            href = a.get_attribute("href") or ""
        except Exception:
            continue
        if not title or not href:
            continue
        if any(k in title for k in keywords):
            item = {"title": title, "url": urljoin(base_url, href)}
            if item not in found:
                found.append(item)
        if len(found) >= limit:
            break
    return found


def load_cn_index(page, url):
    """
    加载中国官方栏目页。

    政务站点普遍存在访问节流与首屏渲染延迟，直接套用国际站点的等待节奏
    容易拿到未渲染完成的 DOM，因此这里单独设置「多次等待 + 重试」策略。
    """
    if not _safe_goto(page, url, timeout=60000):
        return False
    try:
        page.wait_for_load_state("domcontentloaded", timeout=25000)
    except Exception:
        pass
    page.wait_for_timeout(2500)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)
    try:
        _dismiss_cookie_banner(page)
    except Exception:
        pass
    return True


def verify_cn_list(page, source_key, entity, out_dir, search_name, variants, max_docs=12):
    """通用中国清单核验入口：栏目列表 → 公告正文 → 全文比对 → 截图留痕。"""
    info = SOURCES[source_key]
    keywords = CN_LIST_KEYWORDS.get(source_key, [])
    result = {"source": source_key, "status": "error", "screenshots": [], "searched": search_name}

    # 主入口 + 兜底入口（栏目改版时上层栏目页仍可能包含入口）
    candidates = [info["url"]]
    fallback = CN_FALLBACK_URLS.get(source_key)
    if fallback and fallback != info["url"]:
        candidates.append(fallback)

    docs = []
    for ci, c_url in enumerate(candidates):
        for retry in range(2):
            if not load_cn_index(page, c_url):
                continue
            s0 = _screenshot(page, out_dir, source_key, search_name, f"index{ci + 1}")
            if s0:
                result["screenshots"].append(s0)
            docs = collect_cn_list_links(page, c_url, keywords, limit=max_docs)
            if docs:
                break
            print(f"  [{source_key}] 第 {retry + 1} 次未在列表中命中，重试")
            _human_delay(1200, 2500)
        if docs:
            break

    if not docs:
        result["status"] = "manual_review"
        result["detail"] = (
            f"栏目页未检索到标题含 {keywords} 的公告（已试 {len(candidates)} 个人口、各 2 次）；"
            f"中国清单以公告形式发布且不提供统一查询库，需人工复核，不得据此认定「未列名」"
        )
        result["screenshots"] = [s for s in result["screenshots"] if s]
        return result

    checked = 0
    hits = []
    for idx, doc in enumerate(docs, 1):
        print(f"  [{source_key}] ({idx}/{len(docs)}) {doc['title'][:36]}")
        if not _safe_goto(page, doc["url"], timeout=60000):
            continue
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        page.wait_for_timeout(1500)
        checked += 1

        text = _extract_text(page, max_chars=120000)
        matched = match_variants_cn(text, variants, prefer_chinese=True)
        if matched:
            hits.append({"title": doc["title"][:40], "variant": matched, "url": doc["url"]})
            hl = _highlight_text(page, matched, out_dir, source_key, search_name, f"hit{idx}")
            if hl:
                result["screenshots"].append(hl)
        else:
            s = _screenshot(page, out_dir, source_key, search_name, f"doc{idx}")
            if s:
                result["screenshots"].append(s)

    total_docs = len(docs)
    coverage = (checked / total_docs) if total_docs else 0.0
    result["coverage"] = {
        "total_docs": total_docs,
        "checked_docs": checked,
        "coverage_pct": round(coverage * 100, 1),
    }

    if hits:
        first = hits[0]
        result["status"] = "hit"
        result["detail"] = (
            f"命中 {len(hits)} 处；如《{first['title']}》以「{first['variant']}」列名"
            + (f"（已核 {checked}/{total_docs} 份公告）" if total_docs > checked else "")
        )
        result["hits"] = hits
    elif checked == 0:
        result["status"] = "manual_review"
        result["detail"] = f"检索到 {total_docs} 份相关公告但均未能打开"
    elif coverage < CN_MIN_COVERAGE:
        # 部分公告打不开时判 clean = 把「没查」当成「查了没有」——
        # 未覆盖的公告等于未核查，必须显式披露并转人工补核。
        result["status"] = "manual_review"
        result["detail"] = (
            f"仅成功打开 {checked}/{total_docs} 份相关公告（覆盖率 {coverage:.0%}，"
            f"低于 {CN_MIN_COVERAGE:.0%} 阈值）。未能打开的 {total_docs - checked} 份等于未核查，"
            f"**不得据此认定「未列名」**，须人工补核后重出结论。"
        )
        print(f"  [REVIEW] {source_key} coverage {coverage:.0%} < {CN_MIN_COVERAGE:.0%} -> manual review")
    else:
        result["status"] = "clean"
        result["detail"] = f"已核 {checked}/{total_docs} 份相关公告（覆盖率 {coverage:.0%}），未命中"

    result["screenshots"] = [s for s in result["screenshots"] if s]
    return result


VERIFY_FUNCS = {
    "ofac": lambda p, e, t, o, sn, v: verify_ofac(p, e, t, o, sn, v),
    "bis_csl": lambda p, e, t, o, sn, v: verify_bis_csl(p, e, o, sn, v),
    "dod_1260h": lambda p, e, t, o, sn, v: verify_dod_1260h(p, e, o, sn, v),
    "sam": lambda p, e, t, o, sn, v: verify_sam(p, e, o, sn, v),
    "fcc": lambda p, e, t, o, sn, v: verify_fcc(p, e, o, sn, v),
    "uk": lambda p, e, t, o, sn, v: verify_uk(p, e, o, sn, v),
    "un": lambda p, e, t, o, sn, v: verify_un(p, e, o, sn, v),
    "eu": lambda p, e, t, o, sn, v: verify_eu(p, e, o, sn, v),
    "au": lambda p, e, t, o, sn, v: verify_au(p, e, o, sn, v),
    "ca": lambda p, e, t, o, sn, v: verify_ca(p, e, o, sn, v),
    # 中国清单（本 fork 新增）
    "cn_control": lambda p, e, t, o, sn, v: verify_cn_list(p, "cn_control", e, o, sn, v),
    "cn_ucl": lambda p, e, t, o, sn, v: verify_cn_list(p, "cn_ucl", e, o, sn, v),
    "cn_counter": lambda p, e, t, o, sn, v: verify_cn_list(p, "cn_counter", e, o, sn, v),
}


def run_verification(entity, sources=None, entity_type="Entity", out_dir="screenshots",
                     headless=False, extra_variants=None, exact_name_only=False,
                     stealth=False):
    """
    stealth: 是否启用反检测伪装（UA 伪装 / 隐藏 automation 标记 / 手工 Sec-Fetch 头 /
             STEALTH_JS 注入）。**默认关闭**。
             ⚠️ 合规提示：这些手段作用于各国政府官网，可能违反其 Terms of Use
             或 robots 约定。是否启用须由法务评估后决定，不要在未确认前打开。
             关闭时被反爬拦截的源会落入 manual_review —— 这是预期行为，
             不得被当作「未命中」。
    """
    if sources is None:
        sources = ALL_SOURCES

    os.makedirs(out_dir, exist_ok=True)
    results = []
    failed_sources = []

    # 变体生成策略：
    # - 精确名称模式：只使用 entity 本身，不自动生成也不追加额外变体
    # - 如果用户通过 --extra-variants 指定了变体，只用 entity + extra_variants（用户明确的列表）
    # - 如果没有 extra_variants，才自动生成变体
    if exact_name_only:
        search_name = entity
        variants = [entity]
        print("  [VARIANTS] 精确名称模式 — 只搜索用户提供的单个名称")
    elif extra_variants:
        # 用户明确指定了变体列表 → 只用这些，不自动生成
        search_name = entity
        variants = [entity]
        for ev in extra_variants:
            ev = ev.strip()
            if ev and ev not in variants:
                variants.append(ev)
        print(f"  [VARIANTS] 用户指定模式 — 只使用指定的变体，跳过自动生成")
        print(f"  [VARIANTS] Final variants: {variants}")
    else:
        # 无用户指定 → 自动生成搜索变体
        search_name, variants = _get_search_names(entity)
        print(f"  [VARIANTS] 自动生成模式")

    print(f"\n{'='*60}")
    print(f"Official Sanctions Browser Verification (v2 Enhanced)")
    print(f"  Entity (original): {entity}")
    print(f"  Search name (auto): {search_name}")
    print(f"  Variants: {variants}")
    print(f"  Type: {entity_type}")
    print(f"  Sources: {len(sources)}")
    print(f"  Output: {os.path.abspath(out_dir)}")
    print(f"{'='*60}\n")

    # ── 反检测（stealth）开关，默认关闭 ──
    # stealth=True 时才会做四件事：①隐藏 automation 标记 ②UA 伪装
    # ③手工 Sec-Fetch-* 请求头 ④注入 STEALTH_JS。
    # 这四项都是"伪装"，作用于各国政府官网，可能违反其 ToS / robots 约定。
    if stealth:
        print("  [BROWSER] stealth 已启用：UA、automation 指纹、Sec-Fetch 请求头均被伪装。")
        print("           ⚠️ 请确认此举已获法务认可，且符合各目标站点的 Terms of Use。")
    else:
        print("  [BROWSER] stealth 未启用（默认，推荐）。")
        print("           如目标站点因反爬拦截，相关源会落入 manual_review ——")
        print("           这是预期行为，不得当作「未命中」。")

    with require_playwright()() as pw:
        # stealth 关闭时只保留无害的启动参数；AutomationControlled 属于伪装，纳入开关
        launch_args = [
            '--disable-features=VizDisplayCompositor',
            '--no-first-run',
            '--no-default-browser-check',
        ]
        if stealth:
            launch_args.insert(0, '--disable-blink-features=AutomationControlled')
        try:
            browser = pw.chromium.launch(
                headless=headless,
                channel="chrome",  # 使用系统 Chrome
                args=launch_args,
            )
            print("  [BROWSER] Using system Chrome (headed mode)")
        except Exception as e:
            print(f"  [BROWSER] System Chrome not available ({e}), using Chromium")
            browser = pw.chromium.launch(
                headless=headless,
                args=launch_args,
            )
        # locale / viewport 属常规上下文设置，不是伪装，默认保留；
        # UA、Sec-Fetch-* 头属指纹伪装，纳入 stealth 开关。
        context_kwargs = dict(
            viewport={"width": 1440, "height": 1080},
            locale="en-US",
            ignore_https_errors=True,
        )
        if stealth:
            context_kwargs["timezone_id"] = "America/New_York"
            context_kwargs["user_agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
            context_kwargs["extra_http_headers"] = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1",
            }
        context = browser.new_context(**context_kwargs)

        # 注入隐身脚本到每个新页面（仅 stealth 开启时）
        if stealth:
            context.add_init_script(STEALTH_JS)
        page = context.new_page()

        # 构建所有需要搜索的变体列表（去重，主搜索名优先）
        all_search_names = [search_name]
        for v in variants:
            if v not in all_search_names:
                all_search_names.append(v)
        print(f"\n  [MULTI-VARIANT] 将对每个源用以下 {len(all_search_names)} 个变体搜索：")
        for idx, sn in enumerate(all_search_names):
            print(f"    [{idx+1}] {sn}")
        print()

        for i, source_key in enumerate(sources, 1):
            source_info = SOURCES.get(source_key)
            if not source_info:
                print(f"  [WARN] Unknown source: {source_key}, skipping")
                continue

            print(f"[{i}/{len(sources)}] {source_info['flag']} {source_info['name']}...")

            verify_func = VERIFY_FUNCS.get(source_key)
            if not verify_func:
                continue

            # 对每个变体搜索此源，合并结果
            variant_results = []
            for vi, variant_name in enumerate(all_search_names):
                print(f"  [{vi+1}/{len(all_search_names)}] 搜索变体: \"{variant_name}\"")
                try:
                    vr = verify_func(page, entity, entity_type, out_dir, variant_name, variants)
                    vr["_searched_variant"] = variant_name
                    variant_results.append(vr)
                    status_label = {"clean": "[CLEAN]", "hit": "[HIT]",
                                    "manual_review": "[REVIEW]", "error": "[ERROR]"}
                    print(f"    {status_label.get(vr['status'], '[?]')} {vr.get('detail', '')}")
                except Exception as e:
                    print(f"    [ERROR] Exception with variant \"{variant_name}\": {e}")
                    variant_results.append({
                        "source": source_key, "status": "error",
                        "detail": f"variant={variant_name}: {e}",
                        "screenshots": [], "_searched_variant": variant_name,
                    })
                # 变体之间短延迟
                if vi < len(all_search_names) - 1:
                    _human_delay(300, 800)

            # 合并此源的所有变体结果：最严重状态优先，截图/匹配合并
            STATUS_PRIORITY = {"hit": 3, "manual_review": 2, "error": 1, "clean": 0}
            merged = {
                "source": source_key,
                "name": source_info["name"],
                "flag": source_info["flag"],
                "url": source_info["url"],
                "status": "clean",
                "detail": "",
                "screenshots": [],
                "searched": ", ".join(all_search_names),
            }
            details = []
            for vr in variant_results:
                v_name = vr.get("_searched_variant", "?")
                v_status = vr.get("status", "error")
                if STATUS_PRIORITY.get(v_status, 0) > STATUS_PRIORITY.get(merged["status"], 0):
                    merged["status"] = v_status
                # 收集截图（去重）
                for s in vr.get("screenshots", []):
                    if s and s not in merged["screenshots"]:
                        merged["screenshots"].append(s)
                # 收集 csv_matches
                if vr.get("csv_matches"):
                    if "csv_matches" not in merged:
                        merged["csv_matches"] = []
                    merged["csv_matches"].extend(vr["csv_matches"])
                # 收集详情
                if vr.get("detail"):
                    details.append(f"[{v_name}] {vr['detail']}")
            merged["detail"] = " | ".join(details) if details else "All variants clean"

            results.append(merged)

            has_valid = any(_check_screenshot_quality(s) for s in merged.get("screenshots", []))

            # ⚠️ 原实现只把「截图质量差」写进 failed_sources，
            # 而 status == "error" / "manual_review" 的源从不进这个清单——
            # 结果是「程序性失败」在下游看不到任何痕迹。
            # 这里改为：任何非 clean 的状态都必须被登记。
            if merged.get("status") == "error":
                failed_sources.append({"source": source_key, "name": source_info["name"],
                                       "reason": "source_error",
                                       "status": "error",
                                       "detail": merged.get("detail", "")})
            elif merged.get("status") == "manual_review":
                failed_sources.append({"source": source_key, "name": source_info["name"],
                                       "reason": "status_manual_review",
                                       "status": "manual_review",
                                       "detail": merged.get("detail", "")})
            elif not has_valid and merged.get("screenshots"):
                failed_sources.append({"source": source_key, "name": source_info["name"],
                                       "reason": "screenshot_quality_low",
                                       "status": merged.get("status", ""),
                                       "detail": merged.get("detail", "")})

            print(f"  [MERGED] {source_info['name']}: {merged['status'].upper()} ({len(merged['screenshots'])} screenshots)")

            # 源之间加随机延迟（模拟人类）
            if i < len(sources):
                _human_delay(500, 1500)

        browser.close()

    # 汇总
    print(f"\n{'='*60}")
    print(f"Verification Summary")
    print(f"{'='*60}")
    hits = [r for r in results if r["status"] == "hit"]
    cleans = [r for r in results if r["status"] == "clean"]
    reviews = [r for r in results if r["status"] == "manual_review"]
    errors = [r for r in results if r["status"] == "error"]

    if hits:
        print(f"  [HIT] ({len(hits)}): {', '.join(r['name'] for r in hits)}")
    if cleans:
        print(f"  [CLEAN] ({len(cleans)}): {', '.join(r['name'] for r in cleans)}")
    if reviews:
        print(f"  [REVIEW] ({len(reviews)}): {', '.join(r['name'] for r in reviews)}")
    if errors:
        print(f"  [ERROR] ({len(errors)}): {', '.join(r['name'] for r in errors)}")

    total_screenshots = sum(len(r.get("screenshots", [])) for r in results)
    print(f"\n  Total {total_screenshots} screenshots saved to: {os.path.abspath(out_dir)}")

    if failed_sources:
        failed_path = os.path.join(out_dir, "needs_manual_review.json")
        with open(failed_path, "w", encoding="utf-8") as f:
            json.dump(failed_sources, ensure_ascii=False, indent=2, fp=f)
        print(f"\n  ⚠️  {len(failed_sources)} source(s) need manual review: {failed_path}")

    return results


def format_results_markdown(entity, results, entity_type="Entity"):
    """
    生成麦肯锡/Big4 级别尽调风格的 Markdown 报告。
    每个名单包含：背景说明、法律含义、搜索方法、发现、结论 + 证据截图。
    """
    # 各制裁源的背景描述与法律含义
    SOURCE_CONTEXT = {
        "ofac": {
            "full_name": "美国财政部海外资产控制办公室特别指定国民清单 (OFAC SDN List)",
            "background": "OFAC SDN 清单是全球最具影响力的制裁名单之一，由美国财政部维护。被列入 SDN 清单意味着该实体在美国管辖范围内的所有资产将被冻结，且禁止美国人（包括公司）与其进行任何交易。",
            "legal": "违反 OFAC 制裁可能导致高达数百万美元的民事罚款和刑事处罚（含最高 20 年监禁）。根据《国际紧急经济权力法》(IEEPA) 和《贸易敌国法》执行。",
            "method": "通过 OFAC 官方在线搜索引擎（sanctionssearch.ofac.treas.gov）进行模糊匹配搜索，阈值设置为 70 分。",
        },
        "bis_csl": {
            "full_name": "美国商务部工业与安全局综合筛查清单 (BIS Consolidated Screening List)",
            "background": "CSL 整合了美国政府多个出口管制清单，包括实体清单(Entity List)、被拒绝人清单(Denied Persons List)、未经验证清单(Unverified List)等。被列入意味着向该实体出口受管控物项需要特殊许可证或被完全禁止。",
            "legal": "违反出口管制法规（EAR）可能导致每次违规高达 $300,000 的民事罚款，或交易金额的两倍（取较高者）。刑事处罚最高可达 $1,000,000 和 20 年监禁。",
            "method": "通过 trade.gov CSL 搜索引擎在线搜索 + 下载官方 CSV 数据文件进行离线全量匹配验证。",
        },
        "dod_1260h": {
            "full_name": "美国国防部中国军事企业清单 (DoD Section 1260H)",
            "background": "根据《国防授权法》第 1260H 条，美国国防部每年公布与中国军方有关联的企业清单。该清单虽不直接禁止交易，但列入该清单的实体面临更严格的审查，且可能触发投资限制（根据第 13959 号行政令）。",
            "legal": "被列入企业的美国上市证券可能被禁止交易。美国投资者持有的相关证券可能需要在规定期限内剥离。",
            "method": "通过国防部官方网站访问名单 PDF 文件，在浏览器中直接查看并搜索匹配。",
        },
        "sam": {
            "full_name": "美国联邦政府采购排除清单 (SAM.gov Exclusions)",
            "background": "SAM.gov 排除清单列出被禁止参与美国联邦政府采购和非采购项目的实体。被排除的实体无法获得联邦合同、赠款或其他援助。",
            "legal": "与被排除实体签订联邦合同属于违法行为。相关机构有义务在授标前检查此清单。",
            "method": "通过 SAM.gov 官方搜索接口进行实体查询。",
        },
        "fcc": {
            "full_name": "美国联邦通信委员会受管设备和服务清单 (FCC Covered List)",
            "background": "根据《安全和可信通信网络法》第 2 条，FCC 公布对美国国家安全构成不可接受风险的通信设备和服务清单。被列入的设备/服务不得使用联邦资金采购，且现有设备可能需要拆除和更换。",
            "legal": "使用联邦资金采购被列入清单的设备属于违规行为。\"Rip and Replace\" 计划要求替换已部署的受管设备。",
            "method": "直接访问 FCC Supply Chain 页面，加载完整设备清单后进行文本搜索和高亮匹配。",
        },
        "uk": {
            "full_name": "英国金融制裁清单 (UK Financial Sanctions / OFSI)",
            "background": "英国金融制裁实施办公室(OFSI)维护的制裁清单。英国脱欧后独立于欧盟制裁体系运作。被列入意味着在英国管辖范围内的资产将被冻结，且禁止向其提供资金或经济资源。",
            "legal": "违反英国金融制裁属于刑事犯罪，可能面临最高 7 年监禁和/或无上限罚款。OFSI 也可以施加民事罚款（最高 $100万或违规金额的 50%）。",
            "method": "通过英国制裁搜索官方网站进行搜索，启用模糊匹配(Fuzzy Match)以提高召回率。",
        },
        "un": {
            "full_name": "联合国安理会综合制裁清单 (UN Security Council Consolidated List)",
            "background": "联合国安理会制裁是国际社会最权威的制裁机制，由安理会决议授权。所有联合国成员国均有义务执行。清单目标包括恐怖主义、大规模杀伤性武器扩散等威胁国际和平与安全的活动。",
            "legal": "联合国成员国有国际法义务执行安理会制裁决议。未能执行可能导致来自安理会的额外措施。",
            "method": "通过联合国安理会制裁委员会官方搜索系统查询。",
        },
        "eu": {
            "full_name": "欧盟金融制裁综合清单 (EU Consolidated Financial Sanctions)",
            "background": "欧盟制裁清单由欧盟理事会决定，所有成员国均须执行。措施包括资产冻结、旅行禁令和贸易限制。欧盟制裁的域外效力不断扩大。",
            "legal": "违反欧盟制裁在各成员国均属于刑事犯罪。各国的处罚力度不同，但通常包括巨额罚款和监禁。",
            "method": "通过欧盟制裁地图(EU Sanctions Map)官方搜索引擎查询。",
        },
        "au": {
            "full_name": "澳大利亚外交与贸易部制裁综合清单 (Australia DFAT Consolidated List)",
            "background": "澳大利亚根据《自治制裁法 2011》和联合国安理会制裁决议维护制裁清单。违反澳大利亚制裁属于刑事犯罪。",
            "legal": "违反制裁可面临最高 10 年监禁和/或罚款。企业可能面临最高相当于违规交易价值 3 倍的罚款。",
            "method": "下载 DFAT 官方 XLSX 数据文件（Australian_Sanctions_Consolidated_List.xlsx），使用 pandas 进行全量本地检索，匹配结果渲染为表格截图。",
        },
        "ca": {
            "full_name": "加拿大特别经济措施制裁清单 (Canada SEMA Consolidated List)",
            "background": "加拿大根据《特别经济措施法》(SEMA)、《联合国法》等法律实施制裁。加拿大制裁包括资产冻结、交易禁令和贸易限制。",
            "legal": "违反加拿大制裁可面临最高 $25,000 罚款和/或最高 5 年监禁。",
            "method": "通过加拿大全球事务部官方搜索引擎进行查询。",
        },
    }

    lines = []
    lines.append(f"# 🏛️ 全球制裁与出口管制合规筛查报告\n")
    lines.append(f"---\n")
    lines.append(f"**筛查实体：** {entity}")
    lines.append(f"**校验时间：** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**校验名单数：** {len(results)}")
    lines.append(f"**筛查方法：** 自动化浏览器校验（系统 Chrome headed 模式 + 官方数据下载）")
    lines.append("")

    # ═══ 自动 OpenSanctions 交叉验证 ═══
    # 对每个 HIT 结果做交叉验证，判断是否为误中
    # 源 key → OpenSanctions datasets 映射
    SOURCE_TO_OS_DATASETS = {
        "ofac": {"us_ofac_sdn", "us_ofac_cons", "us_sam_exclusions"},
        "bis_csl": {"us_trade_csl", "us_bis_denied"},
        "dod_1260h": {"us_dod_chinese_milcorps"},
        "sam": {"us_sam_exclusions"},
        "uk": {"gb_hmt_sanctions"},
        "un": {"un_sc_sanctions"},
        "eu": {"eu_fsf"},
        "au": {"au_dfat_sanctions"},
        "ca": {"ca_sema_sanctions"},
    }
    # 核心/泛化清单定义已上移到模块级 CORE_SOURCES / FP_CANDIDATE_SOURCES，
    # 此处仅保留旧名别名，避免同一概念出现两份定义。
    CORE_HIT_SOURCES = CORE_SOURCES

    # ISO 国家代码碰撞检测
    ISO_CODES = {"DJI": "Djibouti", "CHN": "China", "IRN": "Iran", "PRK": "North Korea",
                 "RUS": "Russia", "MMR": "Myanmar", "BLR": "Belarus", "CUB": "Cuba",
                 "SYR": "Syria", "VEN": "Venezuela", "CAR": "Central African Republic"}

    os_datasets = set()
    os_results_for_report = []
    try:
        from opensanctions_search import search_entity, scrape_opensanctions
        from env_loader import load_dotenv as _ld
        _ld()
        sr = search_entity(entity, limit=10)
        if not sr.get("results"):
            sr = scrape_opensanctions(entity, limit=10)
        for r in sr.get("results", []):
            os_datasets.update(r.get("datasets", []))
            os_results_for_report.append(r)
    except Exception as e:
        print(f"  [CROSS-VALIDATION] OpenSanctions query failed: {e}")

    # ═══ 误中上下文校验（Enhanced False Positive Context Verification）═══
    # 对每个 HIT 结果做上下文感知的交叉验证
    # 核心三问：(1) 实体类型匹配？ (2) 全名上下文匹配？ (3) 跨清单佐证？

    # 辅助函数：检查 detail/csv_matches 中是否包含实体名的有意义片段
    def _entity_name_in_context(name, detail_text, csv_matches_list=None):
        """检查实体名称是否在命中内容上下文中出现（忽略大小写）"""
        if not detail_text:
            return False
        name_lower = name.lower().strip()
        detail_lower = detail_text.lower()
        # 直接包含实体名
        if name_lower in detail_lower:
            return True
        # 检查实体名的主要组成部分（>= 4 字符的词）
        for part in name_lower.split():
            if len(part) >= 4 and part in detail_lower:
                return True
        # 检查 csv_matches 中的实际匹配内容
        if csv_matches_list:
            for match in csv_matches_list:
                match_str = json.dumps(match, ensure_ascii=False).lower() if isinstance(match, dict) else str(match).lower()
                if name_lower in match_str:
                    return True
                for part in name_lower.split():
                    if len(part) >= 4 and part in match_str:
                        return True
        return False

    def _is_type_mismatch(entity_type, detail_text, csv_matches_list=None):
        """检查命中记录的实体类型是否与搜索实体类型不匹配"""
        if not detail_text:
            return False, ""
        detail_lower = detail_text.lower()
        csv_text = ""
        if csv_matches_list:
            csv_text = json.dumps(csv_matches_list, ensure_ascii=False).lower()

        if entity_type == "Entity":
            person_indicators = ["individual", "person", "date of birth", "nationality:",
                                 "passport", "gender:", "dob:", "born "]
            for ind in person_indicators:
                if ind in detail_lower or ind in csv_text:
                    return True, f"实体类型不匹配: 搜索公司/法人但命中记录为个人('{ind}')"
        elif entity_type == "Individual":
            entity_indicators = ["company", "corporation", "ltd.", "inc.", "co.,",
                                 "organization", "enterprise", "technologies"]
            for ind in entity_indicators:
                if ind in detail_lower or ind in csv_text:
                    return True, f"实体类型不匹配: 搜索个人但命中记录为公司/组织('{ind}')"
        return False, ""

    # 收集跨清单统计，用于综合判断
    core_hit_sources = [r.get("source") for r in results
                        if r["status"] == "hit" and r.get("source") in CORE_HIT_SOURCES]
    has_any_core_hit = len(core_hit_sources) > 0

    for r in results:
        if r["status"] != "hit":
            continue
        src = r.get("source", "")
        detail = r.get("detail", "")
        searched = r.get("searched", entity)
        csv_matches = r.get("csv_matches", None)

        expected_ds = SOURCE_TO_OS_DATASETS.get(src, set())
        has_os_support = bool(os_datasets & expected_ds)

        fp_reason = None
        fp_context_details = []

        if src in CORE_HIT_SOURCES:
            # 核心清单默认保持 HIT，但仍做上下文检查记录
            type_mismatch, type_reason = _is_type_mismatch(entity_type, detail, csv_matches)
            if type_mismatch:
                fp_context_details.append(f"⚠️ 上下文注意: {type_reason}（核心清单，仍以脚本结果为准）")
            r["os_cross_validated"] = has_os_support
            r["fp_reason"] = None
            r["likely_fp"] = False
            r["fp_context"] = "; ".join(fp_context_details) if fp_context_details else None

        elif src in FP_CANDIDATE_SOURCES:
            search_upper = searched.upper() if searched else entity.upper()

            # 模式 1：实体类型不匹配（公司搜到个人、个人搜到公司）
            type_mismatch, type_reason = _is_type_mismatch(entity_type, detail, csv_matches)
            if type_mismatch:
                fp_reason = type_reason
                fp_context_details.append(type_reason)

            # 模式 2：ISO 国家代码碰撞（AU/CA 的 CSV 搜索）
            if not fp_reason and search_upper in ISO_CODES:
                country_name = ISO_CODES[search_upper]
                if country_name.lower() in detail.lower():
                    fp_reason = f"ISO代码碰撞: {search_upper}={country_name}"
                    fp_context_details.append(fp_reason)

            # 模式 3：csv_matches 全名上下文校验
            if not fp_reason and csv_matches:
                has_name_match = _entity_name_in_context(entity, detail, csv_matches)
                if not has_name_match:
                    # 也检查搜索变体
                    variant_found = False
                    if searched and searched != entity:
                        variant_found = _entity_name_in_context(searched, detail, csv_matches)
                    if not variant_found:
                        fp_reason = f"命中内容上下文中未找到实体名称 '{entity}' 的有意义匹配"
                        fp_context_details.append(fp_reason)

            # 模式 4：EU Sanctions Map regime 泛化
            if not fp_reason and src == "eu":
                if "regime" in detail.lower() or "requires LLM" in detail:
                    fp_reason = "EU Sanctions Map 返回 regime 列表，需查看详情页确认"
                    fp_context_details.append(fp_reason)

            # 模式 5：人名子串匹配
            if not fp_reason:
                if "Individual" in detail or "person" in detail.lower():
                    if entity_type == "Entity":
                        fp_reason = "人名子串匹配: 搜索公司但命中内容为个人记录"
                        fp_context_details.append(fp_reason)

            # 模式 6：无 OpenSanctions 佐证 + 泛化清单
            if not fp_reason and not has_os_support:
                fp_reason = f"OpenSanctions 无 {src.upper()} 相关制裁记录佐证"
                fp_context_details.append(fp_reason)

            # 模式 7：跨清单综合判断 — 无核心清单命中时，泛化清单命中更可疑
            if not fp_reason and not has_any_core_hit:
                fp_reason = f"无核心清单(OFAC/BIS/DoD/UK/UN)命中佐证，{src.upper()} 单独命中可疑"
                fp_context_details.append(fp_reason)

            if fp_reason:
                r["likely_fp"] = True
                r["fp_reason"] = fp_reason
                r["os_cross_validated"] = False
            else:
                r["likely_fp"] = False
                r["fp_reason"] = None
                r["os_cross_validated"] = has_os_support
            r["fp_context"] = "; ".join(fp_context_details) if fp_context_details else None
        else:
            r["likely_fp"] = False
            r["fp_reason"] = None
            r["os_cross_validated"] = has_os_support
            r["fp_context"] = None

    # 汇总统计
    hits = [r for r in results if r["status"] == "hit"]
    confirmed_hits = [r for r in hits if not r.get("likely_fp")]
    likely_fps = [r for r in hits if r.get("likely_fp")]
    cleans = [r for r in results if r["status"] == "clean"]
    reviews = [r for r in results if r["status"] == "manual_review"]
    errors = [r for r in results if r["status"] == "error"]

    lines.append("## 📊 一、校验结果汇总\n")
    lines.append("| # | 管辖区 | 名单 | 搜索词 | 结果 | 详情 |")
    lines.append("|---|--------|------|--------|------|------|")

    for i, r in enumerate(results, 1):
        if r["status"] == "hit" and r.get("likely_fp"):
            status = "🟡 **疑似误中**"
        else:
            status_map = {
                "clean": "✅ **未命中**",
                "hit": "🔴 **命中**",
                "manual_review": "🟡 **需人工确认**",
                "error": "❌ **错误**",
            }
            status = status_map.get(r["status"], r["status"])
        detail = r.get("detail", "")
        if r.get("fp_reason"):
            detail += f" [FP: {r['fp_reason']}]"
        searched = r.get("searched", "")
        lines.append(f"| {i} | {r.get('flag', '')} | {r.get('name', r['source'])} | {searched} | {status} | {detail} | <!-- sk:{r['source']} -->")

    lines.append("")

    if confirmed_hits:
        lines.append(f"> ⚠️ **警告：** 在 **{len(confirmed_hits)}** 个制裁/管制名单中发现确认命中记录。命中名单：{', '.join(r.get('name', r['source']) for r in confirmed_hits)}。")
        lines.append("")
    if likely_fps:
        lines.append(f"> 🟡 **疑似误中：** {len(likely_fps)} 个清单命中可能为虚假命中（经 OpenSanctions 交叉验证）。")
        lines.append("")
    if not confirmed_hits and not likely_fps and not errors and not reviews:
        lines.append(f"> ✅ 在全部 {len(results)} 个制裁名单中均未发现命中记录。")
        lines.append("")

    # 风险矩阵
    lines.append("### 风险矩阵\n")
    lines.append(f"- 🔴 **确认命中：** {len(confirmed_hits)} 个名单")
    lines.append(f"- 🟡 **疑似误中：** {len(likely_fps)} 个名单")
    lines.append(f"- ✅ **未命中：** {len(cleans)} 个名单")
    lines.append(f"- 🟡 **待确认：** {len(reviews)} 个名单")
    lines.append(f"- ❌ **校验失败：** {len(errors)} 个名单")
    lines.append("")

    # 各名单详细报告
    lines.append("---\n")
    lines.append("## 📋 二、各名单校验详情\n")

    for i, r in enumerate(results, 1):
        source_key = r.get("source", "")
        source_name = r.get("name", source_key)
        flag = r.get("flag", "")
        status = r["status"]
        ctx = SOURCE_CONTEXT.get(source_key, {})

        if status == "hit" and r.get("likely_fp"):
            status_label = "🟡 疑似误中"
        else:
            status_label = {
                "clean": "✅ 未命中",
                "hit": "🔴 命中",
                "manual_review": "🟡 需人工确认",
                "error": "❌ 校验失败",
            }.get(status, status)

        lines.append(f'<div id="source-{source_key}"></div>\n')
        lines.append(f"### {i}. {flag} {ctx.get('full_name', source_name)} — {status_label}\n")

        # 背景说明
        if ctx.get("background"):
            lines.append(f"**名单背景：** {ctx['background']}\n")

        # 法律含义
        if ctx.get("legal"):
            lines.append(f"**法律影响：** {ctx['legal']}\n")

        # 搜索方法
        lines.append(f"**搜索方法：** {ctx.get('method', '浏览器自动校验')}")
        lines.append(f"**搜索词：** {r.get('searched', 'N/A')}")
        lines.append("")

        # 具体发现
        lines.append(f"**发现：**\n")
        detail = r.get('detail', 'N/A')
        if status == "hit" and r.get("likely_fp"):
            lines.append(f"> 🟡 **疑似误中 — 经 OpenSanctions 交叉验证，本源命中可能为虚假命中。** {r.get('fp_reason', '')}")
            lines.append(f"> 原始检测结果: {detail}")
        elif status == "hit":
            lines.append(f"> 🔴 **经查询确认，目标实体在本名单中存在命中记录。** {detail}")
        elif status == "clean":
            lines.append(f"> ✅ **经查询确认，目标实体未出现在本名单中。** {detail}")
        elif status == "manual_review":
            lines.append(f"> 🟡 **查询结果不确定，需人工核实。** {detail}")
        else:
            lines.append(f"> ❌ **校验过程中出现错误，未能完成查询。** {detail}")
        lines.append("")

        # CSV 匹配详情（BIS CSL）
        csv_matches = r.get("csv_matches")
        if csv_matches:
            lines.append(f"**匹配实体详情（共 {len(csv_matches)} 条记录）：**\n")
            lines.append("| # | 实体名 | 来源名单 | 制裁项目 |")
            lines.append("|---|--------|---------|---------|")
            for idx, m in enumerate(csv_matches[:20], 1):
                lines.append(f"| {idx} | {m.get('name', '')} | {m.get('source', '')[:60]} | {m.get('programs', '')[:60]} |")
            if len(csv_matches) > 20:
                lines.append(f"| ... | *(另有 {len(csv_matches) - 20} 条记录，详见完整截图)* | | |")
            lines.append("")

        # 嵌入截图（按变体分组）
        screenshots = r.get("screenshots", [])
        searched_str = r.get("searched", "")
        searched_variants = [v.strip() for v in searched_str.split(",")] if searched_str else []

        # 去重
        seen_fnames = set()
        unique_screenshots = []
        for s_path in screenshots:
            if s_path and os.path.exists(s_path):
                fname = os.path.basename(s_path)
                if fname not in seen_fnames:
                    seen_fnames.add(fname)
                    unique_screenshots.append(s_path)

        if unique_screenshots and searched_variants and len(searched_variants) > 1:
            # 多变体：按变体分组
            # 文件名格式：{source_key}_{variant_safe_name}_{type}_{timestamp}.png
            # variant_safe_name 是变体名中空格替换为_，特殊字符保留
            variant_groups = {}
            unmatched = []
            for s_path in unique_screenshots:
                fname = os.path.basename(s_path)
                matched_variant = None
                # 从文件名中匹配变体（按长度降序匹配，避免短名误匹配长名的一部分）
                for v in sorted(searched_variants, key=len, reverse=True):
                    v_safe = v.replace(" ", "_")
                    # 检查文件名是否包含 {source_key}_{v_safe}_
                    if f"_{v_safe}_" in fname or fname.startswith(f"{source_key}_{v_safe}_"):
                        matched_variant = v
                        break
                if matched_variant:
                    variant_groups.setdefault(matched_variant, []).append(s_path)
                else:
                    unmatched.append(s_path)

            lines.append(f"**证据截图（{len(unique_screenshots)} 张，按搜索变体分组）：**\n")
            for v in searched_variants:
                group = variant_groups.get(v, [])
                if group:
                    lines.append(f"#### 🔍 搜索词: `{v}` ({len(group)} 张)\n")
                    for j, s_path in enumerate(group, 1):
                        abs_path = os.path.abspath(s_path).replace("\\", "/")
                        fname = os.path.basename(s_path)
                        lines.append(f"**截图 {j}：** {fname}\n")
                        lines.append(f"![{source_name} - {v} - 截图 {j}]({abs_path})\n")
            if unmatched:
                lines.append(f"#### 其他截图 ({len(unmatched)} 张)\n")
                for j, s_path in enumerate(unmatched, 1):
                    abs_path = os.path.abspath(s_path).replace("\\", "/")
                    fname = os.path.basename(s_path)
                    lines.append(f"![{source_name} - 其他 {j}]({abs_path})\n")
        elif unique_screenshots:
            # 单变体或无变体信息：直接列出
            lines.append(f"**证据截图（{len(unique_screenshots)} 张）：**\n")
            for j, s_path in enumerate(unique_screenshots, 1):
                abs_path = os.path.abspath(s_path).replace("\\", "/")
                fname = os.path.basename(s_path)
                lines.append(f"**截图 {j}：** {fname}\n")
                lines.append(f"![{source_name} - 截图 {j}]({abs_path})\n")
        else:
            lines.append("\n*（本源未获取到截图）*\n")

        lines.append("---\n")

    # 证据清单
    lines.append("## 📎 三、证据文件清单\n")
    lines.append("| # | 名单 | 文件名 | 大小 |")
    lines.append("|---|------|--------|------|")

    file_idx = 1
    for r in results:
        source_name = r.get("name", r["source"])
        seen_fnames = set()
        for s_path in r.get("screenshots", []):
            if s_path and os.path.exists(s_path):
                fname = os.path.basename(s_path)
                if fname not in seen_fnames:
                    seen_fnames.add(fname)
                    size_kb = os.path.getsize(s_path) / 1024
                    lines.append(f"| {file_idx} | {source_name} | {fname} | {size_kb:.1f} KB |")
                    file_idx += 1

    lines.append("")

    # 免责声明
    lines.append("## ⚖️ 四、免责声明\n")
    lines.append("- 本报告由自动化合规筛查引擎生成，仅供内部参考，不构成法律意见。")
    lines.append("- 所有校验结果均来自各国政府官方公开数据源的直接查询，已尽最大努力确保准确性。")
    lines.append("- 标注为「✅ 未命中」表示在查询时刻未在官方名单中发现匹配；标注为「🔴 命中」表示发现匹配记录。")
    lines.append("- 制裁名单持续动态更新，本报告有效期建议不超过 **7 天**，建议定期复查。")
    lines.append("- 本报告不能替代合格律师或合规专家的专业判断，最终合规决策应咨询专业法律顾问。")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Official sanctions browser verification (10 international + 3 China sources, v2 enhanced)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/browser_verify.py "华为"
  python scripts/browser_verify.py "Huawei Technologies"
  python scripts/browser_verify.py "huawei" --exact-name-only
  python scripts/browser_verify.py "Huawei" --extra-variants "huawei" --type Entity
  python scripts/browser_verify.py "大疆" --sources ofac bis_csl uk eu
  python scripts/browser_verify.py "埃比特系统美国分公司" --sources cn_ucl cn_control cn_counter --headless
  python scripts/browser_verify.py "DJI" --headed --type Entity
        """,
    )
    parser.add_argument("entity", help="Entity name (Chinese or English, variants auto-generated unless exact mode)")
    parser.add_argument("--sources", nargs="+", choices=ALL_SOURCES, default=None,
                        help="Sources to check (default: all 13, incl. cn_ucl / cn_control / cn_counter)")
    parser.add_argument("--type", default="Entity", choices=["Entity", "Individual"],
                        help="Entity type (default: Entity)")
    parser.add_argument("-o", "--output-dir", default="screenshots",
                        help="Screenshot output directory (default: screenshots/)")
    parser.add_argument("--headless", action="store_true",
                        help="Run in headless mode (default: headed/visible browser)")
    parser.add_argument("--json", action="store_true",
                        help="JSON output")
    parser.add_argument("--markdown", action="store_true",
                        help="Markdown output (default)")
    parser.add_argument("--report", default=None,
                        help="Auto-generate HTML report with embedded screenshots (specify output filename)")
    parser.add_argument("--extra-variants", nargs="+", default=None, metavar="VARIANT",
                        help="Explicit additional names to include (1-N items); when provided, only entity + these names are searched")
    parser.add_argument("--exact-name-only", action="store_true",
                        help="Only search the exact input name; disable auto variant expansion")
    parser.add_argument("--api-report", default=None,
                        help="Path to Phase 1 API report (.md) to embed OpenSanctions results in HTML report")
    parser.add_argument("--no-pdf", action="store_true",
                        help="Skip PDF generation (PDF is generated by default)")

    # 反检测伪装开关。默认关闭。
    # ⚠️ 合规提示（务必先读）：
    #   开启后会隐藏 automation 标记、伪装 User-Agent、伪造 Sec-Fetch-* 请求头
    #   并注入 STEALTH_JS。这些手段作用于各国政府官网，可能违反其 Terms of Use
    #   或 robots 约定，也可能与当地司法辖区的计算机欺诈规则产生冲突。
    #   **是否允许启用，须由法务评估后决定。** 未经确认请保持关闭。
    #   关闭时若某源因反爬被拦截，该源会落 manual_review——这是正确行为，
    #   表示「未能核实」，而不是「未命中」。
    parser.add_argument("--stealth", action="store_true",
                        help="启用反检测伪装（默认关闭；开启前须经法务确认符合各站点 ToS）")
    parser.add_argument("--intel-rounds", choices=["minimal", "standard", "full"], default="standard",
                        help="Tavily 情报搜索轮次档位：minimal=1轮/standard=4轮(默认)/full=7轮。"
                             "轮次越多成本越高（Tavily 按次计费）")

    args = parser.parse_args()
    # 环境变量作为等效开关，便于在不改命令行的情况下由配置统一控制
    if os.environ.get("SANCTIONS_STEALTH") == "1":
        args.stealth = True
    if not args.stealth:
        print("[合规] 反检测伪装（stealth）未启用 —— 目标站点因反爬拦截时，"
              "相关源将标记为 manual_review（未能核实），不会被当作「未命中」。")

    results = run_verification(
        entity=args.entity,
        sources=args.sources,
        entity_type=args.type,
        out_dir=args.output_dir,
        headless=args.headless,
        extra_variants=args.extra_variants or [],
        exact_name_only=args.exact_name_only,
        stealth=args.stealth,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        md = format_results_markdown(args.entity, results, entity_type=args.type)

        # 保存 markdown 文件
        safe_name = args.entity.replace(" ", "_").replace("/", "_")[:30]
        md_path = os.path.join(args.output_dir, f"{safe_name}_verification.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"\n  [SAVED] Markdown report: {md_path}")

        # 自动生成专业 HTML 报告（麦肯锡风格 + 目录索引 + 截图嵌入）
        report_path = args.report
        if not report_path:
            report_path = f"{safe_name}_sanctions_report.html"

        try:
            from generate_report import embed_images, beautify_html
            import markdown as md_lib

            # 内嵌图片为 base64
            md_with_images, img_count = embed_images(md)
            print(f"  [EMBED] {img_count} screenshots embedded as base64")

            # 渲染 HTML body
            html_body = md_lib.markdown(
                md_with_images, extensions=["tables", "fenced_code", "toc", "nl2br"]
            )
            html_body = beautify_html(html_body)

            # 给每个源 anchor div 添加 section class（使 PDF 分页生效）
            import re as _re_html
            html_body = _re_html.sub(
                r'<div id="source-',
                r'<div class="section" id="source-',
                html_body
            )

            # 从 results 构建目录项
            toc_items = []
            for r in results:
                status = r.get("status", "error")
                if status == "hit" and r.get("likely_fp"):
                    icon = "🟡"
                else:
                    icon = {"hit": "🔴", "clean": "🟢", "manual_review": "🟡", "error": "⚠️"}.get(status, "❓")
                anchor = f"source-{r.get('source', 'unknown')}"
                toc_items.append(f'<li><a href="#{anchor}">{icon} {r.get("flag","")} {r.get("name","Unknown")}</a></li>')
            toc_html = "\n".join(toc_items)

            # 构建 PDF 打印专用目录（打印时显示，屏幕隐藏）
            print_toc_items = ['<li><a href="#executive-summary">Executive Summary</a></li>',
                               '<li><a href="#opensanctions">OpenSanctions Cross-Validation</a></li>',
                               '<li><a href="#ai-fp-assessment">AI False Positive Assessment</a></li>']
            for r in results:
                anchor = f"source-{r.get('source', 'unknown')}"
                name = f"{r.get('flag','')} {r.get('name','Unknown')}"
                print_toc_items.append(f'<li><a href="#{anchor}">{name}</a></li>')
            print_toc_items.append('<li><a href="#tavily-news">Adverse Media & Intelligence</a></li>')
            print_toc_items.append('<li><a href="#evidence-list">Evidence List</a></li>')
            print_toc_items.append('<li><a href="#disclaimer">Disclaimer</a></li>')
            print_toc_html = "\n".join(print_toc_items)

            # 统计摘要
            hits = [r for r in results if r["status"] == "hit"]
            confirmed_hits = [r for r in hits if not r.get("likely_fp")]
            likely_fps = [r for r in hits if r.get("likely_fp")]
            cleans = [r for r in results if r["status"] == "clean"]
            total = len(results)

            # 区分核心制裁清单和非核心清单的命中情况
            # 统一使用模块级 CORE_SOURCES（原先此处另定义一份且漏掉 dod_1260h，
            # 与误中判定的 CORE_HIT_SOURCES 不一致，导致同一报告自相矛盾：
            # MD 正文说"确认命中"、HTML 封面说"需复核"）
            core_hits = [r for r in hits if r.get("source", "") in CORE_SOURCES]
            non_core_hits = [r for r in hits if r.get("source", "") not in CORE_SOURCES]

            if core_hits:
                # 核心清单命中 → 确认高风险
                risk_level = "🛑 HIGH RISK"
                risk_color = "#c00000"
            elif non_core_hits:
                # 仅在非核心清单命中 → 可能误中，需人工确认
                risk_level = "🟡 POTENTIAL MATCH — REVIEW REQUIRED"
                risk_color = "#d4a017"
            else:
                risk_level = "🟢 CLEAR"
                risk_color = "#00703c"

            # Tavily 新闻模块：调用 search_entity_sanctions 获取制裁相关新闻
            tavily_html = ""

            # OpenSanctions 交叉验证模块：读取 Phase 1 报告或直接调用 API
            opensanctions_html = ""
            try:
                os_results_list = []  # 统一的结果列表
                os_query = args.entity
                os_source = ""

                if args.api_report and os.path.exists(args.api_report):
                    with open(args.api_report, "r", encoding="utf-8") as f:
                        api_content = f.read()
                    import re as _re
                    # 从 Phase 1 报告提取匹配实体
                    matches = _re.findall(
                        r'### \d+\.\s+(.*?)\s+—.*?(?:匹配|match)\s*\((\d+)%\).*?'
                        r'\*\*命中名单[：:]\*\*\s*(.*?)(?:\n|\r)',
                        api_content, _re.DOTALL
                    )
                    for name, score, datasets in matches:
                        os_results_list.append({
                            "name": name.strip(),
                            "score": int(score) / 100,
                            "datasets": [d.strip() for d in datasets.split("|")],
                        })
                    # 提取别名
                    aliases = _re.findall(r'\*\*别名[：:]\*\*\s*(.*?)(?:\n|\r)', api_content)
                    if aliases and os_results_list:
                        os_results_list[0]["aliases"] = aliases[0].strip()
                    # 提取国家
                    countries = _re.findall(r'\*\*国家[：:]\*\*\s*(.*?)(?:\n|\r)', api_content)
                    if countries and os_results_list:
                        os_results_list[0]["country"] = countries[0].strip()
                    os_source = "Phase 1 API Report"
                    print(f"  [REPORT] OpenSanctions data extracted from {args.api_report}: {len(os_results_list)} matches")

                if not os_results_list:
                    # 无 Phase 1 报告或提取失败时，直接调用 OpenSanctions
                    from opensanctions_search import search_entity, scrape_opensanctions
                    from env_loader import load_dotenv as _ld
                    _ld()
                    sr = search_entity(args.entity, limit=5)
                    if not sr.get("results"):
                        sr = scrape_opensanctions(args.entity, limit=5)
                    for r in sr.get("results", [])[:5]:
                        os_results_list.append({
                            "name": r.get("name", ""),
                            "score": r.get("score", 0),
                            "datasets": r.get("datasets", []),
                            "schema": r.get("schema", ""),
                            "country": ", ".join(r.get("countries", [])),
                            "aliases": ", ".join(r.get("aliases", r.get("names", []))[:4]),
                        })
                    os_source = "Live API Query"
                    print(f"  [REPORT] OpenSanctions live query: {len(os_results_list)} results")

                # 统一生成 HTML
                if os_results_list:
                    # 数据集名称 → 人可读名称 + 颜色
                    DS_DISPLAY = {
                        "us_ofac_sdn": ("🇺🇸 OFAC SDN", "#c00000"),
                        "us_ofac_cons": ("🇺🇸 OFAC Consolidated", "#c00000"),
                        "us_trade_csl": ("🇺🇸 BIS CSL", "#c00000"),
                        "us_bis_denied": ("🇺🇸 BIS Denied", "#c00000"),
                        "us_dod_chinese_milcorps": ("🇺🇸 DoD 1260H", "#c00000"),
                        "us_sam_exclusions": ("🇺🇸 SAM.gov", "#c00000"),
                        "un_sc_sanctions": ("🇺🇳 UN SC", "#1a5276"),
                        "eu_fsf": ("🇪🇺 EU Sanctions", "#003399"),
                        "gb_hmt_sanctions": ("🇬🇧 UK HMT", "#003399"),
                        "au_dfat_sanctions": ("🇦🇺 AU DFAT", "#003399"),
                        "ca_sema_sanctions": ("🇨🇦 CA SEMA", "#003399"),
                        "ext_us_ofac_press_releases": ("📰 OFAC Press", "#666"),
                        "permid": ("🔗 PermID", "#666"),
                        "ext_gleif": ("🔗 GLEIF", "#666"),
                    }
                    COUNTRY_FLAGS = {"cn": "🇨🇳 China", "us": "🇺🇸 USA", "ru": "🇷🇺 Russia", "ir": "🇮🇷 Iran", "kp": "🇰🇵 DPRK"}

                    os_rows = ""
                    for idx, item in enumerate(os_results_list, 1):
                        # 数据集 badge
                        ds_badges = ""
                        for d in item.get("datasets", [])[:6]:
                            d_clean = d.strip()
                            label, color = DS_DISPLAY.get(d_clean, (d_clean, "#555"))
                            ds_badges += f'<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;border-radius:12px;font-size:0.78em;color:white;background:{color};">{label}</span>'

                        score = item.get("score", 0)
                        score_pct = f"{score:.0%}" if isinstance(score, float) else f"{score}%"
                        score_val = int(score * 100) if isinstance(score, float) else int(score)
                        score_color = "#c00000" if score_val >= 70 else "#d4a017" if score_val >= 40 else "#888"
                        # 进度条
                        bar = f'<div style="display:flex;align-items:center;gap:6px;"><div style="width:60px;height:8px;background:#eee;border-radius:4px;overflow:hidden;"><div style="width:{score_val}%;height:100%;background:{score_color};border-radius:4px;"></div></div><strong style="color:{score_color};">{score_pct}</strong></div>'

                        aliases = item.get("aliases", "")
                        country_raw = item.get("country", "")
                        country = COUNTRY_FLAGS.get(country_raw.lower().strip(), country_raw) if country_raw else ""

                        os_rows += f'''<tr>
                            <td style="text-align:center;font-weight:bold;">{idx}</td>
                            <td><strong>{item["name"]}</strong>{"<br/><small style='color:#666;'>" + aliases + "</small>" if aliases else ""}</td>
                            <td>{ds_badges or "<span style='color:#999;'>—</span>"}</td>
                            <td style="text-align:center;">{country}</td>
                            <td>{bar}</td>
                        </tr>'''

                    opensanctions_html = f'''<div id="opensanctions" class="section">
                        <h1>🔍 OpenSanctions Cross-Validation</h1>
                        <div style="display:flex;gap:20px;flex-wrap:wrap;margin:10px 0 15px;">
                            <div><strong>查询实体</strong><br/><span style="font-size:1.1em;">{os_query}</span></div>
                            <div><strong>匹配数</strong><br/><span style="font-size:1.3em;color:#c00000;font-weight:bold;">{len(os_results_list)}</span></div>
                            <div><strong>数据来源</strong><br/><span>{os_source}</span></div>
                        </div>
                        <blockquote style="border-left:4px solid #2563eb;background:#eff6ff;padding:10px 15px;margin:10px 0;">
                            OpenSanctions 聚合全球 100+ 制裁名单数据，用于交叉验证官方筛查结果。<br/>
                            <strong>核心清单命中 + OS 匹配 = ✅ 确认命中</strong> &nbsp;|&nbsp;
                            <strong>泛化清单命中 + OS 无匹配 = 🟡 疑似误中</strong>
                        </blockquote>
                        <table>
                            <thead><tr><th style="width:30px;">#</th><th>实体名称 / 别名</th><th>命中数据集</th><th style="width:80px;">国家</th><th style="width:100px;">匹配度</th></tr></thead>
                            <tbody>{os_rows}</tbody>
                        </table>
                    </div>'''
                    print(f"  [REPORT] OpenSanctions section embedded ({len(os_results_list)} matches)")
            except Exception as e:
                print(f"  [REPORT] OpenSanctions section skipped: {e}")
            try:
                from tavily_search import search_entity_sanctions, verify_url, has_api_keys
                from env_loader import load_dotenv
                load_dotenv()
                # 无 key 时情报层整体跳过，并在报告中标明"未查"而非"无结果"
                intel_available = has_api_keys()
                raw = search_entity_sanctions(
                    args.entity,
                    max_results=8,
                    rounds=getattr(args, "intel_rounds", "standard"),
                )
                intel_available = bool(raw.get("available", intel_available))
                if not intel_available:
                    print(f"  [REPORT] {raw.get('message', '情报层不可用，已跳过')}")
                report.setdefault("_meta", {})["intel_available"] = intel_available
                report["_meta"]["intel_rounds"] = raw.get("rounds")
                if not intel_available:
                    report["_meta"]["intel_message"] = raw.get("message", "")

                # 制裁数据库/清单页面的 URL 特征（非新闻，过滤掉）
                SANCTIONS_DB_PATTERNS = [
                    "sanctionssearch.ofac.treas.gov", "sanctionslist",
                    "sdn-list", "consolidated-list", "scsanctions.un.org",
                    "webgate.ec.europa.eu/fsd", "sam.gov/entity",
                    "trade.gov/consolidated-screening-list",
                    "dfat.gov.au/sites/default/files",
                    "wikipedia.org/wiki/Specially_Designated",
                    "wikipedia.org/wiki/Entity_List",
                    "wikipedia.org/wiki/Bureau_of_Industry",
                    "federalregister.gov",
                    "opensanctions.org",
                    "home.treasury.gov/policy-issues/financial-sanctions",
                    "home.treasury.gov/system/files",
                ]
                # 标题中含这些关键词的也是清单页面不是新闻
                SANCTIONS_TITLE_KEYWORDS = [
                    "sanctions list search", "sdn list", "consolidated screening",
                    "entity list -", "specially designated nationals",
                    "ofac - treasury", "search results",
                    "addition of", "federal register",
                    "sanctions list service", "office of foreign assets control",
                    "final rule", "interim final rule",
                ]

                # 1. 扁平化 + URL去重 + 过滤制裁数据库页面
                seen_urls = set()
                seen_titles = set()
                candidates = []
                for search in raw.get("searches", []):
                    for item in search.get("results", []):
                        u = item.get("url", "")
                        title = item.get("title", "").strip()
                        title_lower = title.lower()

                        # URL 去重
                        if not u or u in seen_urls:
                            continue

                        # 标题去重（忽略大小写和末尾来源标注的差异）
                        # 截取标题核心部分（去掉 " - Source Name" 后缀）
                        title_core = title_lower.split(" - ")[0].strip()[:60]
                        if title_core in seen_titles:
                            continue

                        # 过滤制裁数据库页面
                        is_db = False
                        for pat in SANCTIONS_DB_PATTERNS:
                            if pat in u.lower():
                                is_db = True
                                break
                        if not is_db:
                            for kw in SANCTIONS_TITLE_KEYWORDS:
                                if kw in title_lower:
                                    is_db = True
                                    break
                        if is_db:
                            continue

                        seen_urls.add(u)
                        seen_titles.add(title_core)
                        candidates.append(item)

                # 2. 验证 URL 可访问（过滤 404）
                print(f"  [NEWS] Verifying {len(candidates)} article URLs...")
                verified = []
                for item in candidates:
                    if len(verified) >= 15:
                        break
                    if verify_url(item.get("url", ""), timeout=5):
                        verified.append(item)
                    else:
                        print(f"  [NEWS] SKIP 404: {item.get('url', '')[:80]}")

                # 3. 提取日期并排序（最近的排最前）
                import re as _re
                from datetime import datetime as _dt
                from email.utils import parsedate_to_datetime as _parse_rfc2822
                for item in verified:
                    # Tavily 返回 published_date（RFC 2822 格式，如 'Fri, 04 Jul 2025 06:59:19 GMT'）
                    date_str = item.get("published_date", "")
                    item["_parsed_date"] = None
                    if date_str:
                        # RFC 2822 格式
                        try:
                            item["_parsed_date"] = _parse_rfc2822(date_str)
                        except Exception:
                            pass
                        # ISO 格式备选
                        if not item["_parsed_date"]:
                            for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"]:
                                try:
                                    item["_parsed_date"] = _dt.strptime(date_str[:19], fmt)
                                    break
                                except ValueError:
                                    continue
                    if not item["_parsed_date"]:
                        # 从 content 开头尝试提取日期
                        content = item.get("content", "")
                        m = _re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', content[:100])
                        if m:
                            try:
                                item["_parsed_date"] = _dt.strptime(m.group(1), "%Y-%m-%d")
                            except ValueError:
                                pass
                # 有日期的排前面，按日期倒序；无日期的排后面
                def _sort_key(x):
                    d = x.get("_parsed_date")
                    if d is None:
                        return _dt(2000, 1, 1)
                    # 统一移除 timezone info（避免 aware/naive 比较报错）
                    return d.replace(tzinfo=None) if d.tzinfo else d
                verified.sort(key=_sort_key, reverse=True)

                # 4. 生成 HTML
                if verified:
                    tavily_html = '<div id="tavily-news" class="section"><h1>Adverse Media & Intelligence</h1>'
                    for item in verified:
                        title = item.get("title", "")
                        url = item.get("url", "#")
                        snippet = item.get("content", item.get("snippet", ""))[:300]
                        pd = item.get("_parsed_date")
                        date_display = pd.strftime("%Y-%m-%d") if pd else ""
                        date_html = f'<span class="news-date">{date_display}</span>' if date_display else ""
                        tavily_html += f'''
                        <div class="news-card">
                            <h3><a href="{url}" target="_blank">{title}</a></h3>
                            {date_html}
                            <p>{snippet}</p>
                        </div>'''
                    tavily_html += "</div>"
                    print(f"  [NEWS] {len(verified)} verified articles included in report")
            except Exception as e:
                print(f"  [INFO] Tavily news not available: {e}")

            # ═══ AI 误中审查 HTML Section 生成 ═══
            fp_assessment_html = ""
            hit_results = [r for r in results if r["status"] == "hit"]
            if hit_results:
                fp_rows = ""
                for r in hit_results:
                    src = r.get("source", "")
                    src_name = f"{r.get('flag', '')} {r.get('name', src)}"
                    detail_text = r.get("detail", "")
                    detail_short = (detail_text[:120] + "...") if len(detail_text) > 120 else detail_text

                    if r.get("likely_fp"):
                        judgment = "🟡 疑似误中"
                        row_bg = "#fef9c3"
                        reason = r.get("fp_reason", "")
                        fp_ctx = r.get("fp_context", "")
                        if fp_ctx and fp_ctx != reason:
                            reason = fp_ctx
                    else:
                        judgment = "🔴 确认命中"
                        row_bg = "#fee2e2"
                        reason = "官方数据源确认命中"
                        if r.get("os_cross_validated"):
                            reason += " + OpenSanctions 交叉验证确认"
                        fp_ctx = r.get("fp_context")
                        if fp_ctx:
                            reason += f"<br/><small>{fp_ctx}</small>"

                    fp_rows += f'<tr style="background:{row_bg};"><td style="border:1px solid #e5e7eb;padding:8px;">{src_name}</td><td style="border:1px solid #e5e7eb;padding:8px;font-size:0.9em;">{detail_short}</td><td style="border:1px solid #e5e7eb;padding:8px;"><strong>{judgment}</strong></td><td style="border:1px solid #e5e7eb;padding:8px;">{reason}</td></tr>'

                confirmed_count = len([r for r in hit_results if not r.get("likely_fp")])
                fp_count = len([r for r in hit_results if r.get("likely_fp")])
                clean_count = len([r for r in results if r["status"] == "clean"])

                if confirmed_count > 0:
                    risk_conclusion = f"🛑 排除 {fp_count} 个疑似误中后，仍有 <strong>{confirmed_count}</strong> 个确认命中。实际风险等级：<strong>HIGH RISK</strong>"
                elif fp_count > 0:
                    risk_conclusion = f"🟡 全部 {fp_count} 个命中均为疑似误中，建议使用实体官方全名重新精确搜索确认。实际风险等级：<strong>REVIEW REQUIRED</strong>"
                else:
                    risk_conclusion = f"🟢 无命中记录。实际风险等级：<strong>CLEAR</strong>"

                fp_assessment_html = f'''<div class="section" id="ai-fp-assessment">
                    <h1>🧠 AI Compliance Analyst — False Positive Assessment</h1>
                    <p style="color:#78350f;font-size:0.9em;">本分析由 AI Agent 基于命中内容上下文、实体全名、命中类型、清单权威级别和跨清单语义综合判断自动生成。</p>
                    <table style="border-collapse:collapse;width:100%;font-size:0.9em;">
                        <thead><tr style="background:#fef3c7;">
                            <th style="border:1px solid #d97706;padding:8px;">源</th>
                            <th style="border:1px solid #d97706;padding:8px;">命中内容摘要</th>
                            <th style="border:1px solid #d97706;padding:8px;">判断</th>
                            <th style="border:1px solid #d97706;padding:8px;">理由</th>
                        </tr></thead>
                        <tbody>{fp_rows}</tbody>
                    </table>
                    <h3 style="color:#92400e;margin-top:20px;">综合风险评级（排除疑似误中后）</h3>
                    <p>{risk_conclusion}</p>
                    <p style="font-size:0.85em;color:#666;">共检查 {len(results)} 个制裁源 | 确认命中 {confirmed_count} | 疑似误中 {fp_count} | 清除 {clean_count}</p>
                </div>'''
                print(f"  [REPORT] AI FP Assessment section generated ({confirmed_count} confirmed, {fp_count} likely FP)")
            else:
                fp_assessment_html = ""

            now = datetime.now()

            # 封面「Search Variants」展示串。
            # ⚠️ 原写法：", ".join(results[0].get("searched", args.entity) ...)
            # searched 是字符串，join 会逐字符迭代，输出
            # "H, u, a, w, e, i, , T, e, c, h, n, o, l, o, g, i, e, s" 这类乱码。
            # 这里统一成：优先展示实际搜索过的名称集合，退回实体名。
            if results:
                _searched_raw = results[0].get("searched")
                if isinstance(_searched_raw, (list, tuple, set)):
                    _searched_list = [str(x) for x in _searched_raw if str(x).strip()]
                elif _searched_raw:
                    _searched_list = [str(_searched_raw)]
                else:
                    _searched_list = [args.entity]
            else:
                _searched_list = [args.entity]
            _searched_list = list(dict.fromkeys(_searched_list))  # 去重且保序
            searched_display = ", ".join(_searched_list)

            html_full = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sanctions Screening Report - {args.entity}</title>
<style>{REPORT_CSS}</style>
</head>
<body>

<!-- 侧栏目录 -->
<nav class="toc-sidebar">
    <h3>📋 Table of Contents</h3>
    <ul>
        <li><a href="#cover">🏠 Cover</a></li>
        <li><a href="#executive-summary">📊 Executive Summary</a></li>
    </ul>
    {"<div class=&quot;toc-section-title&quot;>OpenSanctions</div><ul><li><a href=&quot;#opensanctions&quot;>🔍 Cross-Validation</a></li></ul>" if opensanctions_html else ""}
    {"<div class=&quot;toc-section-title&quot;>FP Assessment</div><ul><li><a href=&quot;#ai-fp-assessment&quot;>🧠 误中审查</a></li></ul>" if fp_assessment_html else ""}
    <div class="toc-section-title">Verification Sources</div>
    <ul>
        {toc_html}
    </ul>
    <div class="toc-section-title">Additional</div>
    <ul>
        {"<li><a href='#tavily-news'>📰 Adverse Media</a></li>" if tavily_html else ""}
        <li><a href="#evidence-list">📎 Evidence List</a></li>
        <li><a href="#disclaimer">⚖️ Disclaimer</a></li>
    </ul>
</nav>

<!-- 主内容 -->
<div class="main-content">

    <!-- 封面 -->
    <div class="cover-page" id="cover">
        <div class="cover-logo">Global Compliance Intelligence</div>
        <div class="cover-doc-type">Executive Due Diligence</div>
        <h1 class="cover-title">Global Sanctions &amp;<br/>Export Controls<br/>Fact-Finding Report</h1>
        <div class="cover-subtitle">Target Entity: {args.entity}</div>
        <div class="cover-meta">
            <div><strong>PREPARED DATE</strong>{now.strftime('%B %d, %Y')}</div>
            <div><strong>SOURCES CHECKED</strong>{total} Official Databases</div>
            <div><strong>CLASSIFICATION</strong>Strictly Confidential</div>
        </div>
    </div>

    <!-- PDF 打印专用目录（屏幕隐藏，打印时显示为独立页） -->
    <div class="print-toc">
        <h2>Table of Contents</h2>
        <ol>
            {print_toc_html}
        </ol>
    </div>

    <!-- 执行摘要 -->
    <div class="exec-summary" id="executive-summary">
        <div class="exec-title">Executive Summary</div>
        <div class="risk-badge {"risk-high" if confirmed_hits else "risk-clear"}">{risk_level}</div>
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-num" id="exec-hits-count" style="color:{"var(--red)" if confirmed_hits else "var(--green)"};">{len(confirmed_hits)}</div>
                <div class="stat-label">Confirmed Hits</div>
            </div>
            <div class="stat-card">
                <div class="stat-num" style="color:#d4a017;">{len(likely_fps)}</div>
                <div class="stat-label">Likely FP</div>
            </div>
            <div class="stat-card">
                <div class="stat-num" id="exec-clear-count" style="color:var(--green);">{len(cleans)}</div>
                <div class="stat-label">Clear</div>
            </div>
            <div class="stat-card">
                <div class="stat-num">{total}</div>
                <div class="stat-label">Sources Checked</div>
            </div>
            <div class="stat-card">
                <div class="stat-num">{img_count}</div>
                <div class="stat-label">Evidence Screenshots</div>
            </div>
        </div>
        <p><strong>Entity:</strong> {args.entity}<br/>
        <strong>Search Variants:</strong> {searched_display}<br/>
        <strong>Report Date:</strong> {now.strftime('%Y-%m-%d %H:%M:%S')}</p>
        {f'<div style="margin-top:15px;padding:12px 18px;background:#eff6ff;border-left:4px solid #2563eb;border-radius:0 6px 6px 0;"><strong>🔍 OpenSanctions Cross-Validation:</strong> {len(os_results_list)} match(es) found in aggregated global sanctions database.{" Top hit: <strong>" + os_results_list[0]["name"] + "</strong>" if os_results_list else ""}</div>' if os_results_list else '<div style="margin-top:15px;padding:12px 18px;background:#f0fdf4;border-left:4px solid #22c55e;border-radius:0 6px 6px 0;"><strong>🔍 OpenSanctions Cross-Validation:</strong> No matches found in aggregated database.</div>'}
    </div>

    <!-- 报告正文 -->
    <div class="report-body">
        {opensanctions_html}

        {fp_assessment_html}

        {html_body}

        {tavily_html}

        <!-- 证据清单 -->
        <div class="section" id="evidence-list">
            <h1>📎 Evidence List</h1>
            <style>.badge-fp {{ color: #b45309; border: 1px solid #f59e0b; background: #fef9c3; }}</style>
            <table>
                <thead><tr><th>#</th><th>Source</th><th>Status</th><th>Screenshots</th><th>Detail</th></tr></thead>
                <tbody>
                {"".join(f'<tr data-source-key="{r.get("source","")}">' +
                    f'<td>{i+1}</td><td>{r.get("flag","")} {r.get("name","")}</td>' +
                    f'<td><span class="badge {"badge-fp" if r.get("likely_fp") else ("badge-hit" if r["status"]=="hit" else "badge-clean")}">' +
                    f'{"LIKELY FP" if r.get("likely_fp") else r["status"].upper()}</span></td>' +
                    f'<td>{len(r.get("screenshots",[]))}</td><td>{r.get("detail","")}</td></tr>' for i, r in enumerate(results))}
                </tbody>
            </table>
        </div>

        <!-- 免责声明 -->
        <div class="section" id="disclaimer">
            <h1>⚖️ Disclaimer</h1>
            <blockquote>
                This report is generated by an automated screening engine for reference purposes only and does not constitute legal advice.
                Sanctions lists are continuously updated; screening results are recommended to be valid for no more than 7 days.
                Final compliance decisions should be made in consultation with qualified legal counsel.
            </blockquote>
        </div>
    </div>

    <!-- 页脚 -->
    <div class="report-footer">
        Generated by Autonomous Screening Engine | &copy; {now.year} Global Intelligence Lab. Strictly Confidential.
    </div>

</div>

<script>
// 平滑滚动
document.querySelectorAll('.toc-sidebar a').forEach(a => {{
    a.addEventListener('click', e => {{
        e.preventDefault();
        const target = document.querySelector(a.getAttribute('href'));
        if (target) target.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
    }});
}});
// 滚动高亮当前章节
const sections = document.querySelectorAll('[id]');
const links = document.querySelectorAll('.toc-sidebar a');
window.addEventListener('scroll', () => {{
    let current = '';
    sections.forEach(s => {{
        if (window.scrollY >= s.offsetTop - 100) current = s.id;
    }});
    links.forEach(l => {{
        l.style.borderLeftColor = l.getAttribute('href') === '#' + current ? '#4da6ff' : 'transparent';
        l.style.color = l.getAttribute('href') === '#' + current ? 'white' : '';
    }});
}});
</script>
</body>
</html>"""

            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html_full)
            size_kb = os.path.getsize(report_path) / 1024
            print(f"  [REPORT] HTML report: {report_path} ({size_kb:.1f} KB, {img_count} images embedded)")

            # 自动生成 PDF（默认始终生成，--no-pdf 可跳过）
            pdf_path = report_path.replace(".html", ".pdf")
            try:
                if getattr(args, 'no_pdf', False):
                    print(f"  [PDF] Skipped (--no-pdf flag)")
                    raise Exception("skipped")
                from playwright.sync_api import sync_playwright as pw_sync
                print(f"  [PDF] Rendering PDF via Playwright...")
                html_abs = os.path.abspath(report_path).replace(os.sep, '/')
                with pw_sync() as pw_inst:
                    br = None
                    for ch in ["chrome", None]:
                        try:
                            kw = {"headless": True}
                            if ch:
                                kw["channel"] = ch
                            br = pw_inst.chromium.launch(**kw)
                            break
                        except Exception:
                            continue
                    if br:
                        pg = br.new_page()
                        pg.goto(f"file:///{html_abs}", wait_until="networkidle", timeout=60000)
                        pg.wait_for_timeout(2000)
                        # 隐藏 TOC 侧栏
                        pg.evaluate("""() => {
                            const toc = document.querySelector('.toc-sidebar');
                            if (toc) toc.style.display = 'none';
                            const main = document.querySelector('.main-content');
                            if (main) main.style.marginLeft = '0';
                        }""")
                        pg.wait_for_timeout(500)
                        pg.pdf(
                            path=pdf_path,
                            format="A4",
                            print_background=True,
                            margin={"top": "15mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                            display_header_footer=True,
                            header_template='<div style="font-size:7pt;color:#aaa;width:100%;text-align:center;padding:5px;">CONFIDENTIAL | LEGAL & COMPLIANCE FRAMEWORK</div>',
                            footer_template=f'<div style="font-size:8pt;width:100%;display:flex;justify-content:space-between;padding:5px 20px;color:#888;"><span>{now.strftime("%Y-%m-%d")}</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>',
                        )
                        br.close()
                        size_mb = os.path.getsize(pdf_path) / 1024 / 1024
                        print(f"  [PDF] OK: {pdf_path} ({size_mb:.1f} MB)")
                    else:
                        print(f"  [PDF] WARN: Cannot launch browser, PDF not generated")
            except Exception as e:
                if str(e) != "skipped":
                    print(f"  [PDF] WARN: PDF generation failed: {e}")
                    print(f"  [PDF] 可手动运行: python scripts/generate_report.py {md_path} --entity \"{args.entity}\"")

        except ImportError as e:
            print(f"  [WARN] Cannot generate HTML report: {e}")
            print(f"  [INFO] Use: python generate_report.py {md_path} --entity \"{args.entity}\"")

        print(f"\n{'='*60}")
        print(f"  Done! Files generated:")
        print(f"    Markdown: {md_path}")
        print(f"    HTML:     {report_path}")
        if os.path.exists(report_path.replace('.html', '.pdf')):
            print(f"    PDF:      {report_path.replace('.html', '.pdf')}")
        print(f"    Screenshots: {os.path.abspath(args.output_dir)}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
