"""مسیریاب مراحل گفت‌وگو (conversation steps).

چرا وجود دارد
--------------
تابع ``_steps_impl`` در ``bot.py`` هزار و صد خط بود: زنجیره‌ای از ۲۳ شاخه که
روی ``atk_state["step"]`` تصمیم می‌گرفت کدام مرحلهٔ گفت‌وگو اجرا شود.

برخلاف مسیریاب کال‌بک، اینجا تطبیق فقط با نام مرحله نیست. چند شاخه **گارد**
دارند — مثلاً ``step == "upload_session" and m.document`` — و اگر گارد رد شود
پیام باید به شاخه‌های بعدی بیفتد. حذف این جزئیات یعنی کاربری که به‌جای فایل
متن می‌فرستد، در حالت آپلود گیر می‌کند و هیچ پیام خطایی هم نمی‌گیرد.

پس این مسیریاب گارد را به‌عنوان جزء درجه‌یک نگه می‌دارد: هندلری انتخاب می‌شود
که هم نام مرحله‌اش بخواند و هم گاردش برقرار باشد.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

Handler = Callable[..., Awaitable[None]]
Guard = Callable[[Any], bool]


def has_document(m: Any) -> bool:
    """پیام باید فایل داشته باشد."""
    return bool(getattr(m, "document", None))


def has_text(m: Any) -> bool:
    """پیام باید متن داشته باشد."""
    return bool(getattr(m, "text", None))


class StepRouter:
    """جدول اعزام برای مراحل گفت‌وگو، با پشتیبانی از گارد."""

    def __init__(self) -> None:
        # هر مرحله می‌تواند چند هندلر داشته باشد که به ترتیب ثبت امتحان می‌شوند؛
        # همان ترتیبی که در زنجیرهٔ if اصلی وجود داشت.
        self._steps: Dict[str, List[Tuple[Optional[Guard], Handler]]] = {}

    def step(self, *names: str, guard: Optional[Guard] = None) -> Callable[[Handler], Handler]:
        """ثبت هندلر برای یک یا چند نام مرحله.

        ``guard`` شرط اضافی روی پیام است. اگر برقرار نباشد، این هندلر رد
        می‌شود و هندلر بعدیِ همان مرحله (در صورت وجود) امتحان می‌شود — دقیقاً
        مثل رفتار ``and`` در زنجیرهٔ اصلی.
        """

        def decorator(fn: Handler) -> Handler:
            for name in names:
                bucket = self._steps.setdefault(name, [])
                if guard is None and any(g is None for g, _ in bucket):
                    raise ValueError(
                        f"مرحلهٔ {name!r} دو هندلر بدون گارد دارد؛ دومی هرگز اجرا نمی‌شود"
                    )
                bucket.append((guard, fn))
            return fn

        return decorator

    def resolve(self, step: Optional[str], message: Any) -> Optional[Handler]:
        """هندلر مناسب این مرحله و این پیام، یا ``None``."""
        if not step:
            return None
        for guard, fn in self._steps.get(step, ()):
            if guard is None or guard(message):
                return fn
        return None

    @property
    def steps(self) -> List[str]:
        return sorted(self._steps)

    def __len__(self) -> int:
        return sum(len(v) for v in self._steps.values())
