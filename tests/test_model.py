"""The transport layer, and the position that the linter decides what gets asked.

The load-bearing tests are `TestPromptIsBuiltFromFindings` (unevaluable policy lines are
removed and the removal is stated) and `TestFailuresSurvive` (one bad image must not lose the
others, and must not become an image that showed nothing).
"""

from __future__ import annotations

import io
import json

import pytest

from sop_screen.extract import UnparseableResponse, consolidate
from sop_screen.lint import Kind
from sop_screen.model import (
    OBSERVABLE_FIELDS,
    BedrockClient,
    MissingStubResponse,
    StubClient,
    VisionClient,
    build_prompt,
    collect,
)


def payload(image_type="product_only", **kw):
    body = {
        "image_type": image_type,
        "description": "a product",
        "visible_text": [],
        "in_scope_signals": [],
        "out_of_scope_signals": [],
        "ambiguous_signals": [],
    }
    body.update(kw)
    return body


class TestPromptIsBuiltFromFindings:
    def test_unevaluable_lines_are_removed_and_the_removal_is_declared(
        self, sop_text, sop_findings
    ):
        unevaluable = [f for f in sop_findings if f.kind is Kind.UNEVALUABLE]
        assert unevaluable, "fixture must contain unevaluable rules for this to mean anything"

        prompt = build_prompt(sop_text, sop_findings)
        cited_lines = {n for f in unevaluable for n in f.lines}
        sop_lines = sop_text.splitlines()
        for number in cited_lines:
            text = sop_lines[number - 1].strip()
            if len(text) > 25:  # short lines recur; long ones are distinctive
                assert text not in prompt, f"L{number} should have been dropped: {text!r}"
        assert "were REMOVED from the text above" in prompt
        assert f"{len(cited_lines)} policy line(s)" in prompt

    def test_with_no_findings_nothing_is_dropped_and_no_notice_appears(self, sop_text):
        prompt = build_prompt(sop_text, ())
        assert "were REMOVED" not in prompt
        for line in sop_text.splitlines():
            if len(line.strip()) > 25:
                assert line.strip() in prompt

    def test_the_fabrication_invitation_is_gone(self, sop_text, sop_findings):
        """The original asked for a free-form attribute bag. Three of the four age-rule
        source fields are catalog data, so that request could only be met by invention."""
        assert "product_attributes" not in OBSERVABLE_FIELDS
        prompt = build_prompt(sop_text, sop_findings)
        assert "product_attributes" not in prompt
        assert "Report nothing you cannot see" in prompt

    def test_the_model_is_told_not_to_classify(self, sop_text, sop_findings):
        prompt = build_prompt(sop_text, sop_findings)
        assert "not classifying" in prompt and "do not apply it" in prompt

    def test_every_observable_field_is_requested_by_name(self, sop_text, sop_findings):
        prompt = build_prompt(sop_text, sop_findings)
        for name in OBSERVABLE_FIELDS:
            assert name in prompt


class TestStubClient:
    def test_it_satisfies_the_protocol(self):
        assert isinstance(StubClient({}), VisionClient)

    def test_an_unknown_image_raises_rather_than_returning_nothing(self):
        """An empty response would reach `consolidate()` as an image that showed nothing,
        which is indistinguishable from a genuinely empty image."""
        stub = StubClient({"known.jpg": "{}"})
        with pytest.raises(MissingStubResponse, match="no stubbed response"):
            stub.describe(b"unknown.jpg", "prompt")

    def test_it_records_what_it_was_asked(self):
        stub = StubClient.from_payloads({"a.jpg": payload(), "b.jpg": payload()})
        for name in ("a.jpg", "b.jpg"):
            stub.describe(name.encode(), "the prompt")
        assert stub.calls == ["a.jpg", "b.jpg"]
        assert stub.prompts == ["the prompt", "the prompt"]

    def test_from_payloads_round_trips(self):
        stub = StubClient.from_payloads({"a.jpg": payload(description="hello")})
        assert json.loads(stub.describe(b"a.jpg", "p"))["description"] == "hello"


