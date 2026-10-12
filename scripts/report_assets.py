#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
report_assets.py — 报告渲染所需的静态资源

从 browser_verify.py 的 main() 中原样外提，使该文件回归"采集"单一职责。
本模块只存放纯字符串常量，不含任何采集逻辑；改动时请确保与原实现逐字节一致。

REPORT_CSS  : 报告样式表
REPORT_JS   : 报告页交互脚本（目录高亮、打印优化）
"""

REPORT_CSS = """
:root {
    --navy: #00204a;
    --blue: #003d7a;
    --accent: #0066cc;
    --red: #c00000;
    --green: #00703c;
    --gray-bg: #f7f8fa;
    --gray-border: #e2e6ea;
}

* { box-sizing: border-box; }
body {
    font-family: 'Segoe UI', 'Helvetica Neue', Arial, 'Microsoft YaHei', sans-serif;
    color: #333; line-height: 1.7; font-size: 10pt; margin: 0; padding: 0;
    background: #f0f2f5;
}

/* 固定侧栏目录 */
.toc-sidebar {
    position: fixed; top: 0; left: 0;
    width: 260px; height: 100vh;
    background: var(--navy); color: white;
    overflow-y: auto; z-index: 100;
    padding: 20px 0;
    box-shadow: 3px 0 15px rgba(0,0,0,0.2);
}
.toc-sidebar h3 {
    color: #8db4d8; font-size: 10pt;
    text-transform: uppercase; letter-spacing: 2px;
    padding: 0 20px; margin: 0 0 15px 0;
    border: none;
}
.toc-sidebar ul { list-style: none; padding: 0; margin: 0; }
.toc-sidebar li a {
    display: block; padding: 8px 20px;
    color: #b0c4d8; text-decoration: none;
    font-size: 9.5pt; transition: all 0.2s;
    border-left: 3px solid transparent;
}
.toc-sidebar li a:hover {
    background: rgba(255,255,255,0.08);
    color: white; border-left-color: var(--accent);
}
.toc-sidebar .toc-section-title {
    padding: 15px 20px 5px; font-size: 8pt; color: #5a7a9a;
    text-transform: uppercase; letter-spacing: 1px;
}

/* 主内容 */
.main-content {
    margin-left: 260px; padding: 0;
    background: white; min-height: 100vh;
}

