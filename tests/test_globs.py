import pytest

from fence.globs import included, match_any


@pytest.mark.parametrize(
    ("path", "pattern", "hit"),
    [
        ("vendor/x/y.py", "vendor/**", True),
        ("src/vendor/y.py", "vendor/**", False),
        ("src/auth/login.py", "**/auth/**", True),
        ("auth/login.py", "**/auth/**", True),
        ("a/b.min.js", "**/*.min.js", True),
        ("b.min.js", "*.min.js", True),
        ("deep/b.min.js", "*.min.js", True),  # no slash: any depth, like .gitignore
        ("a/b.js", "*.min.js", False),
        ("docs/", "docs/", True),
        ("docs/x.md", "docs/", True),
        ("a/x.py", "a/?.py", True),
        ("a/xy.py", "a/?.py", False),
    ],
)
def test_match(path: str, pattern: str, hit: bool) -> None:
    assert match_any(path, [pattern]) is hit


def test_included() -> None:
    assert included("src/a.py", ["**"], ["vendor/**"])
    assert not included("vendor/a.py", ["**"], ["vendor/**"])
    assert not included("src/a.py", ["lib/**"], [])
