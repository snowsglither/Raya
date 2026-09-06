"""Découpage de message Telegram-safe (RAYA V2 Phase 9, consigne §14).

Telegram refuse tout `sendMessage` au-delà de 4096 caractères UTF-16 code
units — on utilise `len()` (code points) comme approximation prudente
suffisante pour du texte RAYA (français/anglais, rarement des caractères
hors BMP). Le découpage privilégie les frontières de paragraphe, ne coupe
jamais À L'INTÉRIEUR d'un bloc de code ``` ```, et ne casse un mot que si
une seule "ligne" dépasse déjà la limite à elle seule (dernier recours)."""

from __future__ import annotations

TELEGRAM_MESSAGE_LIMIT = 4096


def _is_fence_line(line: str) -> bool:
    return line.strip().startswith("```")


def _hard_split(text: str, limit: int) -> list[str]:
    limit = max(limit, 1)
    words = text.split(" ")
    pieces: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > limit and current:
            pieces.append(current)
            current = word
        else:
            current = candidate
    if current:
        pieces.append(current)
    final: list[str] = []
    for piece in pieces:
        while len(piece) > limit:
            final.append(piece[:limit])
            piece = piece[limit:]
        if piece:
            final.append(piece)
    return final or [text[:limit]]


def chunk_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    if not text:
        return [text]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    in_fence = False

    def _flush() -> None:
        nonlocal current, current_len
        if not current:
            return
        if in_fence:
            current = current + ["```"]
        chunks.append("\n".join(current))
        current = ["```"] if in_fence else []
        current_len = sum(len(line) + 1 for line in current)

    for line in text.split("\n"):
        line_len = len(line) + 1
        if current_len + line_len > limit and current:
            _flush()
        if line_len > limit:
            # Une seule ligne dépasse déjà la limite entière -> dernier recours.
            for piece in _hard_split(line, limit - current_len if current_len else limit):
                if current_len + len(piece) + 1 > limit and current:
                    _flush()
                current.append(piece)
                current_len += len(piece) + 1
        else:
            current.append(line)
            current_len += line_len
        if _is_fence_line(line):
            in_fence = not in_fence

    if current:
        chunks.append("\n".join(current))
    return chunks
