from app.models import CouponRule
from app.core.config import get_settings
from app.services.proxy_tracker import _pick_coupon_set


def _rule(
    coupon_id: str,
    *,
    discount_type: str = "flat_jpy",
    discount_value: float = 0.0,
    is_stackable: bool = False,
    min_buy_price_jpy: int = 0,
    max_discount_jpy: int | None = None,
) -> CouponRule:
    return CouponRule(
        proxy_name="FromJapan",
        marketplace_source=None,
        coupon_id=coupon_id,
        discount_type=discount_type,
        discount_value=discount_value,
        min_buy_price_jpy=min_buy_price_jpy,
        max_discount_jpy=max_discount_jpy,
        is_stackable=is_stackable,
        priority=10,
        is_active=True,
    )


def test_exact_coupon_optimizer_dedupes_duplicate_coupon_ids():
    rules = [
        _rule("stack_dup", discount_value=400, is_stackable=True),
        _rule("stack_dup", discount_value=350, is_stackable=True),
        _rule("nonstack_1", discount_value=500, is_stackable=False),
        _rule("nonstack_2", discount_value=450, is_stackable=False),
    ]

    discount, coupon_id = _pick_coupon_set(
        rules,
        buy_price_jpy=30000,
        service_fee_jpy=1000,
    )

    assert discount == 900
    assert coupon_id == "nonstack_1+stack_dup"


def test_exact_coupon_optimizer_tie_breaks_by_coupon_id_for_equal_discount():
    rules = [
        _rule("stack_a", discount_value=300, is_stackable=True),
        _rule("stack_b", discount_value=200, is_stackable=True),
        _rule("nonstack_a", discount_value=500, is_stackable=False),
        _rule("nonstack_b", discount_value=500, is_stackable=False),
    ]

    discount, coupon_id = _pick_coupon_set(
        rules,
        buy_price_jpy=30000,
        service_fee_jpy=1000,
    )

    assert discount == 1000
    # Top discount ties between nonstack_a and nonstack_b with the same stackable set.
    # Deterministic lexical coupon-id order should pick nonstack_a.
    assert coupon_id == "nonstack_a+stack_a+stack_b"


def test_coupon_optimizer_caps_stackable_search_space(monkeypatch):
    monkeypatch.setenv("PROXY_COUPON_MAX_EXACT_STACKABLE", "4")
    monkeypatch.setenv("PROXY_COUPON_FALLBACK_TOP_STACKABLE", "3")
    get_settings.cache_clear()

    rules = [
        _rule(f"stack_{idx:02d}", discount_value=float(idx * 10), is_stackable=True)
        for idx in range(1, 21)
    ]
    rules.append(_rule("nonstack", discount_value=100.0, is_stackable=False))

    discount, coupon_id = _pick_coupon_set(
        rules,
        buy_price_jpy=100000,
        service_fee_jpy=2000,
    )

    assert discount == 670
    assert coupon_id == "nonstack+stack_18+stack_19+stack_20"
    get_settings.cache_clear()
