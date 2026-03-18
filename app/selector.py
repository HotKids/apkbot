"""
selector.py — APKMirror 变体筛选器

职责：从已解析的 Variant 列表中，按配置规则选出最佳候选变体。

不涉及 HTML 抓取、Telegram 推送、数据库、网络请求，
可直接在单元测试中独立使用。

核心设计理念
────────────
  "先硬过滤，再打分排序，再选最高分"

  APK 不是硬过滤条件：
    - 硬过滤 BUNDLE 会导致"该应用只发布 BUNDLE"时返回空结果
    - 正确做法是在打分阶段给 APK 极高权重 (+1000)
    - 这样有 APK 时永远优先选 APK，无 APK 时自然 fallback 到 BUNDLE
    - 只有 allow_bundle=False 时才在 filter 阶段排除 BUNDLE
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    app_name: str = ""
    release_version_name: str = ""
    version_code: Optional[int] = None
    display_build: Optional[str] = None      # 如 "(519684117)"，辅助展示用
    variant_label: str = ""
    type: str = ""                           # "APK" | "BUNDLE"
    is_bundle: bool = False
    signatures: list[str] = field(default_factory=list)
    architectures: list[str] = field(default_factory=list)
    min_android_text: Optional[str] = None   # 原始文本，如 "Android 5.0+"
    min_android_api: Optional[int] = None    # API 等级整数，如 21
    dpi: Optional[str] = None               # 如 "nodpi"、"320dpi"
    device_type: Optional[str] = None       # 如 "universal"
    release_url: str = ""
    variant_url: str = ""
    download_page_url: Optional[str] = None
    final_download_url: Optional[str] = None
    raw_text: str = ""


@dataclass
class SelectorConfig:
    # APK 类型偏好
    prefer_apk: bool = True          # True → APK 在打分阶段获得 +1000
    allow_bundle: bool = True        # False → BUNDLE 在过滤阶段直接排除

    # 硬性过滤条件（不满足则排除）
    required_signatures: list[str] = field(default_factory=list)     # AND 关系
    required_architectures: list[str] = field(default_factory=list)  # OR 关系
    required_dpi: Optional[str] = None
    required_device_type: Optional[str] = None
    min_android_floor: Optional[int] = None    # v.min_android_api >= floor
    min_android_ceiling: Optional[int] = None  # v.min_android_api <= ceiling
    match_keywords: list[str] = field(default_factory=list)    # OR，至少命中一个
    exclude_keywords: list[str] = field(default_factory=list)  # 命中任一则排除

    # 打分偏好开关
    prefer_universal: bool = True
    prefer_lower_android: bool = True
    prefer_multi_signature: bool = True


# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def normalize_list_env(value: str | None) -> list[str]:
    """将逗号分隔的字符串转为去空白的列表；None 或空串返回空列表。

    Examples:
        "arm64-v8a,armeabi-v7a" → ["arm64-v8a", "armeabi-v7a"]
        "  3891 , bd32 "        → ["3891", "bd32"]
        "" / None               → []
    """
    if not value:
        return []
    return [s.strip() for s in value.split(",") if s.strip()]


def maybe_parse_int(value: str | None) -> int | None:
    """将字符串解析为整数；None、空串或非法值返回 None。

    Examples:
        "21"  → 21
        "  " → None
        None  → None
        "abc" → None
    """
    if not value or not value.strip():
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _parse_bool(value: str | None, default: bool = True) -> bool:
    """解析布尔型环境变量。

    支持：true/false、1/0、yes/no、on/off（大小写不敏感）。
    未设置时返回 default。
    """
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on"}


# ---------------------------------------------------------------------------
# 核心：硬过滤
# ---------------------------------------------------------------------------

def filter_variant(v: Variant, cfg: SelectorConfig) -> bool:
    """判断变体是否通过硬过滤。

    返回 True = 通过，False = 被淘汰。

    设计要点：
      BUNDLE 不是无条件过滤的对象。只有 allow_bundle=False 时才排除 BUNDLE。
      "APK 优先" 的语义通过打分（+1000）而非过滤来实现。
      这样确保：若当前版本只有 BUNDLE，且 allow_bundle=True，仍能选出结果。
    """
    # 1. BUNDLE 过滤：仅在明确禁止时排除
    #    注意：prefer_apk=True ≠ 排除 BUNDLE，prefer 效果由 score 阶段实现
    if v.is_bundle and not cfg.allow_bundle:
        return False

    # 2. 签名过滤（AND：必须全部命中）
    if cfg.required_signatures:
        if not all(sig in v.signatures for sig in cfg.required_signatures):
            return False

    # 3. 架构过滤（OR：命中任意一个即可）
    if cfg.required_architectures:
        if not any(arch in v.architectures for arch in cfg.required_architectures):
            return False

    # 4. DPI 精确匹配
    if cfg.required_dpi and v.dpi != cfg.required_dpi:
        return False

    # 5. device_type 容错匹配
    #    满足以下任一即可：精确匹配 device_type、在 architectures 中、在 raw_text 中
    #    容错的原因：APKMirror 页面有时把 "universal" 写在架构列或原始文本里
    if cfg.required_device_type:
        dt = cfg.required_device_type.lower()
        matched = (
            (v.device_type or "").lower() == dt
            or dt in [a.lower() for a in v.architectures]
            or dt in v.raw_text.lower()
        )
        if not matched:
            return False

    # 6. Android API 范围过滤
    #    若配置了上下限，但变体没有 API 信息（None），视为不满足
    if cfg.min_android_floor is not None:
        if v.min_android_api is None or v.min_android_api < cfg.min_android_floor:
            return False
    if cfg.min_android_ceiling is not None:
        if v.min_android_api is None or v.min_android_api > cfg.min_android_ceiling:
            return False

    # 7. 排除关键词（命中任意一个即排除）
    if cfg.exclude_keywords:
        combined = (v.variant_label + " " + v.raw_text).lower()
        if any(kw.lower() in combined for kw in cfg.exclude_keywords):
            return False

    # 8. 必需关键词（OR：至少命中一个才保留）
    if cfg.match_keywords:
        combined = (v.variant_label + " " + v.raw_text).lower()
        if not any(kw.lower() in combined for kw in cfg.match_keywords):
            return False

    return True


# ---------------------------------------------------------------------------
# 核心：打分
# ---------------------------------------------------------------------------

def score_variant(v: Variant, cfg: SelectorConfig) -> float:
    """对通过硬过滤的变体打分，分数越高越优先。

    关键设计：APK 类型在此获得极高分（+1000）而非在 filter 阶段硬排除 BUNDLE。
    这样实现了"有 APK 优先选 APK，无 APK 时自然 fallback BUNDLE"的语义。
    """
    s = 0.0

    # 1. APK 最高优先级
    #    prefer_apk=True 时 APK 获得 +1000，远超其他所有分项之和
    #    prefer_apk=False 时不拉开 APK/BUNDLE 差距（让其他维度决定）
    if cfg.prefer_apk and v.type == "APK":
        s += 1000.0

    # 2. 多签名优先（多签名通常意味着更广泛的设备兼容性）
    if cfg.prefer_multi_signature and len(v.signatures) > 1:
        s += 30.0

    # 3. 架构优先级（按当前主流程度从高到低）
    if "arm64-v8a" in v.architectures:
        s += 50.0
    elif "armeabi-v7a" in v.architectures:
        s += 30.0
    elif "x86_64" in v.architectures:
        s += 15.0
    elif "x86" in v.architectures:
        s += 5.0

    # 4. universal 设备类型优先（覆盖范围更广）
    if cfg.prefer_universal:
        is_universal = (
            v.device_type == "universal"
            or "universal" in v.architectures
            or "universal" in v.raw_text.lower()
        )
        if is_universal:
            s += 20.0

    # 5. nodpi 优先（适配所有屏幕密度，无需资源拆分）
    if v.dpi == "nodpi":
        s += 15.0

    # 6. 更低 Android 要求优先（兼容更多设备）
    #    公式：max(0, 50 - api_level)，api 越低得分越高
    if cfg.prefer_lower_android and v.min_android_api is not None:
        s += max(0.0, 50.0 - v.min_android_api)

    # 7. version_code 越大越新（用小数加分，不影响主要排名但能打破平局）
    if v.version_code is not None:
        s += v.version_code / 1_000_000.0

    # 8. required_signatures 命中加分
    #    filter 已要求全部命中，此处加分的意义是：
    #    当有多个同样满足条件的变体时，倾向选出与目标签名集更吻合的那个
    if cfg.required_signatures:
        for sig in cfg.required_signatures:
            if sig in v.signatures:
                s += 50.0

    return s


# ---------------------------------------------------------------------------
# 主函数
# ---------------------------------------------------------------------------

def select_best_variant(
    variants: list[Variant],
    config: SelectorConfig,
) -> Variant | None:
    """从 variants 列表中选出最佳变体。

    流程：
      1. 硬过滤（filter_variant）
      2. 若无候选，返回 None
      3. 对候选打分（score_variant）
      4. 取最高分者

    平局决胜规则（优先级从高到低）：
      a. score 更高
      b. version_code 更大
      c. variant_url 非空
      d. 原列表靠前（保持稳定性）
    """
    # 步骤 1：硬过滤，保留位置索引用于平局决胜
    candidates: list[tuple[int, Variant]] = [
        (i, v) for i, v in enumerate(variants) if filter_variant(v, config)
    ]
    if not candidates:
        return None

    # 步骤 2：打分排序
    def sort_key(item: tuple[int, Variant]) -> tuple[float, int, int, int]:
        idx, v = item
        return (
            score_variant(v, config),     # 主排名：分数越高越好
            1 if v.version_code is not None else 0,  # 有 version_code 优先
            v.version_code if v.version_code is not None else -1,
            1 if v.variant_url else 0,    # 有 URL 优先
            -idx,                         # 原列表靠前优先（负值使小 idx 更大）
        )

    best = max(candidates, key=sort_key)[1]
    return best


# ---------------------------------------------------------------------------
# ENV → Config
# ---------------------------------------------------------------------------

def config_from_env(env: Mapping[str, str]) -> SelectorConfig:
    """从环境变量映射（如 os.environ）构造 SelectorConfig。

    布尔解析支持：true/false、1/0、yes/no、on/off（大小写不敏感）
    列表解析支持：逗号分隔，自动 strip，空串转空列表
    """
    return SelectorConfig(
        prefer_apk=_parse_bool(env.get("PREFER_APK"), default=True),
        allow_bundle=_parse_bool(env.get("ALLOW_BUNDLE"), default=True),
        required_signatures=normalize_list_env(env.get("REQUIRED_SIGNATURES")),
        required_architectures=normalize_list_env(env.get("REQUIRED_ARCHITECTURES")),
        required_dpi=env.get("REQUIRED_DPI", "").strip() or None,
        required_device_type=env.get("REQUIRED_DEVICE_TYPE", "").strip() or None,
        min_android_floor=maybe_parse_int(env.get("MIN_ANDROID_FLOOR")),
        min_android_ceiling=maybe_parse_int(env.get("MIN_ANDROID_CEILING")),
        match_keywords=normalize_list_env(env.get("MATCH_KEYWORDS")),
        exclude_keywords=normalize_list_env(env.get("EXCLUDE_KEYWORDS")),
        prefer_universal=_parse_bool(env.get("PREFER_UNIVERSAL"), default=True),
        prefer_lower_android=_parse_bool(env.get("PREFER_LOWER_ANDROID"), default=True),
        prefer_multi_signature=_parse_bool(env.get("PREFER_MULTI_SIGNATURE"), default=True),
    )