/* 封面 */
.cover-page {
    background: linear-gradient(135deg, var(--navy) 0%, var(--blue) 50%, #004a8c 100%);
    padding: 60px 50px;
    color: white; position: relative; overflow: hidden;
}
.cover-page::before {
    content: ''; position: absolute;
    top: -50%; right: -20%; width: 80%; height: 200%;
    background: radial-gradient(ellipse, rgba(255,255,255,0.03) 0%, transparent 70%);
}
.cover-logo { font-size: 11pt; letter-spacing: 3px; text-transform: uppercase; color: #6da3d0; margin-bottom: 40px; }
.cover-doc-type { font-size: 10pt; color: #4da6ff; letter-spacing: 3px; text-transform: uppercase; font-weight: bold; }
.cover-title {
    font-family: 'Georgia', 'Palatino Linotype', serif;
    font-size: 32pt; line-height: 1.2; margin: 15px 0; color: white; border: none;
}
.cover-subtitle { font-size: 16pt; color: #b0c8e0; font-weight: 300; margin-bottom: 30px; }
.cover-meta {
    display: flex; gap: 40px;
    border-top: 1px solid rgba(255,255,255,0.15);
    padding-top: 20px; font-size: 9pt; color: #8da4b8;
}
.cover-meta strong { display: block; color: #b0c8e0; margin-bottom: 3px; }

/* 执行摘要 */
.exec-summary {
    background: var(--gray-bg);
    padding: 35px 50px; margin: 0;
    border-bottom: 1px solid var(--gray-border);
}
.exec-title {
    font-family: 'Georgia', serif;
    font-size: 18pt; color: var(--navy); margin-bottom: 15px;
    padding-bottom: 10px; border-bottom: 2px solid var(--navy);
}
.risk-badge {
    display: inline-block; padding: 6px 18px;
    font-size: 12pt; font-weight: bold; letter-spacing: 1px;
    border-radius: 4px; margin: 10px 0;
}
.risk-high { background: #fff0f0; color: var(--red); border: 2px solid var(--red); }
.risk-clear { background: #ebf5f0; color: var(--green); border: 2px solid var(--green); }

/* 统计卡片 */
.stats-row { display: flex; gap: 20px; margin: 20px 0; }
.stat-card {
    flex: 1; padding: 18px; border-radius: 8px;
    text-align: center; border: 1px solid var(--gray-border);
    background: white;
}
.stat-card .stat-num { font-size: 28pt; font-weight: bold; line-height: 1; }
.stat-card .stat-label { font-size: 9pt; color: #666; text-transform: uppercase; letter-spacing: 1px; margin-top: 5px; }

/* 正文区域 */
.report-body { padding: 35px 50px; }

/* 源章节 */
.section { margin-bottom: 35px; }
.section h1 {
    font-family: 'Georgia', serif;
    font-size: 18pt; color: var(--navy);
    border-bottom: 2px solid var(--navy);
    padding-bottom: 8px; margin-top: 30px;
}
.section h2 {
    font-size: 13pt; color: var(--blue);
    border-bottom: 1px solid var(--gray-border);
    padding-bottom: 5px; margin-top: 20px;
}
.section h3 { font-size: 11pt; color: #444; text-transform: uppercase; letter-spacing: 0.5pt; }

/* 表格 */
table {
    width: 100%; border-collapse: collapse;
    margin: 15px 0; font-size: 9.5pt;
}
th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--gray-border); }
th {
    background: transparent; color: var(--navy); font-weight: bold;
    border-top: 2px solid var(--navy); border-bottom: 2px solid var(--navy);
    text-transform: uppercase; letter-spacing: 0.5pt; font-size: 8.5pt;
}
tbody tr:last-child td { border-bottom: 2px solid var(--navy); }
tr:nth-child(even) { background: #fafbfc; }

/* 图片 */
img {
    display: block; max-width: 95%; margin: 15px auto 5px;
    border: 1px solid #d0d0d0; border-radius: 4px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
.img-caption {
    text-align: center; font-size: 8.5pt; color: #888;
    margin-bottom: 20px; font-style: italic;
}

/* 徽章 */
.badge { display: inline-block; padding: 3px 8px; font-size: 8pt; font-weight: bold; letter-spacing: 0.5pt; text-transform: uppercase; border-radius: 3px; }
.badge-hit { color: var(--red); border: 1px solid var(--red); background: #fff0f0; }
.badge-clean { color: var(--green); border: 1px solid var(--green); background: #ebf5f0; }

/* 新闻卡片 */
.news-card {
    background: var(--gray-bg); border: 1px solid var(--gray-border);
    border-radius: 6px; padding: 15px 20px; margin: 10px 0;
}
.news-card h3 { font-size: 11pt; margin: 0 0 5px 0; border: none; }
.news-card h3 a { color: var(--blue); text-decoration: none; }
.news-card h3 a:hover { text-decoration: underline; }
.news-card .news-source { font-size: 8pt; color: #888; margin: 0 0 8px 0; }
.news-card p { font-size: 9.5pt; margin: 0; color: #555; }
.news-date { display: inline-block; font-size: 8pt; color: #0066cc; background: #e8f0fe; padding: 2px 8px; border-radius: 3px; margin-bottom: 6px; }

/* 引用 */
blockquote {
    background: var(--gray-bg); border-left: 4px solid var(--navy);
    padding: 10px 20px; margin: 15px 0; color: #555; font-style: italic;
    border-radius: 0 4px 4px 0;
}

/* 页脚 */
.report-footer {
    text-align: center; padding: 30px; font-size: 8pt; color: #aaa;
    border-top: 1px solid var(--gray-border);
}

/* 打印/PDF 优化 */
.print-toc { display: none; }  /* 打印专用目录，屏幕隐藏 */

@media print {
    .toc-sidebar { display: none !important; }
    .main-content { margin-left: 0 !important; }

    /* 打印专用目录页 */
    .print-toc {
        display: block !important;
        page-break-after: always;
        padding: 50px;
    }
    .print-toc h2 {
        font-family: 'Georgia', serif;
        font-size: 22pt; color: var(--navy);
        border-bottom: 3px solid var(--navy);
        padding-bottom: 10px; margin-bottom: 25px;
    }
    .print-toc ol {
        list-style: none; padding: 0; counter-reset: toc-counter;
    }
    .print-toc ol li {
        counter-increment: toc-counter;
        padding: 8px 0;
        border-bottom: 1px dotted #ccc;
        font-size: 11pt;
    }
    .print-toc ol li::before {
        content: counter(toc-counter) ". ";
        font-weight: bold; color: var(--navy);
    }
    .print-toc ol li a {
        color: var(--blue); text-decoration: none;
    }

    /* 封面独占一页 */
    .cover-page { page-break-after: always; }

    /* 执行摘要独占一页 */
    .exec-summary { page-break-after: always; }

    /* 每个 Section（源/新闻/证据/免责）单独起页 */
    .section { page-break-before: always; }

    /* 防止 h1/h2/h3 成为页面最后内容 */
    h1, h2, h3 {
        page-break-after: avoid;
        orphans: 3; widows: 3;
    }

    /* 图片不跨页，与标题保持一起 */
    img {
        page-break-inside: avoid;
        page-break-before: auto;
        max-height: 85vh;
    }
    .img-caption { page-break-before: avoid; }

    /* 表格行不断裂 */
    tr { page-break-inside: avoid; }

    /* 新闻卡片不断裂 */
    .news-card { page-break-inside: avoid; }

    /* 引用块不断裂 */
    blockquote { page-break-inside: avoid; }

    /* 紧凑间距（减少空白） */
    .section { margin-bottom: 15px; }
    .report-body { padding: 25px 40px; }
    .stats-row { gap: 10px; margin: 10px 0; }

    body { background: white; }
}
"""


# 报告页交互脚本。原先内嵌在 HTML 模板的 f-string 中，其中花括号被转义为
# {{ }}；这里以**未转义**形式保存，由调用方 .format() 或字符串替换使用，
# 避免与外层 f-string 的转义规则纠缠。
REPORT_JS = """
// 平滑滚动
document.querySelectorAll('.toc-sidebar a').forEach(a => {
    a.addEventListener('click', e => {
        e.preventDefault();
        const target = document.querySelector(a.getAttribute('href'));
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
});
// 滚动高亮当前章节
const sections = document.querySelectorAll('[id]');
const links = document.querySelectorAll('.toc-sidebar a');
window.addEventListener('scroll', () => {
    let current = '';
    sections.forEach(s => {
        if (window.scrollY >= s.offsetTop - 100) current = s.id;
    });
    links.forEach(l => {
        l.style.borderLeftColor = l.getAttribute('href') === '#' + current ? '#4da6ff' : 'transparent';
        l.style.color = l.getAttribute('href') === '#' + current ? 'white' : '';
    });
});
"""


__all__ = ["REPORT_CSS", "REPORT_JS"]
