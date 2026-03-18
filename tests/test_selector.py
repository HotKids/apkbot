"""test_selector.py — selector 模块单元测试

覆盖所有核心场景：
  - APK vs BUNDLE 选择逻辑
  - 硬过滤（签名 AND、架构 OR、DPI、device_type 容错、Android 范围、关键词）
  - 打分（prefer_apk、架构权重、版本号决胜）
  - 辅助函数（normalize_list_env、maybe_parse_int、config_from_env）
  - 边界情况（空列表、全被过滤、平局决胜）
"""

import pytest

from selector import (
    SelectorConfig,
    Variant,
    config_from_env,
    filter_variant,
    maybe_parse_int,
    normalize_list_env,
    score_variant,
    select_best_variant,
)


# ---------------------------------------------------------------------------
# 测试夹具（helpers）
# ---------------------------------------------------------------------------

def make_apk(**kwargs) -> Variant:
    """构造一个通用 APK 变体，方便单测覆盖局部字段。"""
    defaults = dict(
        type="APK",
        is_bundle=False,
        architectures=["arm64-v8a"],
        signatures=["ab12"],
        dpi="nodpi",
        device_type="universal",
        min_android_api=21,
        version_code=1_000_000,
        variant_url="https://apkmirror.com/apk/foo",
    )
    defaults.update(kwargs)
    return Variant(**defaults)


def make_bundle(**kwargs) -> Variant:
    """构造一个通用 BUNDLE 变体。"""
    defaults = dict(
        type="BUNDLE",
        is_bundle=True,
        architectures=["arm64-v8a"],
        signatures=["ab12"],
        dpi="nodpi",
        device_type="universal",
        min_android_api=21,
        version_code=1_000_000,
        variant_url="https://apkmirror.com/bundle/foo",
    )
    defaults.update(kwargs)
    return Variant(**defaults)


# 默认宽松配置（allow_bundle=True，不加任何硬性条件）
_default_cfg = SelectorConfig()


# ===========================================================================
# 场景 1：有 APK 和 BUNDLE，同时都满足条件 → 应优先选 APK
# ===========================================================================

def test_apk_preferred_over_bundle_when_both_pass():
    """prefer_apk=True 时，APK 因 +1000 打分远高于 BUNDLE，应被选中。"""
    apk = make_apk()
    bundle = make_bundle()
    result = select_best_variant([apk, bundle], _default_cfg)
    assert result is apk


def test_apk_preferred_regardless_of_order():
    """无论 BUNDLE 在列表前还是后，都应选 APK。"""
    apk = make_apk()
    bundle = make_bundle()
    assert select_best_variant([bundle, apk], _default_cfg) is apk
    assert select_best_variant([apk, bundle], _default_cfg) is apk


# ===========================================================================
# 场景 2：没有 APK，只有 BUNDLE，allow_bundle=True → 应选 BUNDLE
# ===========================================================================

def test_bundle_selected_when_no_apk_and_allowed():
    """无 APK 时，allow_bundle=True 应 fallback 到 BUNDLE。"""
    bundle = make_bundle()
    cfg = SelectorConfig(allow_bundle=True)
    result = select_best_variant([bundle], cfg)
    assert result is bundle


def test_multiple_bundles_selects_best():
    """多个 BUNDLE 时，应按打分选出最佳。"""
    b_arm64 = make_bundle(architectures=["arm64-v8a"])
    b_x86 = make_bundle(architectures=["x86"])
    cfg = SelectorConfig(allow_bundle=True)
    result = select_best_variant([b_x86, b_arm64], cfg)
    assert result is b_arm64  # arm64-v8a 打分更高


# ===========================================================================
# 场景 3：没有 APK，只有 BUNDLE，allow_bundle=False → 应返回 None
# ===========================================================================

def test_bundle_rejected_when_not_allowed():
    """allow_bundle=False 时，BUNDLE 被硬过滤，返回 None。"""
    bundle = make_bundle()
    cfg = SelectorConfig(allow_bundle=False)
    result = select_best_variant([bundle], cfg)
    assert result is None


def test_mixed_apk_and_bundle_allow_bundle_false():
    """allow_bundle=False 时，只有 APK 通过过滤，应选 APK。"""
    apk = make_apk()
    bundle = make_bundle()
    cfg = SelectorConfig(allow_bundle=False)
    result = select_best_variant([apk, bundle], cfg)
    assert result is apk


# ===========================================================================
# 场景 4：required_signatures 为 AND → 缺少任一签名应被过滤
# ===========================================================================

