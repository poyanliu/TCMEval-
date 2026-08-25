"""Tests for utility modules: response parser, document parser, prompt builder."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.utils.response_parser import parse_llm_response
from backend.utils.document_parser import truncate_text, detect_format
from backend.services.prompt_builder import build_dimension_prompt


class TestResponseParser:
    """Test the LLM response parser's extraction chain."""

    def test_clean_json(self):
        result = parse_llm_response(
            '{"score": 8, "evidence": "契合度高", "comment": "良好"}'
        )
        assert result["score"] == 8
        assert result["evidence"] == "契合度高"
        assert result["comment"] == "良好"

    def test_markdown_wrapped(self):
        result = parse_llm_response(
            '```json\n{"score": 7, "evidence": "证据", "comment": "评语"}\n```'
        )
        assert result["score"] == 7

    def test_chinese_prefix(self):
        result = parse_llm_response(
            '好的，以下是评价结果：\n{"score": 6, "evidence": "e", "comment": "c"}'
        )
        assert result["score"] == 6

    def test_score_only_fallback(self):
        result = parse_llm_response("评分：9分")
        assert result["score"] == 9

    def test_out_of_range_clamped(self):
        result = parse_llm_response('{"score": 15, "evidence": "x", "comment": "y"}')
        assert result["score"] == 10

    def test_zero_score_clamped(self):
        result = parse_llm_response('{"score": -1, "evidence": "x", "comment": "y"}')
        assert result["score"] == 1

    def test_empty_input(self):
        result = parse_llm_response("")
        assert result["score"] >= 1

    def test_none_input(self):
        result = parse_llm_response(None)
        assert result["score"] >= 1

    def test_bracket_balanced(self):
        result = parse_llm_response(
            '一些废话 {\n  "score": 5,\n  "evidence": "提取的证据",\n  "comment": "还行"\n} 更多废话'
        )
        assert result["score"] == 5
        assert result["evidence"] == "提取的证据"


class TestDocumentParser:
    """Test document format detection and text truncation."""

    def test_detect_pdf(self):
        assert detect_format("test.pdf") == "application/pdf"

    def test_detect_docx(self):
        assert detect_format("test.docx") == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    def test_detect_unknown(self):
        assert detect_format("test.txt") is None

    def test_detect_case_insensitive(self):
        assert detect_format("TEST.PDF") == "application/pdf"

    def test_truncate_short_text(self):
        text = "short text"
        assert truncate_text(text, 100) == text

    def test_truncate_long_text(self):
        text = "x" * 7000
        result = truncate_text(text, 6000)
        assert len(result) <= 6050  # 6000 + truncation marker
        assert "截断" in result


class TestPromptBuilder:
    """Test evaluation prompt construction."""

    def test_basic_prompt(self):
        prompt = build_dimension_prompt("测试文献", "政策契合度")
        assert "政策契合度" in prompt
        assert "测试文献" in prompt
        assert "score" in prompt
        assert "evidence" in prompt
        assert "comment" in prompt

    def test_long_text_truncation(self):
        long_text = "长文本" * 3000
        prompt = build_dimension_prompt(long_text, "理论深度", max_chars=1000)
        assert "截断" in prompt

    def test_all_dimensions_have_desc(self):
        from shared.constants import DIMENSIONS
        for dim in DIMENSIONS:
            prompt = build_dimension_prompt("测试", dim)
            assert dim in prompt


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
