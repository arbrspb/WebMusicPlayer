from app.config import add_recent_folder, normalize_recent_folders


def test_recent_folders_are_normalized_deduplicated_and_limited():
    folders = [
        "2025\\Club House",
        "2025/Club House",
        "../outside",
        r"Z:\absolute",
        *[f"Collection/{index}" for index in range(12)],
    ]

    result = normalize_recent_folders(folders)

    assert result[0] == "2025/Club House"
    assert len(result) == 8
    assert "../outside" not in result
    assert all(not value.startswith("Z:") for value in result)


def test_add_recent_folder_moves_existing_folder_to_front():
    result = add_recent_folder(
        ["House/Week 1", "House/Week 2", "House/Week 3"],
        "house\\week 2",
    )

    assert result[0] == "house/week 2"
    assert len(result) == 3
    assert sum(value.casefold() == "house/week 2" for value in result) == 1
