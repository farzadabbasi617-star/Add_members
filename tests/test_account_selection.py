"""انتخاب اکانت برای ادد موازی.

قبلاً ادد موازی همیشه هر اکانت آماده‌ای را برمی‌داشت و کاربر هیچ کنترلی
نداشت. این تست‌ها قرارداد جدید را قفل می‌کنند: کاربر انتخاب می‌کند،
بک‌اند فقط همان‌ها را استفاده می‌کند، و اگر هیچ‌کدام آماده نبودند دلیلش
گزارش می‌شود.
"""
import ast
import inspect
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = (ROOT / "web_app.py").read_text(encoding="utf-8")
TREE = ast.parse(WEB)


def _func(name):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"تابع {name} پیدا نشد")


def _src(node):
    return ast.get_source_segment(WEB, node)


def _js():
    html = re.search(r'MINI_APP_HTML = """(.*?)"""', WEB, re.S).group(1)
    return "\n".join(
        re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', html, re.S)
    )


JS = _js()


# ---------------------------------------------------------------- بک‌اند

def test_trigger_parallel_add_accepts_phones():
    """امضا باید پارامتر phones داشته باشد."""
    fn = _func("trigger_parallel_add")
    args = [a.arg for a in fn.args.args]
    assert "phones" in args, f"phones در امضا نیست: {args}"


def test_phones_defaults_to_none_for_backward_compat():
    """phones باید اختیاری باشد تا فراخوان‌های قدیمی نشکنند."""
    fn = _func("trigger_parallel_add")
    args = [a.arg for a in fn.args.args]
    idx = args.index("phones")
    n_defaults = len(fn.args.defaults)
    # پارامترهای دارای پیش‌فرض، انتهای لیست هستند
    first_with_default = len(args) - n_defaults
    assert idx >= first_with_default, "phones باید مقدار پیش‌فرض داشته باشد"
    default = fn.args.defaults[idx - first_with_default]
    assert isinstance(default, ast.Constant) and default.value is None


def test_wanted_set_is_normalized():
    """شماره‌ها باید strip و به str تبدیل شوند (ورودی از فرم HTML می‌آید)."""
    src = _src(_func("trigger_parallel_add"))
    assert "wanted" in src, "متغیر wanted تعریف نشده"
    m = re.search(r"wanted\s*=\s*\{([^}]+)\}", src)
    assert m, "wanted باید یک set comprehension باشد"
    body = m.group(1)
    assert "strip()" in body, "شماره‌ها باید strip شوند"
    assert "str(" in body, "شماره‌ها باید به str تبدیل شوند"


def test_filtering_happens_after_collect_ready_accounts():
    """فیلتر باید بعد از collect باشد تا وضعیت واقعی اکانت‌ها معلوم شود."""
    src = _src(_func("trigger_parallel_add"))
    i_collect = src.index("collect_ready_accounts()")
    i_filter = src.index("if wanted:")
    assert i_collect < i_filter, (
        "فیلتر اکانت‌های انتخابی باید بعد از collect_ready_accounts انجام شود، "
        "وگرنه دلیل آماده‌نبودن اکانت گزارش نمی‌شود"
    )


def test_empty_selection_reports_reason_and_returns():
    """اگر هیچ اکانت انتخابی آماده نبود، باید دلیل در live_status_text بیاید."""
    src = _src(_func("trigger_parallel_add"))
    m = re.search(r"if not chosen:(.*?)healthy_accs = chosen", src, re.S)
    assert m, "شاخه‌ی «هیچ اکانت انتخابی آماده نیست» وجود ندارد"
    branch = m.group(1)
    assert "skipped" in branch, "دلیل رد شدن باید از skipped خوانده شود"
    assert "live_status_text" in branch, "دلیل باید به UI گزارش شود"
    assert "return" in branch, "باید زودهنگام برگردد و ادد را شروع نکند"


def test_no_selection_uses_all_accounts():
    """وقتی phones خالی است، رفتار قبلی حفظ شود (همه اکانت‌ها)."""
    src = _src(_func("trigger_parallel_add"))
    # فیلتر باید داخل «if wanted:» باشد، نه بی‌قید‌و‌شرط
    assert re.search(r"if wanted:\s*\n\s*chosen\s*=", src), (
        "فیلتر باید مشروط به وجود انتخاب باشد تا حالت «همه» کار کند"
    )


@pytest.mark.parametrize("route_marker", [
    'data.get("phones")',      # روت aiohttp
    'post_data.get("phones")',  # dispatcher رشته‌ای
])
def test_both_routes_forward_phones(route_marker):
    """هر دو مسیر HTTP باید phones را پاس بدهند.

    درس گذشته: مینی‌اپ از dispatcher رشته‌ای رد می‌شد و ویژگی‌ای که فقط
    در روت aiohttp اضافه شده بود عملاً هرگز اجرا نمی‌شد.
    """
    assert route_marker in WEB, f"{route_marker} در web_app.py نیست"


def test_no_caller_of_trigger_parallel_add_is_stale():
    """هیچ فراخوانی نباید بدون phones مانده باشد."""
    calls = re.findall(r"trigger_parallel_add\(([^)]*)\)", WEB)
    calls = [c for c in calls if "def " not in c]
    for c in calls:
        assert "phones" in c, f"فراخوانی بدون phones: trigger_parallel_add({c})"


