from src.rag.splitter import Chunk, iter_structured_lines


def test_incremental_split_matches_full_text_split() -> None:
    text = (
        "# Chapter\n\n"
        "First paragraph with enough text to split.\n\n"
        "## Section\n\n"
        "Second paragraph.\n"
    )

    from_lines = list(iter_structured_lines(text.splitlines(), chunk_size=30, overlap=5))
    from_iter = list(
        iter_structured_lines(iter(text.splitlines(keepends=True)), chunk_size=30, overlap=5)
    )

    assert from_iter == from_lines
    assert from_lines == [
        Chunk(
            text="# Chapter\n\nFirst paragraph with enough text to split.",
            heading_path=["Chapter"],
            line_start=2,
            line_end=2,
        ),
        Chunk(
            text="# Chapter\n## Section\n\nSecond paragraph.",
            heading_path=["Chapter", "Section"],
            line_start=6,
            line_end=6,
        ),
    ]
