"""Turning a model's reply into evidence, without damaging it on the way in.

Three things here, each replacing something the original tool got wrong:

1. `parse_model_json` -- the original ran a regex repair UNCONDITIONALLY before
   `json.loads`, so it rewrote replies that were already valid. The regex inserted a
   colon after any quoted run followed by `[ { " t f n` or a digit, which silently turned
   a transcription like `The box says "Ages 3+" 5 pieces` into
   `The box says "Ages 3+": 5 pieces`. It still parsed, so nothing noticed, and the damage
   landed in the transcribed package text the age rules read. Here: parse first, repair
   only on failure, apply each repair STRING-AWARE so nothing inside a string literal is
   ever touched, re-validate after every step, and report which repairs were used.

2. `consolidate` -- the original did `d.pop("in_scope_signals")` on the per-image record
   before storing it, so the report kept a flat deduped union and could no longer say
   which image produced which signal. Signals here carry their source image.

3. `prepare_image` -- the original called `Image.save(..., "JPEG")` on whatever it opened,
   which raises `OSError: cannot write mode RGBA as JPEG` for any oversized PNG with
   alpha; `.png` is an accepted input, and the failure was swallowed and counted as a
   load error. Here alpha is flattened first and the shrink loop has a floor.

This module knows nothing about any particular model or provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ImagePreparationError",
    "ParsedPayload",
    "Signal",
    "UnparseableResponse",
    "consolidate",
    "parse_model_json",
    "prepare_image",
    "REQUIRED_FIELDS",
    "SIGNAL_FIELDS",
]

REQUIRED_FIELDS = (
    "image_type",
    "description",
    "visible_text",
    "in_scope_signals",
    "out_of_scope_signals",
    "ambiguous_signals",
    "product_attributes",
)
SIGNAL_FIELDS = ("in_scope_signals", "out_of_scope_signals", "ambiguous_signals")


class UnparseableResponse(ValueError):
    """No repair produced valid JSON. Carries the original text for the report."""

    def __init__(self, text: str, attempted: tuple[str, ...]) -> None:
        super().__init__(
            f"could not parse model response after attempting {list(attempted)}; "
            f"{len(text)} chars, starts {text[:80]!r}"
        )
        self.text = text
        self.attempted = attempted


class ImagePreparationError(ValueError):
    """The image could not be brought under the size limit."""


@dataclass(frozen=True)
class ParsedPayload:
    data: dict[str, Any]
    repairs: tuple[str, ...] = ()

    @property
    def was_clean(self) -> bool:
        """True when the reply parsed as sent. A repaired payload is weaker evidence and
        a report should be able to say so."""
        return not self.repairs


# ------------------------------------------------------------------ string-aware repairs


def _spans_outside_strings(text: str) -> list[tuple[int, int]]:
    """Index ranges that are NOT inside a JSON string literal."""
    spans, start, i, n = [], 0, 0, len(text)
    in_str = esc = False
    while i < n:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                start = i + 1
        elif ch == '"':
            spans.append((start, i))
            in_str = True
        i += 1
    if not in_str:
        spans.append((start, n))
    return spans


def _strip_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return text
    t = t.split("\n", 1)[1] if "\n" in t else t[3:]
    if t.rstrip().endswith("```"):
        t = t.rstrip()[:-3]
    return t.strip()


def _extract_object(text: str) -> str:
    """The outermost balanced {...}, ignoring braces inside strings.

    Covers the common failure of a model prefacing or trailing the object with prose.
    """
    depth = start = -1
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth < 0:
                depth, start = 0, i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def _insert_missing_colons(text: str) -> str:
    """Insert `:` between an object KEY and its value, and nowhere else.

    This is what the original regex was reaching for. The difference is that it only
    fires in a key slot -- directly after `{` or after `,` whose innermost container is an
    object -- so a quoted run inside a string value, or a malformed array like
    `["a" "b"]`, is left alone. Declining to guess is the point: the original's guess is
    what corrupted the evidence.
    """
    out: list[str] = []
    stack: list[str] = []
    in_str = esc = key_slot = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                if key_slot and stack and stack[-1] == "{":
                    j = i + 1
                    while j < n and text[j] in " \t\r\n":
                        j += 1
                    if j < n and text[j] != ":":
                        out.append(":")
                    key_slot = False
            i += 1
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append(ch)
            key_slot = ch == "{"
        elif ch in "}]":
            if stack:
                stack.pop()
            key_slot = False
        elif ch == ",":
            key_slot = bool(stack) and stack[-1] == "{"
        elif ch == ":":
            key_slot = False
        out.append(ch)
        i += 1
    return "".join(out)


def _drop_trailing_commas(text: str) -> str:
    keep = list(text)
    for lo, hi in _spans_outside_strings(text):
        segment = text[lo:hi]
        for off, ch in enumerate(segment):
            if ch != ",":
                continue
            rest = segment[off + 1 :].lstrip()
            if rest[:1] in ("}", "]"):
                keep[lo + off] = ""
    return "".join(keep)


_REPAIRS = (
    ("strip_fence", _strip_fence),
    ("extract_object", _extract_object),
    ("insert_missing_colons", _insert_missing_colons),
    ("drop_trailing_commas", _drop_trailing_commas),
)


def parse_model_json(text: str) -> ParsedPayload:
    """Parse a model reply, repairing only if it does not already parse.

    Repairs are cumulative and each is validated by re-parsing, so a repair that does not
    help is still recorded only if it changed the text and the result eventually parsed.
    """
    try:
        return ParsedPayload(_require_object(json.loads(text)))
    except (json.JSONDecodeError, TypeError):
        pass

    current, applied = text, []
    for name, fn in _REPAIRS:
        nxt = fn(current)
        if nxt != current:
            applied.append(name)
            current = nxt
        try:
            return ParsedPayload(_require_object(json.loads(current)), tuple(applied))
        except (json.JSONDecodeError, TypeError):
            continue
    raise UnparseableResponse(text, tuple(n for n, _ in _REPAIRS))


def _require_object(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise TypeError(f"expected a JSON object, got {type(obj).__name__}")
    return obj


# ------------------------------------------------------------------------- consolidation


@dataclass(frozen=True)
class Signal:
    """One observation, and the image it came from. Provenance is not optional."""

    text: str
    image: str
    kind: str


@dataclass(frozen=True)
class Consolidated:
    per_image: tuple[dict[str, Any], ...]
    signals: tuple[Signal, ...]
    failures: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def of_kind(self, kind: str) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.kind == kind)

    def images_supporting(self, kind: str) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.signals:
            if s.kind == kind and s.image not in seen:
                seen.append(s.image)
        return tuple(seen)


def consolidate(results: dict[str, ParsedPayload | Exception]) -> Consolidated:
    """Merge per-image payloads, KEEPING the per-image records and signal provenance.

    Deduplication is by (text, kind, image) rather than by text alone: the same
    observation seen in three images is three pieces of evidence, and collapsing them
    throws away the only thing that distinguishes a corroborated signal from a one-off.
    """
    per_image: list[dict[str, Any]] = []
    signals: list[Signal] = []
    failures: list[dict[str, str]] = []

    for image in sorted(results):
        outcome = results[image]
        if isinstance(outcome, Exception):
            failures.append({"image": image, "error": str(outcome)})
            continue
        record = dict(outcome.data)
        record["image"] = image
        record["repairs"] = list(outcome.repairs)
        per_image.append(record)
        for kind in SIGNAL_FIELDS:
            for item in record.get(kind) or []:
                if isinstance(item, str) and item.strip():
                    sig = Signal(item.strip(), image, kind)
                    if sig not in signals:
                        signals.append(sig)

    return Consolidated(tuple(per_image), tuple(signals), tuple(failures))


# ---------------------------------------------------------------------- image preparation

MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_QUALITY_LADDER = (85, 70, 50, 30, 15, 5)
_MIN_EDGE = 32


def prepare_image(data: bytes, suffix: str, max_bytes: int) -> tuple[bytes, str]:
    """Return bytes within `max_bytes`, converting to JPEG only if shrinking is needed.

    Raises rather than returning an oversized payload: sending it anyway turns a local,
    explainable failure into a remote one with a worse message.
    """
    media = MEDIA_TYPES.get(suffix.lower(), "image/jpeg")
    if len(data) <= max_bytes:
        return data, media

    try:
        import io

        from PIL import Image
    except ImportError as e:  # pragma: no cover - depends on the optional extra
        raise ImagePreparationError(
            f"image is {len(data)} bytes (limit {max_bytes}) and Pillow is not installed, "
            "so it cannot be shrunk; install the 'bedrock' extra"
        ) from e

    img = Image.open(io.BytesIO(data))
    if img.mode in ("RGBA", "LA", "P"):
        # JPEG has no alpha channel. The original skipped this and raised
        # "cannot write mode RGBA as JPEG" on any oversized PNG with transparency.
        img = img.convert("RGB")

    def encode(im, quality: int) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    for quality in _QUALITY_LADDER:
        out = encode(img, quality)
        if len(out) <= max_bytes:
            return out, "image/jpeg"

    out = encode(img, _QUALITY_LADDER[-1])
    while len(out) > max_bytes and min(img.size) > _MIN_EDGE:
        img = img.resize((max(int(img.width * 0.75), 1), max(int(img.height * 0.75), 1)))
        out = encode(img, _QUALITY_LADDER[-1])
    if len(out) > max_bytes:
        raise ImagePreparationError(
            f"could not reach {max_bytes} bytes; smallest attempt was {len(out)} at "
            f"{img.size} and the {_MIN_EDGE}px floor was reached"
        )
    return out, "image/jpeg"
