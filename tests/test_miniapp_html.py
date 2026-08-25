"""
اعتبارسنجی HTML مینی‌اپ.

پس‌زمینه (نسخه ۱.۴.۷):
مالک گزارش داد «کل مینی‌اپ کار نمی‌کند و فریز شده». سرور سالم بود و همه
endpointها زیر ۴۰۰ میلی‌ثانیه جواب می‌دادند — مشکل در خودِ HTML بود.

دکمه حذف اکانت این‌طور ساخته می‌شد:

    onclick="deleteAccount('${acc.phone}', '${(acc.name||'').replace(/'/g, "\\'")}')"

کوتیشن دوتایی داخل `.replace(...)` ویژگی `onclick="` را **زودتر می‌بست**.
از آن نقطه به بعد مرورگر بقیه را به‌عنوان ویژگی‌های بی‌معنی می‌خواند، DOM
خراب می‌شد و کل صفحه از کار می‌افتاد.

همان الگو از قبل در تب «لیدها» هم بود (`copyInviteMsg`).

درس: مقدار پویا هرگز نباید مستقیم داخل ویژگی onclick درون‌یابی شود.
راه‌حل: data-attribute + event delegation + فرار دادن با escAttr.
"""
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "web_app.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def html():
    match = re.search(r'MINI_APP_HTML = """(.*?)"""', SRC, re.S)
    assert match, "قالب MINI_APP_HTML پیدا نشد"
    return match.group(1)


def test_no_quote_breaking_onclick(html):
    """
    هسته باگ: هیچ onclick نباید کوتیشن دوتایی یا فراخوانی متد داخلش
    داشته باشد — ویژگی را می‌شکند و کل DOM را خراب می‌کند.
    """
    offenders = []
    for m in re.finditer(r'onclick="([^"]*)"', html):
        inner = m.group(1)
        if ".replace(" in inner or ".map(" in inner or ".filter(" in inner:
            line = html[: m.start()].count("\n") + 1
            offenders.append(f"خط ~{line}: {inner[:70]}")

    assert not offenders, (
        "این onclickها مقدار پویا را با فراخوانی متد درون‌یابی می‌کنند و "
        "می‌توانند ویژگی را بشکنند و مینی‌اپ را فریز کنند:\n  "
        + "\n  ".join(offenders)
        + "\nبه‌جایش data-attribute + event delegation استفاده کن."
    )


def test_script_tags_balanced(html):
    assert html.count("<script") == html.count("</script>")


def test_every_inline_handler_target_exists(html):
    """
    هر تابعی که در onclick/onchange صدا زده می‌شود باید تعریف شده باشد،
    وگرنه کلیک روی دکمه خطای کنسول می‌دهد و «کار نمی‌کند».
    """
    called = set(re.findall(r'on(?:click|change|input|submit)="(\w+)\(', html))
    defined = set(re.findall(r"(?:async\s+)?function\s+(\w+)", html))
    missing = sorted(called - defined)
    assert not missing, f"این توابع صدا زده می‌شوند ولی تعریف نشده‌اند: {missing}"


def test_delegated_handlers_have_matching_classes(html):
    """
    اگر هندلر با closest('.x') وصل شود ولی هیچ دکمه‌ای کلاس x نداشته
    باشد، دکمه بی‌صدا کار نمی‌کند.
    """
    delegated = set(re.findall(r"closest\('\.([\w-]+)'\)", html))
    assert delegated, "انتظار می‌رفت حداقل یک هندلر delegation وجود داشته باشد"

    for cls in delegated:
        assert re.search(rf'class="[^"]*\b{re.escape(cls)}\b', html), (
            f"هندلر روی .{cls} وصل شده ولی هیچ عنصری این کلاس را ندارد"
        )


