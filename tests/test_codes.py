import pytest

from app.keywords.codes import ClassifierCode, CodeMatcher, code_matches, expand_code_cell


def test_expand_simple_and_list():
    assert expand_code_cell("13.92.12") == ["13.92.12"]
    assert expand_code_cell("14.14.13 / 14.14.14") == ["14.14.13", "14.14.14"]


def test_expand_ktru_range():
    assert expand_code_cell("13.92.14.000-00000001…03") == [
        "13.92.14.000-00000001",
        "13.92.14.000-00000002",
        "13.92.14.000-00000003",
    ]
    assert expand_code_cell("13.92.14.000-00000008...11") == [
        "13.92.14.000-00000008",
        "13.92.14.000-00000009",
        "13.92.14.000-00000010",
        "13.92.14.000-00000011",
    ]


def test_expand_rejects_garbage():
    with pytest.raises(ValueError):
        expand_code_cell("бельё")


def test_prefix_matching():
    assert code_matches("13.92.12", "13.92.12.110")
    assert code_matches("13.92.14", "13.92.14.000-00000002")
    assert code_matches("13.92.12", "13.92.12")
    assert not code_matches("13.92.1", "13.92.12.110")
    assert not code_matches("13.92.12", "13.92.120")


def _code(code, excluded=False):
    return ClassifierCode(
        code=code, classifier="ОКПД2", name="", goods="", priority=None if excluded else 1, excluded=excluded
    )


def test_longest_match_wins_and_exclusion():
    matcher = CodeMatcher([_code("13.92.12"), _code("13.92.12.160", excluded=True), _code("13.92.12.110")])
    assert matcher.match("13.92.12.110").code == "13.92.12.110"
    assert matcher.match("13.92.12.190").code == "13.92.12"
    assert matcher.match("13.92.12.160-00000001").excluded
    assert matcher.match("14.13.21") is None