def test_required_signatures_and_partial_fails():
    """只命中部分签名时，变体应被过滤。"""
    cfg = SelectorConfig(required_signatures=["3891", "bd32"])
    v = make_apk(signatures=["3891"])                    # 缺少 bd32
    assert filter_variant(v, cfg) is False


def test_required_signatures_and_all_pass():
    """同时命中所有签名时，变体应通过。"""
    cfg = SelectorConfig(required_signatures=["3891", "bd32"])
    v = make_apk(signatures=["3891", "bd32", "xxxx"])    # 额外签名无妨
    assert filter_variant(v, cfg) is True


def test_required_signatures_and_in_selection():
    """只有满足全部签名的变体才能被选中。"""
    cfg = SelectorConfig(required_signatures=["3891", "bd32"])
    v_partial = make_apk(signatures=["3891"])
    v_full = make_apk(signatures=["3891", "bd32"])
    result = select_best_variant([v_partial, v_full], cfg)
    assert result is v_full


# ===========================================================================
# 场景 5：required_architectures 为 OR → 命中任意一个即可
# ===========================================================================

def test_required_architectures_or_arm64_passes():
    cfg = SelectorConfig(required_architectures=["arm64-v8a", "armeabi-v7a"])
    assert filter_variant(make_apk(architectures=["arm64-v8a"]), cfg) is True


def test_required_architectures_or_armv7_passes():
    cfg = SelectorConfig(required_architectures=["arm64-v8a", "armeabi-v7a"])
    assert filter_variant(make_apk(architectures=["armeabi-v7a"]), cfg) is True


def test_required_architectures_or_x86_fails():
    """x86 不在要求列表里，应被过滤。"""
    cfg = SelectorConfig(required_architectures=["arm64-v8a", "armeabi-v7a"])
    assert filter_variant(make_apk(architectures=["x86"]), cfg) is False


def test_required_architectures_empty_allows_all():
    """不配置 required_architectures 时，架构字段不参与过滤。"""
    cfg = SelectorConfig(required_architectures=[])
    assert filter_variant(make_apk(architectures=["x86"]), cfg) is True


# ===========================================================================
# 场景 6：两个 APK 都满足条件 → Android 要求更低者优先
# ===========================================================================

def test_lower_android_api_preferred():
    """prefer_lower_android=True 时，API 等级越低得分越高。"""
    cfg = SelectorConfig(prefer_lower_android=True)
    v_low = make_apk(min_android_api=21, version_code=1_000_000)
    v_high = make_apk(min_android_api=28, version_code=1_000_000)
    result = select_best_variant([v_low, v_high], cfg)
    assert result is v_low


def test_lower_android_disabled_not_a_factor():
    """prefer_lower_android=False 时，Android API 不影响打分（仅靠 version_code 决胜）。"""
    cfg = SelectorConfig(prefer_lower_android=False, prefer_universal=False,
                         prefer_multi_signature=False)
    v_low = make_apk(min_android_api=21, version_code=1_000_000)
    v_high = make_apk(min_android_api=28, version_code=2_000_000)
    result = select_best_variant([v_low, v_high], cfg)
    assert result is v_high  # version_code 更大的胜出


# ===========================================================================
# 场景 7：两个 APK 条件接近 → version_code 更高者优先
# ===========================================================================

def test_higher_version_code_preferred():
    """version_code 更大（更新）的变体应被优先选中。"""
    v_old = make_apk(version_code=1_000_000, min_android_api=21)
    v_new = make_apk(version_code=2_000_000, min_android_api=21)
    result = select_best_variant([v_old, v_new], _default_cfg)
    assert result is v_new


def test_version_code_none_lower_priority():
    """没有 version_code 的变体在平局时应排在有 version_code 的后面。"""
    v_with = make_apk(version_code=1_000_000, min_android_api=21,
                      architectures=["x86"])  # 低架构分，确保平局
    v_none = make_apk(version_code=None, min_android_api=21,
                      architectures=["x86"])
    cfg = SelectorConfig(prefer_lower_android=False, prefer_universal=False)
    result = select_best_variant([v_none, v_with], cfg)
    assert result is v_with


# ===========================================================================
# 场景 8：exclude_keywords 生效
# ===========================================================================

def test_exclude_keywords_in_variant_label():
    """exclude_keyword 命中 variant_label 时应被过滤。"""
    cfg = SelectorConfig(exclude_keywords=["beta"])
    v = make_apk(variant_label="arm64-v8a beta build", raw_text="")
    assert filter_variant(v, cfg) is False


