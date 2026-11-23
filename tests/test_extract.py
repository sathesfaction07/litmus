"""Regressions for the three extractor defects, each pinned by reproducing the original.

The original code is quoted here rather than paraphrased. A test that asserts "the new
version is correct" proves much less than one that demonstrates the old version corrupting
real input and the new version not.
"""

from __future__ import annotations

import io
import json
import re
import warnings

import pytest

from sop_screen.extract import (
    ImagePreparationError,
    UnparseableResponse,
    consolidate,
    parse_model_json,
    prepare_image,
)

# --- the original, verbatim -------------------------------------------------------------
# text = _repair_json(text)      <- ran on EVERY response, before json.loads
# result = json.loads(text)
_ORIGINAL_PATTERN = r'("[^"]+")\s+(?=[[{"tfn\d])'


def original_repair_json(text: str) -> str:
    with warnings.catch_warnings():
        # the original also emits FutureWarning: Possible nested set, from `[` inside `[...]`
        warnings.simplefilter("ignore", FutureWarning)
        return re.sub(_ORIGINAL_PATTERN, r"\1: ", text)


def payload(**over):
    base = {
        "image_type": "packaging",
        "description": "A boxed plush toy",
        "visible_text": ["Ages 3+"],
        "in_scope_signals": ["plush construction"],
        "out_of_scope_signals": [],
        "ambiguous_signals": [],
        "product_attributes": {"material": "plush"},
    }
    base.update(over)
    return base


class TestOriginalCorruptionIsGone:
    """The defect: a repair applied unconditionally, rewriting replies that were fine."""

    CORRUPTING = [
        'The box says "Ages 3+" 5 pieces included',
        'The tag reads "Ages 3+" then a barcode',
        'Label: "Not for children" for ages 14 and up',
        'Box text "Made in China" 24 pieces',
        'Sticker "CE" tested to EN71',
    ]

    @pytest.mark.parametrize("transcription", CORRUPTING)
    def test_original_silently_corrupted_valid_input(self, transcription):
        """Premise of the whole fix: valid in, valid out, DIFFERENT meaning, no error."""
        raw = json.dumps(payload(description=transcription))
        assert json.loads(raw)["description"] == transcription  # valid to begin with

        repaired = original_repair_json(raw)
        recovered = json.loads(repaired)["description"]  # still parses -- that is the trap
        assert recovered != transcription, "this input no longer demonstrates the defect"
        assert ":" in recovered

    @pytest.mark.parametrize("transcription", CORRUPTING)
    def test_new_parser_leaves_them_exactly_alone(self, transcription):
        raw = json.dumps(payload(description=transcription))
        parsed = parse_model_json(raw)
        assert parsed.data["description"] == transcription
        assert parsed.repairs == ()
        assert parsed.was_clean

    def test_corruption_also_hit_transcribed_package_text(self):
        """visible_text is the field the age rules read, so this was the worst case."""
        entry = 'Warning: "Not for children under 3" 36 months+'
        raw = json.dumps(payload(visible_text=[entry]))
        assert json.loads(original_repair_json(raw))["visible_text"] != [entry]
        assert parse_model_json(raw).data["visible_text"] == [entry]


