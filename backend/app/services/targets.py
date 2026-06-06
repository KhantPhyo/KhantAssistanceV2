"""Resolve @tokens into assistant IDs for job assignment.

Token grammar:
  @all                   → every active assistant
  @<group_name>          → all members of a Group whose normalized name matches
                           (e.g. @shop1 matches "Shop 1", "shop-1", "SHOP1")
  @<telegram_username>   → single active assistant with matching telegram_username
  Multiple tokens combine via union (deduped).

Used by admin_bot /newjob, /reassign, and /createjob.
"""
from __future__ import annotations

import re
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Assistant, Group, AssistantGroup


def _norm(s: str | None) -> str:
    """Lower-case and strip non-alphanumerics for fuzzy name matching."""
    if not s:
        return ""
    return re.sub(r"\W+", "", s).lower()


def split_tokens(raw: str) -> list[str]:
    """Split a free-form targets string into individual @-tokens.
    Accepts space, comma, or semicolon as separators."""
    if not raw:
        return []
    parts = re.split(r"[\s,;]+", raw.strip())
    out: list[str] = []
    for p in parts:
        p = p.strip().lstrip("@").strip()
        if p:
            out.append(p)
    return out


def resolve(db: Session, tokens: list[str]) -> tuple[list[int], list[str], list[str]]:
    """Resolve a list of @tokens into a deduped list of assistant IDs.

    Returns (assistant_ids, matched_labels, unknown_tokens).
    `matched_labels` describes what each token resolved to — for a confirmation
    message back to the user.
    """
    ids: set[int] = set()
    matched: list[str] = []
    unknown: list[str] = []

    for raw in tokens:
        t = raw.lstrip("@").strip()
        if not t:
            continue
        norm = _norm(t)

        # 1) special wildcard
        if norm == "all":
            rows = db.query(Assistant).filter(Assistant.status == "active").all()
            new_ids = {a.id for a in rows}
            ids |= new_ids
            matched.append(f"@all → {len(new_ids)} active staff")
            continue

        # 2) Group name (fuzzy: case + non-alphanumeric tolerant)
        groups = db.query(Group).all()
        grp = next((g for g in groups if _norm(g.name) == norm), None)
        if grp:
            mems = db.query(AssistantGroup).filter(AssistantGroup.group_id == grp.id).all()
            mids = {m.assistant_id for m in mems}
            # only count active assistants
            if mids:
                actives = {a.id for a in db.query(Assistant).filter(
                    Assistant.id.in_(mids), Assistant.status == "active"
                ).all()}
                ids |= actives
                matched.append(f"@{grp.name} → {len(actives)} member(s)")
            else:
                matched.append(f"@{grp.name} → (empty group)")
            continue

        # 3) Telegram username (case-insensitive exact)
        asst = db.query(Assistant).filter(
            func.lower(Assistant.telegram_username) == t.lower(),
            Assistant.status == "active",
        ).first()
        if asst:
            ids.add(asst.id)
            matched.append(f"@{asst.telegram_username} ({asst.name})")
            continue

        unknown.append(t)

    return sorted(ids), matched, unknown
