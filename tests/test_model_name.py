from athena.__main__ import _model_pulled, _normalize_model_name


def test_normalize_adds_latest():
    assert _normalize_model_name("troofevades") == "troofevades:latest"


def test_normalize_keeps_explicit_tag():
    assert _normalize_model_name("llama3.1:8b") == "llama3.1:8b"


def test_pulled_matches_unsuffixed_request():
    # User asked for "troofevades"; API returns "troofevades:latest".
    assert _model_pulled("troofevades", ["troofevades:latest", "qwen2.5-coder:14b"])


def test_pulled_matches_suffixed_request():
    assert _model_pulled("troofevades:latest", ["troofevades:latest"])


def test_not_pulled_when_truly_absent():
    assert not _model_pulled("does-not-exist", ["troofevades:latest", "llama3.1:8b"])


def test_not_pulled_treats_different_tags_as_different():
    # 8b and 70b are distinct; should not match.
    assert not _model_pulled("llama3.1:70b", ["llama3.1:8b"])