def test_exclude_keywords_in_raw_text():
    """exclude_keyword 命中 raw_text 时应被过滤。"""
    cfg = SelectorConfig(exclude_keywords=["preview"])
    v = make_apk(variant_label="stable", raw_text="preview candidate arm64")
    assert filter_variant(v, cfg) is False


def test_exclude_keywords_case_insensitive():
    """关键词匹配应不区分大小写。"""
    cfg = SelectorConfig(exclude_keywords=["BETA"])
    v = make_apk(variant_label="beta build", raw_text="")
    assert filter_variant(v, cfg) is False


def test_exclude_keywords_no_match_passes():
    """未命中排除词时，变体应通过。"""
    cfg = SelectorConfig(exclude_keywords=["beta", "preview"])
    v = make_apk(variant_label="stable release", raw_text="arm64 nodpi")
    assert filter_variant(v, cfg) is True


def test_exclude_keywords_in_selection():
    """selection 中只有未命中排除词的变体能被选中。"""
    cfg = SelectorConfig(exclude_keywords=["beta"])
    v_stable = make_apk(variant_label="stable")
    v_beta = make_apk(variant_label="beta build")
    result = select_best_variant([v_stable, v_beta], cfg)
    assert result is v_stable


# ===========================================================================
# 场景 9：match_keywords 生效
# ===========================================================================

def test_match_keywords_passes_when_hit():
    """命中 match_keywords 中任意一个关键词时通过。"""
    cfg = SelectorConfig(match_keywords=["arm64", "universal"])
    v_arm = make_apk(variant_label="arm64-v8a", raw_text="")
    v_uni = make_apk(variant_label="universal", raw_text="")
    assert filter_variant(v_arm, cfg) is True
    assert filter_variant(v_uni, cfg) is True


def test_match_keywords_fails_when_none_hit():
    """一个关键词都没命中时被过滤。"""
    cfg = SelectorConfig(match_keywords=["arm64", "universal"])
    v = make_apk(variant_label="x86 build", raw_text="x86 only")
    assert filter_variant(v, cfg) is False


def test_match_keywords_case_insensitive():
    cfg = SelectorConfig(match_keywords=["ARM64"])
    v = make_apk(variant_label="arm64-v8a build", raw_text="")
    assert filter_variant(v, cfg) is True


def test_match_keywords_in_selection():
    cfg = SelectorConfig(match_keywords=["arm64"])
    v_match = make_apk(variant_label="arm64 build")
    v_no = make_apk(variant_label="x86 build", raw_text="x86")
    result = select_best_variant([v_match, v_no], cfg)
    assert result is v_match


# ===========================================================================
# 打分逻辑测试
# ===========================================================================

def test_apk_gets_1000_bonus_when_prefer_apk():
    """prefer_apk=True 时 APK 应获得 +1000 加分。"""
    cfg = SelectorConfig(prefer_apk=True, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=False)
    apk = make_apk(min_android_api=21, dpi=None, device_type=None,
                   architectures=[], signatures=["a"])
    s = score_variant(apk, cfg)
    assert s >= 1000.0


def test_bundle_gets_no_apk_bonus():
    """BUNDLE 不应获得 prefer_apk 的 +1000 加分。"""
    cfg = SelectorConfig(prefer_apk=True, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=False)
    bundle = make_bundle(min_android_api=21, dpi=None, device_type=None,
                         architectures=[], signatures=["a"])
    s = score_variant(bundle, cfg)
    assert s < 1000.0


def test_prefer_apk_false_no_1000_bonus():
    """prefer_apk=False 时，APK 不应获得 +1000 加分。"""
    cfg = SelectorConfig(prefer_apk=False, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=False)
    apk = make_apk(min_android_api=21, dpi=None, device_type=None,
                   architectures=[], signatures=["a"])
    s = score_variant(apk, cfg)
    assert s < 1000.0


def test_architecture_scoring_order():
    """架构打分应满足：arm64-v8a > armeabi-v7a > x86_64 > x86。"""
    cfg = SelectorConfig(prefer_apk=False, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=False)
    arm64 = make_apk(architectures=["arm64-v8a"], dpi=None, device_type=None)
    armv7 = make_apk(architectures=["armeabi-v7a"], dpi=None, device_type=None)
    x64 = make_apk(architectures=["x86_64"], dpi=None, device_type=None)
    x86 = make_apk(architectures=["x86"], dpi=None, device_type=None)
    scores = [score_variant(v, cfg) for v in [arm64, armv7, x64, x86]]
    assert scores[0] > scores[1] > scores[2] > scores[3]


