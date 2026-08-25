"""تست سلامت مینی‌اپ — بدون نیاز به شبکه (تحلیل استاتیک HTML/JS)"""
import re

SRC = open("web_app.py", encoding="utf-8").read()


def _html_js():
    m = re.search(r'MINI_APP_HTML = """(.*?)"""\s*\n\n# ----', SRC, re.S)
    assert m, "MINI_APP_HTML پیدا نشد"
    html = m.group(1)
    js = re.findall(r"<script>(.*?)</script>", html, re.S)[-1]
    return html, js


def test_mini_app_html_exists():
    html, _ = _html_js()
    assert "<html" in html and "dir=\"rtl\"" in html


def test_all_getelementbyid_targets_exist():
    html, js = _html_js()
    ids = set(re.findall(r"getElementById\('([^']+)'\)", js))
    missing = [i for i in ids if f'id="{i}"' not in html]
    assert not missing, f"المان‌های JS در HTML وجود ندارند: {missing}"


def test_live_console_widgets_present():
    html, js = _html_js()
    for el in ("dash-live-console", "dash-live-added", "dash-live-skipped",
               "dash-live-remaining", "dash-live-failed", "dash-live-last",
               "dash-accounts-strip", "m-limited", "m-blocked"):
        assert f'id="{el}"' in html, f"المان {el} در HTML نیست"


def test_duplicate_ids_not_present():
    html, _ = _html_js()
    ids = re.findall(r'id="([^"]+)"', html)
    dups = {i for i in ids if ids.count(i) > 1}
    assert not dups, f"آیدی تکراری: {dups}"


def test_js_braces_balanced():
    _, js = _html_js()
    assert js.count("{") == js.count("}"), "تعداد آکولادها نامتوازن است"
