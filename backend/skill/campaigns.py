"""Filter A-provided read-only campaigns using verified scope and taste facts.

Campaigns require source, a verifiable time window, and either store_ids or an
explicit verified all-stores flag. ``taste_tags`` are product taste categories;
raw marketing tags such as "上新" or "麦金卡" never imply beef/chicken taste.
Membership-only campaigns additionally require confirmed eligibility. This
function reports discoverable information, never a guaranteed prize or coupon.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any


def _data(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if is_dataclass(value):
        return asdict(value)
    return {}


def _instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def recommend_campaigns(
    campaigns: Any, requirements: Any, store_code: str, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return active, applicable discoveries, with evidence and conditions.

    A campaign with unverified date/store scope or unknown membership
    eligibility is omitted. Naive and timezone-aware timestamps are not mixed.
    User-declared ``active`` alone never proves account qualification.
    """
    if not isinstance(store_code, str) or not store_code:
        return []
    entries = campaigns if isinstance(campaigns, Sequence) and not isinstance(campaigns, (str, bytes)) else []
    req = _data(requirements)
    preferred = {tag for tag in (req.get("preferred_tags") or []) if isinstance(tag, str)}
    current = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    ranked: list[tuple[int, datetime, dict[str, Any]]] = []
    for raw in entries:
        campaign = _data(raw)
        source = campaign.get("source")
        start = _instant(campaign.get("start_at", campaign.get("start_time")))
        end = _instant(campaign.get("end_at", campaign.get("end_time")))
        if (
            not isinstance(source, str) or not source
            or start is None or end is None
            or (start.tzinfo is None) != (current.tzinfo is None)
            or (end.tzinfo is None) != (current.tzinfo is None)
            or end < start or current < start or current > end
        ):
            continue
        store_ids = campaign.get("store_ids")
        at_store = isinstance(store_ids, (list, tuple, set)) and store_code in store_ids
        all_stores = campaign.get("all_stores") is True and campaign.get("store_applicability_verified") is True
        if not at_store and not all_stores:
            continue
        account_required = campaign.get("account_required")
        if type(account_required) is not bool:
            # Unknown account conditions cannot be advertised as applicable.
            continue
        if account_required and (
            campaign.get("account_eligibility_verified") is not True
            or campaign.get("eligible") is not True
        ):
            continue
        membership = campaign.get("membership_condition", "none")
        if campaign.get("membership_required") is True:
            membership = "active"
        if membership not in ("none", "active"):
            continue
        if membership == "active" and (
            req.get("membership_status") != "active" or campaign.get("eligibility_verified") is not True
        ):
            continue
        taste_tags = campaign.get("taste_tags")
        if not isinstance(taste_tags, (list, tuple, set)):
            taste_tags = []
        taste_match = len(preferred & {tag for tag in taste_tags if isinstance(tag, str)})
        if preferred and taste_match == 0:
            continue
        output = dict(campaign)
        output["start_at"] = campaign.get("start_at", campaign.get("start_time"))
        output["end_at"] = campaign.get("end_at", campaign.get("end_time"))
        output["applicable_store_code"] = store_code
        output["taste_match_count"] = taste_match
        output["reward_guaranteed"] = False
        output["availability_note"] = "活动信息与参与条件以来源为准；奖励或抽奖结果不保证获得。"
        ranked.append((taste_match, end, output))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in ranked]