def test_escape_helper_exists_and_is_used(html):
    """مقادیر پویا داخل data-attribute باید فرار داده شوند."""
    assert "function escAttr(" in html, "تابع فرار دادن escAttr تعریف نشده"
    for ch in ("&amp;", "&quot;", "&#39;", "&lt;"):
        assert ch in html, f"escAttr باید {ch} را پوشش دهد"

    # هر data-attribute پویا باید فرار داده شود — یا مستقیم با escAttr(...)
    # یا از طریق متغیری که خودش با escAttr ساخته شده.
    escaped_vars = set(re.findall(r"const\s+(\w+)\s*=\s*escAttr\(", html))
    offenders = []
    for m in re.finditer(r'data-(?:del-name|inv-title|inv-cat)="\$\{([^}]+)\}"', html):
        expr = m.group(1).strip()
        if expr.startswith("escAttr("):
            continue
        if expr in escaped_vars:
            continue
        offenders.append(expr)

    assert not offenders, (
        f"این data-attributeها فرار داده نشده‌اند: {offenders}. "
        f"(متغیرهای فرارداده‌شده: {sorted(escaped_vars)})"
    )


def test_delete_button_uses_data_attributes(html):
    """دکمهٔ حذف اکانت نباید مقدار را داخل onclick بگذارد.

    نام اکانت می‌تواند کوتیشن داشته باشد و HTML را بشکند؛ راه امن،
    data-attribute به‌همراه یک listener سراسری است.

    دکمهٔ «کپی پیام دعوت» قبلاً هم اینجا بررسی می‌شد، ولی همراه با تب CRM
    حذف شد — آن بخش به کار اصلی این ربات ربطی نداشت.
    """
    assert 'data-del-phone="' in html and 'class="btn-del-acc' in html
    assert 'onclick="deleteAccount(' not in html
    # بازماندهٔ قابلیت حذف‌شده نباید برگردد
    assert 'btn-copy-inv' not in html
    assert 'copyInviteMsg' not in html


def test_template_literals_balanced(html):
    """
    innerHTML با template literal ساخته می‌شود؛ بک‌تیک نامتوازن یعنی
    خطای نحوی جاوااسکریپت و مرگ کل صفحه.
    """
    in_script = "".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    assert in_script.count("`") % 2 == 0, "بک‌تیک نامتوازن در جاوااسکریپت"


def test_html_served_by_route_is_the_validated_template():
    """مطمئن شو همین قالبی که تست می‌کنیم واقعاً سرو می‌شود."""
    assert "MINI_APP_HTML" in SRC
    assert re.search(r"(return|write|body\s*=).*MINI_APP_HTML", SRC), (
        "MINI_APP_HTML باید توسط یک هندلر سرو شود"
    )

# ───────────────── پارس واقعی جاوااسکریپت ─────────────────

def test_javascript_actually_parses(html, tmp_path):
    """
    مهم‌ترین تست این فایل.

    تست‌های مبتنی بر regex بالا، باگی که کل مینی‌اپ را فریز کرد نگرفتند:
    یک `\n` در سورس پایتون به newline واقعی تبدیل شده بود و داخل رشته
    تک‌کوتیشنی جاوااسکریپت نشسته بود — که در JS نامعتبر است و کل بلوک
    script را می‌کشد. هیچ تبی کار نمی‌کرد.

    تنها راه مطمئن، پارس کردن با موتور واقعی است.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node در دسترس نیست")

    blocks = [b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if b.strip()]
    assert blocks, "هیچ بلوک جاوااسکریپتی پیدا نشد"

    for i, block in enumerate(blocks):
        path = tmp_path / f"block{i}.js"
        path.write_text(block, encoding="utf-8")
        proc = subprocess.run(
            [node, "--check", str(path)], capture_output=True, text=True, timeout=60
        )
        assert proc.returncode == 0, (
            f"بلوک script #{i} خطای نحوی دارد — کل مینی‌اپ از کار می‌افتد:\n"
            f"{proc.stderr[:600]}"
        )