class TestRepairOnlyWhenNeeded:
    def test_valid_json_is_never_rewritten(self):
        raw = json.dumps(payload())
        parsed = parse_model_json(raw)
        assert parsed.data == payload()
        assert parsed.repairs == ()

    def test_code_fence_is_stripped(self):
        raw = "```json\n" + json.dumps(payload()) + "\n```"
        parsed = parse_model_json(raw)
        assert parsed.data["image_type"] == "packaging"
        assert "strip_fence" in parsed.repairs

    def test_surrounding_prose_is_discarded(self):
        raw = "Here is the analysis:\n" + json.dumps(payload()) + "\nHope that helps!"
        parsed = parse_model_json(raw)
        assert parsed.data["image_type"] == "packaging"
        assert "extract_object" in parsed.repairs

    def test_trailing_comma_is_removed(self):
        parsed = parse_model_json('{"a": 1, "b": [1, 2,], }')
        assert parsed.data == {"a": 1, "b": [1, 2]}
        assert "drop_trailing_commas" in parsed.repairs

    def test_missing_colon_in_a_key_slot_is_inserted(self):
        parsed = parse_model_json('{"image_type" "packaging", "visible_text" ["Ages 3+"]}')
        assert parsed.data == {"image_type": "packaging", "visible_text": ["Ages 3+"]}
        assert "insert_missing_colons" in parsed.repairs

    def test_a_quoted_run_inside_a_string_value_is_not_a_key_slot(self):
        """The exact distinction the original lacked. Broken structure, intact content."""
        broken = '{"description" "The box says \\"Ages 3+\\" 5 pieces"}'
        parsed = parse_model_json(broken)
        assert parsed.data["description"] == 'The box says "Ages 3+" 5 pieces'
        assert "insert_missing_colons" in parsed.repairs

    def test_a_malformed_array_is_declined_rather_than_guessed(self):
        """`["a" "b"]` has no correct repair. Inserting a colon is what broke evidence
        before, so the parser refuses and says so."""
        with pytest.raises(UnparseableResponse) as e:
            parse_model_json('{"visible_text": ["a" "b"]}')
        assert e.value.text == '{"visible_text": ["a" "b"]}'
        assert "insert_missing_colons" in e.value.attempted

    def test_a_json_array_at_top_level_is_rejected(self):
        with pytest.raises(UnparseableResponse):
            parse_model_json('["not", "an", "object"]')

    def test_repairs_are_reported_so_a_payload_can_be_weighted(self):
        raw = "```\n" + json.dumps(payload()) + "\n```"
        assert not parse_model_json(raw).was_clean


class TestProvenanceSurvivesConsolidation:
    def test_per_image_records_keep_their_signals(self):
        """The original popped the signal keys off the record before storing it."""
        results = {
            "img_01.jpg": parse_model_json(json.dumps(payload())),
            "img_02.jpg": parse_model_json(
                json.dumps(payload(in_scope_signals=["child shown holding product"]))
            ),
        }
        merged = consolidate(results)
        assert len(merged.per_image) == 2
        for record in merged.per_image:
            assert "in_scope_signals" in record, "signal keys stripped from the record again"
            assert record["image"] in ("img_01.jpg", "img_02.jpg")

    def test_each_signal_names_the_image_it_came_from(self):
        results = {
            "a.jpg": parse_model_json(json.dumps(payload(in_scope_signals=["plush"]))),
            "b.jpg": parse_model_json(
                json.dumps(payload(in_scope_signals=["plush", "bright pink"]))
            ),
        }
        merged = consolidate(results)
        in_scope = merged.of_kind("in_scope_signals")
        assert {(s.text, s.image) for s in in_scope} == {
            ("plush", "a.jpg"), ("plush", "b.jpg"), ("bright pink", "b.jpg"),
        }

    def test_a_corroborated_signal_is_not_collapsed_into_one(self):
        """Deduping by text alone made one image indistinguishable from three agreeing."""
        results = {
            f"img_{i}.jpg": parse_model_json(json.dumps(payload(in_scope_signals=["plush"])))
            for i in range(3)
        }
        merged = consolidate(results)
        assert len(merged.of_kind("in_scope_signals")) == 3
        assert len(merged.images_supporting("in_scope_signals")) == 3

    def test_failures_are_recorded_beside_the_successes(self):
        results = {
            "ok.jpg": parse_model_json(json.dumps(payload())),
            "bad.jpg": UnparseableResponse("<<garbage>>", ("strip_fence",)),
        }
        merged = consolidate(results)
        assert len(merged.per_image) == 1
        assert len(merged.failures) == 1
        assert merged.failures[0]["image"] == "bad.jpg"

    def test_empty_and_whitespace_signals_are_dropped(self):
        results = {"a.jpg": parse_model_json(json.dumps(payload(in_scope_signals=["", "  "])))}
        assert consolidate(results).of_kind("in_scope_signals") == ()


