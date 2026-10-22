"""Screenshot only the checked-in, redacted Arc result files; no Azure access."""

import argparse
import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
CAPTURES = (
    ("management", "独立 SSH 管理通道"),
    ("isolation", "Arc Connected 与原生 IMDS 隔离"),
    ("sql-grant", "SQL 最小权限授权"),
    ("sql-query", "Arc 身份真实 SQL 查询"),
    ("final-state", "结束后解除分配，资源保留"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, help="Optional installed Chromium/Edge executable.")
    args = parser.parse_args()
    output = ROOT / "images"
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        options = {"headless": True}
        if args.browser:
            options["executable_path"] = str(args.browser)
        browser = playwright.chromium.launch(**options)
        try:
            page = browser.new_page(viewport={"width": 1360, "height": 900}, device_scale_factor=1)
            page.route("**/*", lambda route: route.abort())
            for name, title in CAPTURES:
                source = ROOT / "evidence" / "arc" / (name + ".json")
                value = json.loads(source.read_text(encoding="utf-8"))
                content = html.escape(json.dumps(value, indent=2, ensure_ascii=False))
                page.set_content(
                    '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    "<style>body{margin:28px;color:#111827;background:white;"
                    'font:19px/1.5 "Microsoft YaHei",sans-serif}'
                    "h1{font-size:23px;font-weight:500;margin:0 0 12px}"
                    "p{margin:4px 0}pre{margin-top:24px;white-space:pre-wrap;"
                    'overflow-wrap:anywhere;font:18px/1.5 Consolas,monospace}'
                    "footer{font-size:15px;color:#475569;margin-top:24px}</style>"
                    f"<h1>实测结果文件展示 | {html.escape(title)}（已脱敏）</h1>"
                    "<p>2026-10-22 已保存记录；未为截图重启 VM 或重新执行云测试。</p>"
                    "<p>这是结果文件展示，不是原始运行时终端或 Azure Portal 截图。</p>"
                    f"<pre>{content}</pre>"
                    f"<footer>来源：docs/evidence/arc/{name}.json；"
                    "环境标识以占位符替换，验证值保留。</footer></html>"
                )
                page.evaluate("document.fonts.ready")
                page.screenshot(path=str(output / ("arc-" + name + ".png")), full_page=True)
                print("Rendered " + str(output / ("arc-" + name + ".png")))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
