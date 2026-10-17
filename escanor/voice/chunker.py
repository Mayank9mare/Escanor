"""
voice/chunker.py

Streaming speech: the reply arrives as text fragments; SentenceChunker
turns them into speakable sentences as soon as each one is complete, so
the first sentence plays while the model is still writing the rest.
That's the biggest latency win in the voice pipeline
(IMPLEMENTATION_PLAN.md Phase 4b).

speakable() cleans Markdown for the ear: code blocks become "(code
shown on screen)", links are read by their text, bare URLs become
"a link", emphasis and heading marks are dropped.
"""
from __future__ import annotations
import re

# Words ending in "." that don't end a sentence.
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "eg", "ie",
    "approx", "no", "fig", "inc", "ltd", "co", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "u.s", "u.k",
}
_MIN_CHARS = 12          # don't speak "Ok." alone if more is coming quickly
_MAX_CHARS = 300         # force a break in very long sentences (at a comma if possible)


def speakable(text: str) -> str:
    text = re.sub(r"```.*?(```|$)", " (code shown on screen) ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.MULTILINE)  # tables aren't readable aloud
    text = re.sub(r"<untrusted[^>]*>|</untrusted>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_boundary(text: str, index: int) -> bool:
    """Is the punctuation at text[index] the end of a sentence?"""
    char = text[index]
    if char in "!?":
        return True
    if char == "\n":
        return index > 0 and text[index - 1] == "\n"  # blank line = paragraph break
    if char != ".":
        return False
    before = text[:index]
    word = re.search(r"([A-Za-z.]+)$", before)
    if word and word.group(1).lower().rstrip(".") in _ABBREVIATIONS:
        return False
    if re.search(r"\d$", before) and index + 1 < len(text) and text[index + 1].isdigit():
        return False  # 3.14
    if re.search(r"(^|\s)[A-Z]$", before):
        return False  # initials: "J. R. R."
    return True


def _fenced_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each complete ```...``` block."""
    marks = [m.start() for m in re.finditer("```", text)]
    return [(marks[i], marks[i + 1] + 3) for i in range(0, len(marks) - 1, 2)]


class SentenceChunker:
    """feed() fragments in; get back sentences ready to speak. flush() at
    the end of the reply returns whatever is left."""

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, fragment: str) -> list[str]:
        self._buffer += fragment
        sentences: list[str] = []
        while True:
            cut = self._find_cut()
            if cut is None:
                break
            piece, self._buffer = self._buffer[:cut], self._buffer[cut:]
            spoken = speakable(piece)
            if spoken:
                sentences.append(spoken)
        return sentences

    def flush(self) -> list[str]:
        piece, self._buffer = self._buffer, ""
        spoken = speakable(piece)
        return [spoken] if spoken else []

    def _find_cut(self) -> int | None:
        text = self._buffer
        # Never cut inside an unfinished code block; wait for its closing fence.
        if text.count("```") % 2 == 1:
            return None
        fenced = _fenced_spans(text)
        for i, char in enumerate(text):
            if any(start <= i < end for start, end in fenced):
                continue  # punctuation inside code isn't a sentence end
            if char in ".!?\n" and _is_boundary(text, i):
                end = i + 1
                while end < len(text) and text[end] in "\"')]*_":
                    end += 1
                # Need the next character to be sure ("3." could become "3.14").
                if end >= len(text):
                    return None
                if not text[end].isspace() and char != "\n":
                    continue
                if len(speakable(text[:end])) < _MIN_CHARS:
                    continue
                return end
        if len(text) > _MAX_CHARS:
            comma = text.rfind(", ", 0, _MAX_CHARS)
            return comma + 2 if comma > _MIN_CHARS else _MAX_CHARS
        return None
