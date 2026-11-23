"""Transport: getting observations out of a vision model, and what to ask it for.

Two positions here, both of which are the point of the module.

**The prompt is BUILT FROM THE LINT FINDINGS, not from the whole policy.** Injecting an entire
natural-language SOP and then asking for "any attributes the policy uses for classification"
asks the model for fields that are not in the image -- catalog age attributes, price relative
to comparable products, title and bullet text. A model asked for a field it cannot observe
will often supply it anyway, and a fabricated attribute is indistinguishable from an observed
one by the time it reaches a consumer. So `build_prompt()` takes the linter's `UNEVALUABLE`
findings and excludes exactly those clauses, then asks only for what a photograph can carry.
The linter is not a report that a human reads and forgets; it decides what gets asked.

**The client is a Protocol with an offline default.** `StubClient` is deterministic and
requires no credentials, so the whole suite -- including the pipeline test -- runs for someone
with no AWS account. `BedrockClient` is optional, imports boto3 lazily, and accepts an
injected client so its request construction is testable without the dependency or a network.

`StubClient` raises on an image it was not given a response for. An empty response for an
unknown key would flow into `consolidate()` as an image that yielded no signals, which is
indistinguishable from an image that genuinely showed nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from .extract import (
    ParsedPayload,
    UnparseableResponse,
    parse_model_json,
    prepare_image,
)
from .lint import Finding, Kind

__all__ = [
    "MAX_IMAGE_BYTES",
    "BedrockClient",
    "MissingStubResponse",
    "OBSERVABLE_FIELDS",
    "StubClient",
    "VisionClient",
    "build_prompt",
    "collect",
]

OBSERVABLE_FIELDS: tuple[str, ...] = (
    "image_type",
    "description",
    "visible_text",
    "in_scope_signals",
    "out_of_scope_signals",
    "ambiguous_signals",
)
"""What a photograph can carry. Deliberately excludes `product_attributes`.

