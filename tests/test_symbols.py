import textwrap

import pytest

from fence.diffing.symbols import MODULE, enclosing_symbol

SOURCE = textwrap.dedent(
    """\
    import time

    RETRIES = 3


    def charge(card):
        time.sleep(2)
        return card


    class Gateway:
        timeout = 5

        @staticmethod
        @property
        def ping():
            def helper():
                return 1
            return helper()

        async def send(self):
            await self.flush()


    if True:
        def guarded():
            pass
    """
)


@pytest.mark.parametrize(
    ("line", "qualname", "kind"),
    [
        (1, MODULE, "module"),
        (3, MODULE, "module"),
        (6, "charge", "function"),
        (7, "charge", "function"),
        (11, "Gateway", "class"),
        (12, "Gateway", "class"),
        (14, "Gateway.ping", "function"),  # decorator line belongs to the function
        (15, "Gateway.ping", "function"),
        (18, "Gateway.ping.helper", "function"),
        (19, "Gateway.ping", "function"),
        (22, "Gateway.send", "function"),
        (26, "guarded", "function"),  # def nested in an if-block
    ],
)
def test_enclosing_symbol(line: int, qualname: str, kind: str) -> None:
    sym = enclosing_symbol(SOURCE, line)
    assert sym is not None
    assert (sym.qualname, sym.kind) == (qualname, kind)


def test_decorated_function_span_starts_at_decorator() -> None:
    sym = enclosing_symbol(SOURCE, 16)
    assert sym is not None and sym.start_line == 14 and sym.end_line == 19


def test_syntax_error_returns_none() -> None:
    assert enclosing_symbol("def broken(:\n    pass\n", 1) is None


def test_line_past_end_returns_none() -> None:
    assert enclosing_symbol("x = 1\n", 5) is None


def test_line_zero_is_an_error() -> None:
    with pytest.raises(ValueError):
        enclosing_symbol("x = 1\n", 0)