class TestTheScannerTracksEscapes:
    """ADR 002 claims repairs are string-aware. A backslash-escaped quote inside a value is
    where that claim is decided: mistake it for a string terminator and the scanner believes it
    is in a key slot, inserts a colon into prose, and the payload still parses."""

    def test_an_escaped_quote_inside_a_value_does_not_confuse_the_key_slot(self):
        raw = '{"description": "he said \\"hi\\"", "visible_text" []}'
        parsed = parse_model_json(raw)
        assert parsed.data["description"] == 'he said "hi"'
        assert parsed.data["visible_text"] == []
        assert "insert_missing_colons" in parsed.repairs

    def test_a_literal_backslash_in_a_value_survives_the_repair(self):
        raw = '{"description": "path C:\\\\temp", "visible_text" []}'
        parsed = parse_model_json(raw)
        assert parsed.data["description"] == "path C:\\temp"
        assert parsed.data["visible_text"] == []


class TestTheTrailingCommaScannerAlsoTracksEscapes:
    """A THIRD scanner. `_drop_trailing_commas` walks spans outside strings via its own escape
    tracking, separate from the colon repair's. A backslash must not desynchronise it, or a
    comma inside prose gets deleted."""

    def test_a_backslash_in_a_value_does_not_desync_the_trailing_comma_repair(self):
        raw = '{"description": "path C:\\\\temp, then stop", "visible_text": [],}'
        parsed = parse_model_json(raw)
        assert parsed.data["description"] == "path C:\\temp, then stop", (
            "the comma inside the string must survive"
        )
        assert parsed.data["visible_text"] == []
        assert "drop_trailing_commas" in parsed.repairs


class TestImagePreparation:
    @staticmethod
    def _png(mode: str, size=(2400, 2400)) -> bytes:
        from PIL import Image

        colour = (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)
        buf = io.BytesIO()
        Image.new(mode, size, colour).save(buf, format="PNG")
        return buf.getvalue()

    def test_an_oversized_rgba_png_no_longer_raises(self):
        """The original: OSError: cannot write mode RGBA as JPEG, swallowed by a bare
        except and counted as a load failure. `.png` is an accepted input format."""
        data = self._png("RGBA")
        out, media = prepare_image(data, ".png", max_bytes=len(data) // 4)
        assert media == "image/jpeg"
        assert len(out) <= len(data) // 4

        from PIL import Image

        assert Image.open(io.BytesIO(out)).mode == "RGB"

    def test_an_oversized_rgb_png_still_works(self):
        data = self._png("RGB")
        out, media = prepare_image(data, ".png", max_bytes=len(data) // 4)
        assert len(out) <= len(data) // 4 and media == "image/jpeg"

    @staticmethod
    def _noise_png(size=(400, 400)) -> bytes:
        """Noise, because a flat colour compresses so well as PNG that JPEG can never win
        without downscaling -- which is why the quality-only path went untested."""
        import random

        from PIL import Image

        rng = random.Random(1234)
        raw = bytes(rng.randrange(256) for _ in range(size[0] * size[1]))
        buf = io.BytesIO()
        Image.frombytes("L", size, raw).save(buf, format="PNG")
        return buf.getvalue()

    def test_quality_reduction_alone_avoids_downscaling(self):
        """The ladder's success path: if a lower JPEG quality fits, the image keeps its
        dimensions. Resizing loses information a policy rule might depend on reading."""
        from PIL import Image

        data = self._noise_png()
        out, media = prepare_image(data, ".png", max_bytes=len(data) - 1)

        assert media == "image/jpeg"
        assert len(out) <= len(data) - 1
        assert Image.open(io.BytesIO(out)).size == (400, 400), "must not have been resized"

    def test_a_small_image_is_passed_through_untouched(self):
        data = self._png("RGBA", size=(8, 8))
        out, media = prepare_image(data, ".png", max_bytes=10_000_000)
        assert out == data, "a small image must not be re-encoded"
        assert media == "image/png"

    def test_an_impossible_limit_raises_instead_of_sending_it_anyway(self):
        data = self._png("RGB")
        with pytest.raises(ImagePreparationError, match="floor was reached"):
            prepare_image(data, ".png", max_bytes=8)

    def test_an_unknown_suffix_falls_back_to_jpeg(self):
        assert prepare_image(b"x" * 10, ".bmp", max_bytes=100) == (b"x" * 10, "image/jpeg")