The original asked for a free-form attribute bag covering "any attributes the SOP uses for
classification". Three of the four age-rule source fields are catalog data, so that request
could only be satisfied by invention.
"""


MAX_IMAGE_BYTES = 3_750_000
"""Raw-byte ceiling before base64. Base64 inflates by 4/3, so this keeps the encoded payload
under the 5 MB request limit. `prepare_image` only re-encodes when this is exceeded."""


class MissingStubResponse(KeyError):
    """Asked for an image the stub has no response for."""


@runtime_checkable
class VisionClient(Protocol):
    """Anything that turns an image plus a prompt into raw model text."""

    def describe(self, image: bytes, prompt: str, suffix: str = ".jpg") -> str: ...


def build_prompt(sop_text: str, findings: tuple[Finding, ...] = ()) -> str:
    """Ask only for what is in the image, and say which rules are deliberately omitted.

    Clauses cited by an `UNEVALUABLE` finding are dropped from the injected policy. The
    omission is stated in the prompt rather than performed silently, so a reader of the
    prompt can see that the tool knows what it is not asking about.
    """
    unevaluable_lines = {
        line for f in findings if f.kind is Kind.UNEVALUABLE for line in f.lines
    }
    kept, dropped = [], []
    for number, line in enumerate(sop_text.splitlines(), start=1):
        (dropped if number in unevaluable_lines else kept).append(line)

    body = "\n".join(kept)
    fields = "\n".join(f"  - {name}" for name in OBSERVABLE_FIELDS)
    notice = ""
    if dropped:
        notice = (
            f"\n{len(dropped)} policy line(s) were REMOVED from the text above because their "
            "evidence is not present in a photograph (catalog attributes, pricing, title or "
            "bullet text). Do not attempt to satisfy them. If you cannot see something, it "
            "does not go in the response.\n"
        )

    return (
        "You are transcribing what is visible in ONE product image. You are not classifying "
        "the product and you are not deciding anything.\n"
        f"{notice}\n"
        "Reply with a single JSON object and nothing else, containing exactly these keys:\n"
        f"{fields}\n\n"
        "`image_type` is one of: lifestyle, product_only, packaging, label, gift_card, "
        "marketing, other.\n"
        "`visible_text` is text you can actually read in the image, transcribed verbatim.\n"
        "The three signal lists hold short phrases describing what you SEE. Put a phrase in "
        "`ambiguous_signals` when you cannot tell -- that is a useful answer, not a failure.\n"
        "Report nothing you cannot see. An empty list is correct when there is nothing to "
        "report.\n\n"
        "--- POLICY CONTEXT (for vocabulary only; do not apply it) ---\n"
        f"{body}\n"
    )


@dataclass
class StubClient:
    """Deterministic offline client. Records the prompts it was given.

    `responses` maps image name to the raw text the model would have returned, so a fixture
    can include malformed JSON and exercise the repair path without a network.
    """

    responses: dict[str, str]
    prompts: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def describe(self, image: bytes, prompt: str, suffix: str = ".jpg") -> str:
        name = image.decode("utf-8", errors="replace")
        self.prompts.append(prompt)
        self.calls.append(name)
        if name not in self.responses:
            raise MissingStubResponse(
                f"no stubbed response for {name!r}; known: {sorted(self.responses)}"
            )
        return self.responses[name]

    @classmethod
    def from_payloads(cls, payloads: dict[str, dict]) -> StubClient:
        """Build from already-valid payload dicts, for the common case."""
        return cls({name: json.dumps(body) for name, body in payloads.items()})


@dataclass
class BedrockClient:
    """Optional real inference. boto3 is imported lazily and never at module import.

    `client` may be injected, which is how the request shape is tested without boto3 and
    without credentials.
    """

    model_id: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    region: str = "us-east-1"
    client: object | None = None
    max_tokens: int = 2048
    max_bytes: int = MAX_IMAGE_BYTES

    def _ensure_client(self) -> object:
        if self.client is None:
            try:
                import boto3  # noqa: PLC0415
            except ImportError as exc:  # pragma: no cover - depends on the environment
                raise ImportError(
                    "BedrockClient needs boto3: pip install -e '.[bedrock]'. "
                    "StubClient requires nothing and runs the whole suite offline."
                ) from exc
            self.client = boto3.client("bedrock-runtime", region_name=self.region)
        return self.client

    def request_body(self, image: bytes, prompt: str, suffix: str = ".jpg") -> dict:
        """The payload, separated out so a test can assert its shape without a call.

        The media type comes from `prepare_image`, which re-encodes to JPEG ONLY when the
        image is over the limit -- so a small PNG is sent as a PNG. Hardcoding
        `image/jpeg` here would mislabel it and the model would be decoding against the
        wrong declared type.
        """
        import base64  # noqa: PLC0415

        data, media_type = prepare_image(image, suffix, self.max_bytes)

        return {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": self.max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64.b64encode(data).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }

    def describe(self, image: bytes, prompt: str, suffix: str = ".jpg") -> str:
        client = self._ensure_client()
        response = client.invoke_model(  # type: ignore[attr-defined]
            modelId=self.model_id,
            body=json.dumps(self.request_body(image, prompt, suffix)),
        )
        payload = json.loads(response["body"].read())
        return "".join(
            block.get("text", "") for block in payload.get("content", [])
        )


def collect(
    images: dict[str, bytes],
    client: VisionClient,
    prompt: str,
) -> dict[str, ParsedPayload | UnparseableResponse]:
    """Ask the client about every image and parse each reply. Failures are RETURNED.

    A transport or parse failure becomes an `UnparseableResponse` in the result rather than
    an exception that loses the other images, and `consolidate()` keeps it in `failures`
    instead of treating the image as having shown nothing.
    """
    out: dict[str, ParsedPayload | UnparseableResponse] = {}
    for name, blob in sorted(images.items()):
        try:
            raw = client.describe(blob, prompt, PurePosixPath(name).suffix or ".jpg")
        except Exception as exc:  # noqa: BLE001 - one bad image must not lose the rest
            out[name] = UnparseableResponse(f"{type(exc).__name__}: {exc}", ())
            continue
        try:
            out[name] = parse_model_json(raw)
        except UnparseableResponse as exc:
            out[name] = exc
    return out
