"""مسیریاب کال‌بک‌های تلگرام.

چرا وجود دارد
--------------
تابع ``_cb_impl`` در ``bot.py`` سه هزار خط و ۱۲۸ شاخهٔ ``if`` پشت سر هم بود.
هر کلیک کاربر تمام شاخه‌ها را از بالا اسکن می‌کرد تا به مورد خودش برسد، و هر
تغییر کوچک ریسک شکستن یک شاخهٔ دیگر را داشت.

این ماژول فقط *انتخاب هندلر* را از *اجرای هندلر* جدا می‌کند. منطق داخل
هندلرها دست نخورده باقی می‌ماند؛ چیزی که عوض می‌شود این است که به‌جای اسکن
خطی، یک جست‌وجوی دیکشنری انجام می‌شود.

قواعد تطبیق
-----------
دو نوع شاخه در کد اصلی وجود داشت و ترتیبشان معنادار بود:

* ``d == "x"``            → تطبیق دقیق
* ``d.startswith("x_")``  → تطبیق پیشوندی

تطبیق دقیق **همیشه** بر پیشوندی مقدم است، و بین پیشوندها **بلندترین** پیشوند
برنده می‌شود. این دقیقاً همان چیزی است که ترتیب دستیِ کد قبلی تضمین می‌کرد:
مثلاً ``atk_target_manual`` باید قبل از ``atk_target_`` بررسی شود، که در کد
قبلی با شرط دستیِ ``and not d.startswith("atk_target_manual")`` نوشته شده بود.
اینجا این تضمین ساختاری است، نه دستی.
"""

from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, Optional, Tuple

Handler = Callable[..., Awaitable[None]]


class CallbackRouter:
    """جدول اعزام برای کال‌بک‌های اینلاین."""

    def __init__(self) -> None:
        self._exact: Dict[str, Handler] = {}
        self._prefix: List[Tuple[str, Handler]] = []
        self._prefix_sorted = True

    # ------------------------------------------------------------------ ثبت
    def exact(self, *keys: str) -> Callable[[Handler], Handler]:
        """ثبت هندلر برای یک یا چند کلید دقیق."""

        def decorator(fn: Handler) -> Handler:
            for key in keys:
                if key in self._exact:
                    raise ValueError(f"کلید تکراری در مسیریاب: {key!r}")
                self._exact[key] = fn
            return fn

        return decorator

    def prefix(self, *prefixes: str) -> Callable[[Handler], Handler]:
        """ثبت هندلر برای کال‌بک‌هایی که با پیشوند مشخص شروع می‌شوند."""

        def decorator(fn: Handler) -> Handler:
            for pre in prefixes:
                if any(existing == pre for existing, _ in self._prefix):
                    raise ValueError(f"پیشوند تکراری در مسیریاب: {pre!r}")
                self._prefix.append((pre, fn))
            self._prefix_sorted = False
            return fn

        return decorator

    # ------------------------------------------------------------------ تطبیق
    def resolve(self, data: str) -> Optional[Handler]:
        """هندلر متناظر با این کال‌بک، یا ``None`` اگر چیزی ثبت نشده باشد."""
        if data is None:
            return None

        handler = self._exact.get(data)
        if handler is not None:
            return handler

        # بلندترین پیشوند برنده است تا ``atk_target_manual`` پیش از
        # ``atk_target_`` بررسی شود؛ بدون این، شاخهٔ عمومی‌تر جلوی
        # شاخهٔ خاص‌تر را می‌گیرد.
        if not self._prefix_sorted:
            self._prefix.sort(key=lambda item: len(item[0]), reverse=True)
            self._prefix_sorted = True

        for pre, fn in self._prefix:
            if data.startswith(pre):
                return fn
        return None

    # ------------------------------------------------------------------ کمکی
    @property
    def exact_keys(self) -> List[str]:
        return sorted(self._exact)

    @property
    def prefixes(self) -> List[str]:
        return sorted((pre for pre, _ in self._prefix), key=len, reverse=True)

    def __len__(self) -> int:
        return len(self._exact) + len(self._prefix)