def test_multi_sig_bonus():
    """prefer_multi_signature=True 时，多签名变体应获得加分。"""
    cfg = SelectorConfig(prefer_apk=False, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=True)
    single = make_apk(signatures=["ab12"], architectures=["x86"])
    multi = make_apk(signatures=["ab12", "cd34"], architectures=["x86"])
    assert score_variant(multi, cfg) > score_variant(single, cfg)


def test_required_signatures_score_bonus():
    """命中 required_signatures 的每个签名应获得 +50 加分。"""
    cfg = SelectorConfig(required_signatures=["3891", "bd32"],
                         prefer_apk=False, prefer_lower_android=False,
                         prefer_universal=False, prefer_multi_signature=False)
    v = make_apk(signatures=["3891", "bd32"], architectures=[],
                 dpi=None, device_type=None)
    s = score_variant(v, cfg)
    # 2 个签名命中各 +50 = 100
    assert s >= 100.0


# ===========================================================================
# device_type 容错匹配
# ===========================================================================

def test_device_type_exact_match():
    cfg = SelectorConfig(required_device_type="universal")
    v = make_apk(device_type="universal")
    assert filter_variant(v, cfg) is True


def test_device_type_in_architectures():
    """device_type 可从 architectures 中容错匹配。"""
    cfg = SelectorConfig(required_device_type="universal")
    v = make_apk(device_type=None, architectures=["universal"])
    assert filter_variant(v, cfg) is True


def test_device_type_in_raw_text():
    """device_type 可从 raw_text 中容错匹配（不区分大小写）。"""
    cfg = SelectorConfig(required_device_type="universal")
    v = make_apk(device_type=None, architectures=[], raw_text="Universal APK nodpi")
    assert filter_variant(v, cfg) is True


def test_device_type_no_match_fails():
    cfg = SelectorConfig(required_device_type="universal")
    v = make_apk(device_type="phone", architectures=["arm64-v8a"], raw_text="phone only")
    assert filter_variant(v, cfg) is False


# ===========================================================================
# Android API 范围过滤
# ===========================================================================

def test_android_floor_pass():
    cfg = SelectorConfig(min_android_floor=21)
    assert filter_variant(make_apk(min_android_api=21), cfg) is True
    assert filter_variant(make_apk(min_android_api=28), cfg) is True


def test_android_floor_fail():
    cfg = SelectorConfig(min_android_floor=21)
    assert filter_variant(make_apk(min_android_api=19), cfg) is False


def test_android_ceiling_pass():
    cfg = SelectorConfig(min_android_ceiling=28)
    assert filter_variant(make_apk(min_android_api=21), cfg) is True
    assert filter_variant(make_apk(min_android_api=28), cfg) is True


def test_android_ceiling_fail():
    cfg = SelectorConfig(min_android_ceiling=28)
    assert filter_variant(make_apk(min_android_api=33), cfg) is False


def test_android_range_none_api_fails():
    """配置了范围但变体无 API 信息时，应被过滤。"""
    cfg = SelectorConfig(min_android_floor=21)
    assert filter_variant(make_apk(min_android_api=None), cfg) is False


# ===========================================================================
# 边界情况
# ===========================================================================

def test_empty_variants_returns_none():
    assert select_best_variant([], _default_cfg) is None


def test_all_filtered_returns_none():
    cfg = SelectorConfig(required_signatures=["xxxx"])
    variants = [make_apk(signatures=["ab12"]), make_bundle(signatures=["cd34"])]
    assert select_best_variant(variants, cfg) is None


def test_single_variant_returned_directly():
    v = make_apk()
    result = select_best_variant([v], _default_cfg)
    assert result is v


def test_tiebreak_prefers_earlier_in_list():
    """完全相同的两个变体，应选列表中靠前的那个。"""
    cfg = SelectorConfig(prefer_lower_android=False, prefer_universal=False,
                         prefer_multi_signature=False)
    v1 = make_apk(version_code=1_000_000, min_android_api=21,
                  architectures=["x86"], dpi=None, device_type=None, signatures=["a"])
    v2 = make_apk(version_code=1_000_000, min_android_api=21,
                  architectures=["x86"], dpi=None, device_type=None, signatures=["a"])
    result = select_best_variant([v1, v2], cfg)
    assert result is v1


