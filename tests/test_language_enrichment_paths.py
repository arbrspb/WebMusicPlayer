import os

import pytest

from app.language_enrichment import _safe_music_path


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")


@pytest.mark.parametrize("root, relative", [
    (r"C:\Music", r"Club\track.mp3"),
    (r"\\server\share\Music", r"2025\track.mp3"),
    (r"\\server\share\Music", r"2025\Deep\Set\track.mp3"),
])
def test_safe_music_path_accepts_local_and_unc_children(root, relative):
    result = _safe_music_path(root, relative)
    assert os.path.normcase(result).startswith(os.path.normcase(os.path.abspath(root)))


def test_safe_music_path_is_case_insensitive_on_windows():
    result = _safe_music_path(r"C:\Music", r"Club\TRACK.mp3")
    assert os.path.normcase(result) == os.path.normcase(r"C:\Music\Club\track.mp3")


@pytest.mark.parametrize("relative", [r"..\outside.mp3", r"..\..\outside.mp3"])
def test_safe_music_path_rejects_parent_escape(relative):
    with pytest.raises(ValueError):
        _safe_music_path(r"C:\Music", relative)


@pytest.mark.parametrize("root, candidate", [
    (r"C:\Music", r"C:\MusicBackup\track.mp3"),
    (r"\\server\share\Music", r"\\server\share\MusicBackup\track.mp3"),
])
def test_safe_music_path_rejects_similar_sibling_prefix(root, candidate):
    with pytest.raises(ValueError):
        _safe_music_path(root, candidate)
