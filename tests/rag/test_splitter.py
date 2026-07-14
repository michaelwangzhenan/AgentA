from src.rag.splitter import iter_structured_lines, split_structured


def test_incremental_split_matches_full_text_split() -> None:
    text = (
        "# Chapter\n\n"
        "First paragraph with enough text to split.\n\n"
        "## Section\n\n"
        "Second paragraph.\n"
    )

    expected = split_structured(text, chunk_size=30, overlap=5)
    actual = list(iter_structured_lines(iter(text.splitlines(keepends=True)), 30, 5))

    assert actual == expected
