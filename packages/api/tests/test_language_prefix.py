from data_intelligence_api.application.language import extract_language_prefix


def test_extracts_vietnamese_prefix_and_removes_it_from_query() -> None:
    assert extract_language_prefix("vi Phân tích bộ dữ liệu này") == (
        "vi",
        "Phân tích bộ dữ liệu này",
    )
    assert extract_language_prefix("vi-VN: Phân tích bộ dữ liệu này") == (
        "vi",
        "Phân tích bộ dữ liệu này",
    )


def test_extract_language_prefix_preserves_unprefixed_queries() -> None:
    assert extract_language_prefix("Phân tích bộ dữ liệu này") == (
        None,
        "Phân tích bộ dữ liệu này",
    )
    assert extract_language_prefix("zh-CN phân tích dữ liệu") == (
        "zh",
        "phân tích dữ liệu",
    )