class TestCollect:
    def test_every_image_is_parsed(self):
        images = {"a.jpg": b"a.jpg", "b.jpg": b"b.jpg"}
        stub = StubClient.from_payloads({"a.jpg": payload(), "b.jpg": payload()})
        out = collect(images, stub, "prompt")
        assert set(out) == {"a.jpg", "b.jpg"}
        assert all(not isinstance(v, UnparseableResponse) for v in out.values())

    def test_a_transport_failure_does_not_lose_the_other_images(self):
        images = {"good.jpg": b"good.jpg", "missing.jpg": b"missing.jpg"}
        stub = StubClient.from_payloads({"good.jpg": payload(in_scope_signals=["playset"])})
        out = collect(images, stub, "prompt")

        assert isinstance(out["missing.jpg"], UnparseableResponse)
        assert "MissingStubResponse" in out["missing.jpg"].text
        assert not isinstance(out["good.jpg"], UnparseableResponse)

    def test_a_failure_becomes_a_recorded_failure_not_an_empty_image(self):
        images = {"good.jpg": b"good.jpg", "bad.jpg": b"bad.jpg"}
        stub = StubClient({"good.jpg": json.dumps(payload()), "bad.jpg": "<<<garbage>>>"})
        merged = consolidate(collect(images, stub, "prompt"))

        assert len(merged.failures) == 1
        assert [rec["image"] for rec in merged.per_image] == ["good.jpg"]

    def test_images_are_visited_in_a_deterministic_order(self):
        images = {"z.jpg": b"z.jpg", "a.jpg": b"a.jpg", "m.jpg": b"m.jpg"}
        stub = StubClient.from_payloads({n: payload() for n in images})
        collect(images, stub, "prompt")
        assert stub.calls == ["a.jpg", "m.jpg", "z.jpg"]


class TestBedrockClient:
    @staticmethod
    def _png(size=(64, 64), mode="RGB"):
        from PIL import Image

        buf = io.BytesIO()
        Image.new(mode, size, "white").save(buf, format="PNG")
        return buf.getvalue()

    def test_request_body_is_built_without_boto3_or_credentials(self):
        body = BedrockClient(client=object()).request_body(self._png(), "the prompt", ".png")
        content = body["messages"][0]["content"]

        assert body["anthropic_version"] == "bedrock-2023-05-31"
        assert content[1]["text"] == "the prompt"

    def test_a_small_png_keeps_its_own_media_type_rather_than_being_relabelled(self):
        """`prepare_image` re-encodes ONLY when over the limit. Hardcoding image/jpeg here
        would mislabel a PNG that was never converted."""
        import base64

        body = BedrockClient(client=object()).request_body(self._png(), "p", ".png")
        source = body["messages"][0]["content"][0]["source"]
        assert source["media_type"] == "image/png"
        assert base64.b64decode(source["data"])[:8] == b"\x89PNG\r\n\x1a\n"

    def test_an_oversized_image_is_shrunk_and_relabelled_jpeg(self):
        import base64

        big = self._png(size=(600, 600), mode="RGBA")
        client = BedrockClient(client=object(), max_bytes=2000)
        source = client.request_body(big, "p", ".png")["messages"][0]["content"][0]["source"]

        assert source["media_type"] == "image/jpeg", "shrinking converts, and must say so"
        shrunk = base64.b64decode(source["data"])
        assert shrunk[:3] == b"\xff\xd8\xff" and len(shrunk) <= 2000

    def test_describe_concatenates_text_blocks(self):
        class FakeBody:
            @staticmethod
            def read():
                return json.dumps(
                    {"content": [{"type": "text", "text": '{"a":'}, {"type": "text", "text": "1}"}]}
                )

        class FakeClient:
            def __init__(self):
                self.kwargs = None

            def invoke_model(self, **kwargs):
                self.kwargs = kwargs
                return {"body": FakeBody()}

        fake = FakeClient()
        client = BedrockClient(client=fake, model_id="model-x")
        assert client.describe(self._png(), "p", ".png") == '{"a":1}'
        assert fake.kwargs["modelId"] == "model-x"

    def test_it_satisfies_the_protocol(self):
        assert isinstance(BedrockClient(client=object()), VisionClient)
