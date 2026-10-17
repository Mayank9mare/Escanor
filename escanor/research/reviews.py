"""
research/reviews.py

Spaced review: learn-anything keeps one learning log per topic (saved by
Escanor at research/learn/<topic>.md, from the skill's template). Each
log has a "**Next review due:** YYYY-MM-DD" line and a flashcards table
with a "Next due" column. due_reviews() finds topics whose review date
has arrived, for the morning briefing and the research_reviews tool.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from datetime import date
from ..brain.vault import Vault, VaultError

LEARN_FOLDER = "research/learn"
_NEXT_REVIEW = re.compile(r"\*\*Next review due:\*\*\s*(\d{4}-\d{2}-\d{2})")
_TITLE = re.compile(r"^#\s*Learning Log:\s*(.+)$", re.M)
_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass
class Review:
    topic: str
    path: str
    due: date
    cards_due: int


def _cards_due(text: str, today: date) -> int:
    """Count flashcard rows whose 'Next due' column is today or earlier."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().lower() == "## flashcards")
    except StopIteration:
        return 0
    header = next((line for line in lines[start:] if line.strip().startswith("|")), "")
    columns = [c.strip().lower() for c in header.strip().strip("|").split("|")]
    if "next due" not in columns:
        return 0
    index = columns.index("next due")
    count = 0
    for line in lines[start:]:
        if line.startswith("## ") and line.strip().lower() != "## flashcards":
            break
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) > index and (match := _DATE.search(cells[index])):
            if date.fromisoformat(match.group(1)) <= today:
                count += 1
    return count


def due_reviews(vault: Vault, today: date) -> list[Review]:
    try:
        paths = vault.list(LEARN_FOLDER)
    except VaultError:
        return []
    reviews = []
    for path in paths:
        text = vault.read(path)
        match = _NEXT_REVIEW.search(text)
        if not match:
            continue
        due = date.fromisoformat(match.group(1))
        cards = _cards_due(text, today)
        if due <= today or cards:
            title = _TITLE.search(text)
            reviews.append(Review(title.group(1).strip() if title else path.rsplit("/", 1)[-1][:-3], path, due, cards))
    return sorted(reviews, key=lambda r: r.due)


def describe(reviews: list[Review], today: date) -> str:
    if not reviews:
        return ""
    lines = []
    for r in reviews:
        late = (today - r.due).days
        when = "due today" if late == 0 else (f"{late} days overdue" if late > 0 else f"due {r.due}")
        cards = f", {r.cards_due} flashcards" if r.cards_due else ""
        lines.append(f"- {r.topic} ({when}{cards}) — {r.path}")
    return "\n".join(lines)