# ---------------------------------------------------------------- UI

@pytest.mark.parametrize("el", [
    'id="parallel-acc-list"',
    'id="parallel-acc-summary"',
    'parallelAccAll(true)',
    'parallelAccAll(false)',
])
def test_ui_elements_exist(el):
    assert el in WEB, f"عنصر UI غایب: {el}"


@pytest.mark.parametrize("fn", [
    "function renderParallelAccounts",
    "function currentParallelPhones",
    "function parallelAccAll",
    "function updateParallelAccSummary",
])
def test_js_functions_exist(fn):
    assert fn in JS, f"تابع JS غایب: {fn}"


def test_render_is_called_after_loading_accounts():
    """بدون این فراخوانی فهرست هرگز پر نمی‌شود."""
    m = re.search(r"async function loadAttackAccounts\(\)\s*\{(.*?)\n        \}", JS, re.S)
    assert m, "loadAttackAccounts پیدا نشد"
    assert "renderParallelAccounts(" in m.group(1), (
        "loadAttackAccounts باید renderParallelAccounts را صدا بزند"
    )


def test_disabled_accounts_are_never_submitted():
    """اکانت غیرقابل‌استفاده نباید در payload برود."""
    m = re.search(r"function currentParallelPhones\(\)\s*\{(.*?)\n        \}", JS, re.S)
    assert m, "currentParallelPhones پیدا نشد"
    body = m.group(1)
    assert "cb.checked" in body and "!cb.disabled" in body, (
        "فقط چک‌باکس‌های تیک‌خورده و فعال باید ارسال شوند"
    )


def test_start_parallel_sends_phones():
    m = re.search(r"async function startParallelAdd\(\)\s*\{(.*?)\n        \}", JS, re.S)
    assert m, "startParallelAdd پیدا نشد"
    body = m.group(1)
    assert "phones: phones" in body, "phones باید در بدنه‌ی درخواست باشد"
    assert "currentParallelPhones()" in body, "انتخاب کاربر باید خوانده شود"


def test_start_parallel_blocks_empty_selection():
    """انتخاب خالی نباید به سرور برود."""
    m = re.search(r"async function startParallelAdd\(\)\s*\{(.*?)\n        \}", JS, re.S)
    body = m.group(1)
    guard = body.index("if (!phones.length)")
    fetch = body.index("fetch(")
    assert guard < fetch, "گارد انتخاب خالی باید قبل از fetch باشد"


def test_accounts_tab_shortcut_populates_list_first():
    """میان‌بر تب اکانت‌ها نباید با فهرست خالی به گارد بخورد."""
    m = re.search(
        r"async function startParallelAddFromAccounts\(\)\s*\{(.*?)\n        \}", JS, re.S
    )
    assert m, "startParallelAddFromAccounts پیدا نشد"
    body = m.group(1)
    assert "loadAttackAccounts()" in body, (
        "میان‌بر باید ابتدا فهرست را پر کند وگرنه currentParallelPhones خالی است"
    )
    assert "parallelAccAll(true)" in body, "میان‌بر یعنی «همه اکانت‌ها»"


def test_phone_numbers_render_ltr():
    """شماره تلفن در صفحه‌ی RTL باید LTR نمایش داده شود."""
    m = re.search(r"function renderParallelAccounts\((.*?)\n        \}", JS, re.S)
    body = m.group(1)
    assert "'dir', 'ltr'" in body or 'dir="ltr"' in body, "شماره باید dir=ltr باشد"


def test_render_uses_dom_api_not_string_concat_for_phone():
    """نام اکانت از تلگرام می‌آید؛ با textContent درج شود نه innerHTML."""
    m = re.search(r"function renderParallelAccounts\((.*?)\n        \}", JS, re.S)
    body = m.group(1)
    assert "textContent" in body, (
        "نام/شماره باید با textContent درج شوند تا تزریق HTML ممکن نباشد"
    )


def test_previous_selection_survives_refresh():
    """رفرش دوره‌ای نباید تیک‌های کاربر را پاک کند."""
    m = re.search(r"function renderParallelAccounts\((.*?)\n        \}", JS, re.S)
    body = m.group(1)
    assert "prev" in body and "hadPrev" in body, (
        "انتخاب قبلی باید قبل از بازسازی فهرست خوانده و بازگردانده شود"
    )


def test_js_actually_parses():
    """اعتبارسنجی واقعی نحو JS با node.

    هیوریستیک شمردن کوتیشن روی خطوطی مثل .replace(/'/g, '&#39;') مثبت
    کاذب می‌داد. تنها راه قابل‌اعتماد، پارس کردن واقعی است.
    درس: یک \\n در سورس پایتونِ حاوی JS به newline واقعی تبدیل می‌شود و
    رشته‌ی JS را می‌شکند — یک بار کل مینی‌اپ را فریز کرد.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if not node:
        pytest.skip("node نصب نیست")
    with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as f:
        f.write(JS)
        path = f.name
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert r.returncode == 0, f"JS نامعتبر:\n{r.stderr}"
    finally:
        pathlib.Path(path).unlink(missing_ok=True)
