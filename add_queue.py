"""ترتیب‌دهی صف ادد.

چرا جدا شد
-----------
این منطق داخل ``_execute_parallel_add`` (۷۲۸ خط) دفن شده بود، در حالی که خودش
کاملاً خالص است: ورودی لیست ممبر، خروجی لیست مرتب‌شده. جداکردنش یعنی می‌شود
تستش کرد بدون اینکه به تلگرام وصل شویم یا اکانتی را به خطر بیندازیم.

قاعده‌ها از کد اصلی آمده‌اند، نه از حدس:

* ``prefer_addable_members`` اول کسانی را می‌آورد که یوزرنیم دارند، چون ادد با
  یوزرنیم بسیار کمتر از ادد با آیدی به PEER_FLOOD می‌خورد.
* در حالت ``max`` ترتیب **شافل** می‌شود. دلیلش در کامنت کد اصلی آمده: مرتب‌سازی
  ترتیب ثابتی می‌داد و هر بار که عملیات از نو شروع می‌شد، همان هشت نفر ابتدای
  صف دوباره امتحان می‌شدند — همان‌هایی که دفعهٔ قبل هم شکست خورده بودند.
* شافل در ``max`` دو تکه است: هشتصد نفر اول جدا و بقیه جدا. این‌طور کیفیت
  (یوزرنیم‌دارها) حفظ می‌شود ولی تکرارِ همان سرِ صف از بین می‌رود.
"""

from __future__ import annotations

import random
from typing import Any, Callable, List, Optional, Sequence

#: مرز بین «سر صف باکیفیت» و بقیه در حالت max.
MAX_MODE_HEAD_SIZE = 800


def order_members_for_add(
    members: Sequence[Any],
    add_mode: str,
    prefer_addable: Callable[[Sequence[Any]], List[Any]],
    rng: Optional[random.Random] = None,
) -> List[Any]:
    """ترتیب نهایی ممبرها برای صف ادد.

    ``rng`` فقط برای تست تزریق می‌شود؛ در تولید همان ``random`` سراسری است.
    """
    ordered = list(prefer_addable(members))

    if add_mode != "max":
        # حالت‌های safe/fast/ultra ترتیب کیفی را دست‌نخورده نگه می‌دارند.
        return ordered

    shuffler = rng or random

    if len(ordered) > MAX_MODE_HEAD_SIZE:
        head = ordered[:MAX_MODE_HEAD_SIZE]
        tail = ordered[MAX_MODE_HEAD_SIZE:]
        shuffler.shuffle(head)
        shuffler.shuffle(tail)
        return head + tail

    shuffler.shuffle(ordered)
    return ordered