def test_tiebreak_variant_url_present_wins():
    """平局时，有 variant_url 的应优先于无 URL 的。"""
    cfg = SelectorConfig(prefer_lower_android=False, prefer_universal=False,
                         prefer_multi_signature=False)
    v_with = make_apk(version_code=1_000_000, architectures=["x86"],
                      dpi=None, device_type=None, signatures=["a"],
                      variant_url="https://example.com")
    v_without = make_apk(version_code=1_000_000, architectures=["x86"],
                         dpi=None, device_type=None, signatures=["a"],
                         variant_url="")
    result = select_best_variant([v_without, v_with], cfg)
    assert result is v_with


# ===========================================================================
# normalize_list_env
# ===========================================================================

def test_normalize_list_env_basic():
    assert normalize_list_env("arm64-v8a,armeabi-v7a") == ["arm64-v8a", "armeabi-v7a"]


def test_normalize_list_env_strips_whitespace():
    assert normalize_list_env("  3891 , bd32 ") == ["3891", "bd32"]


def test_normalize_list_env_empty_string():
    assert normalize_list_env("") == []


def test_normalize_list_env_none():
    assert normalize_list_env(None) == []


def test_normalize_list_env_single():
    assert normalize_list_env("arm64-v8a") == ["arm64-v8a"]


def test_normalize_list_env_trailing_comma():
    assert normalize_list_env("a,b,") == ["a", "b"]


# ===========================================================================
# maybe_parse_int
# ===========================================================================

def test_maybe_parse_int_valid():
    assert maybe_parse_int("21") == 21
    assert maybe_parse_int("  28  ") == 28


def test_maybe_parse_int_none():
    assert maybe_parse_int(None) is None


def test_maybe_parse_int_empty():
    assert maybe_parse_int("") is None
    assert maybe_parse_int("  ") is None


def test_maybe_parse_int_invalid():
    assert maybe_parse_int("abc") is None
    assert maybe_parse_int("21.5") is None


# ===========================================================================
# config_from_env
# ===========================================================================

def test_config_from_env_full():
    env = {
        "PREFER_APK": "true",
        "ALLOW_BUNDLE": "false",
        "REQUIRED_SIGNATURES": "3891,bd32",
        "REQUIRED_ARCHITECTURES": "arm64-v8a,armeabi-v7a",
        "REQUIRED_DPI": "nodpi",
        "REQUIRED_DEVICE_TYPE": "universal",
        "MIN_ANDROID_FLOOR": "21",
        "MIN_ANDROID_CEILING": "34",
        "MATCH_KEYWORDS": "stable",
        "EXCLUDE_KEYWORDS": "beta,preview",
        "PREFER_UNIVERSAL": "yes",
        "PREFER_LOWER_ANDROID": "1",
        "PREFER_MULTI_SIGNATURE": "on",
    }
    cfg = config_from_env(env)
    assert cfg.prefer_apk is True
    assert cfg.allow_bundle is False
    assert cfg.required_signatures == ["3891", "bd32"]
    assert cfg.required_architectures == ["arm64-v8a", "armeabi-v7a"]
    assert cfg.required_dpi == "nodpi"
    assert cfg.required_device_type == "universal"
    assert cfg.min_android_floor == 21
    assert cfg.min_android_ceiling == 34
    assert cfg.match_keywords == ["stable"]
    assert cfg.exclude_keywords == ["beta", "preview"]
    assert cfg.prefer_universal is True
    assert cfg.prefer_lower_android is True
    assert cfg.prefer_multi_signature is True


def test_config_from_env_empty_uses_defaults():
    cfg = config_from_env({})
    assert cfg.prefer_apk is True
    assert cfg.allow_bundle is True
    assert cfg.required_signatures == []
    assert cfg.required_dpi is None
    assert cfg.required_device_type is None
    assert cfg.min_android_floor is None
    assert cfg.min_android_ceiling is None


def test_config_from_env_bool_truthy_variants():
    for val in ("true", "True", "TRUE", "1", "yes", "YES", "on", "ON"):
        assert config_from_env({"PREFER_APK": val}).prefer_apk is True


def test_config_from_env_bool_falsy_variants():
    for val in ("false", "False", "FALSE", "0", "no", "NO", "off", "OFF"):
        assert config_from_env({"PREFER_APK": val}).prefer_apk is False


def test_config_from_env_empty_dpi_becomes_none():
    cfg = config_from_env({"REQUIRED_DPI": ""})
    assert cfg.required_dpi is None


def test_config_from_env_empty_device_type_becomes_none():
    cfg = config_from_env({"REQUIRED_DEVICE_TYPE": "  "})
    assert cfg.required_device_type is None
