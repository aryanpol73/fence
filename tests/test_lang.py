import pytest

from fence.anchors.fingerprint import containment, fingerprint, jaccard, same_code
from fence.lang import enclosing_symbol, plugin_for, supported_languages, tokens_for

JS = """\
// payments
class Client {
  charge(card) {
    if (!card) { throw new Error('no card'); }
    return 1;
  }
}

const retry = async (fn) => {
  return fn();
};

function helper() { return 2 }
"""

TS = """\
interface Opts { timeout: number }
export class Svc {
  private run(o: Opts): number {
    return o.timeout;
  }
}
"""

GO = """\
package pay

type Client struct{}

func (c *Client) Charge(n int) error {
	if n < 0 {
		return nil
	}
	return nil
}

func New() *Client { return &Client{} }
"""

JAVA = """\
public class Gateway {
    @Override
    public void charge(int x) {
        synchronized (this) {
            x++;
        }
    }
}
"""


@pytest.mark.parametrize(
    ("path", "src", "line", "qualname"),
    [
        ("a.js", JS, 4, "Client.charge"),
        ("a.js", JS, 10, "retry"),
        ("a.js", JS, 13, "helper"),
        ("a.js", JS, 1, "<module>"),
        ("a.ts", TS, 4, "Svc.run"),
        ("a.tsx", TS, 1, "Opts"),
        ("a.go", GO, 6, "Client.Charge"),
        ("a.go", GO, 12, "New"),
        ("A.java", JAVA, 5, "Gateway.charge"),
        ("x.py", "class A:\n    def m(self):\n        return 1\n", 3, "A.m"),
    ],
)
def test_enclosing_symbol_per_language(path: str, src: str, line: int, qualname: str) -> None:
    sym = enclosing_symbol(path, src, line)
    assert sym is not None and sym.qualname == qualname


def test_java_annotation_is_inside_method_span() -> None:
    sym = enclosing_symbol("A.java", JAVA, 2)
    assert sym is not None and sym.qualname == "Gateway.charge"


def test_unsupported_language_returns_none() -> None:
    assert plugin_for("a.rb") is None
    assert enclosing_symbol("a.rb", "def x\nend\n", 1) is None


def test_languages_can_be_disabled() -> None:
    assert plugin_for("a.go", enabled=["python"]) is None
    assert plugin_for("a.tsx", enabled=["typescript"]) is not None


def test_supported_languages_meet_brief_minimum() -> None:
    assert {"python", "javascript", "typescript", "go", "java"} <= set(supported_languages())


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("a.py", "def f(a,b):\n  return g('x', [1,2,],)  # c\n",
         'def f(a, b):\n    return g(\n        "x",\n        [1, 2],\n    )\n'),
        ("a.js", "function f(){return g('x')}\n", 'function f() {\n  // note\n  return g("x");\n}\n'),
        ("a.go", "package p\nfunc f() int { return 1 }\n", "package p\n\nfunc f() int {\n\treturn 1\n}\n"),
        ("A.java", "class A{void m(){x(\"s\");}}", "class A {\n  // c\n  void m() {\n    x(\"s\");\n  }\n}\n"),
    ],
)
def test_reformatting_keeps_tokens_equal(path: str, before: str, after: str) -> None:
    assert tokens_for(path, before) == tokens_for(path, after)


@pytest.mark.parametrize(
    ("path", "before", "after"),
    [
        ("a.py", "x = 1\n", "x = 2\n"),
        ("a.js", "if (a) { b() }\n", "if (!a) { b() }\n"),
        ("a.go", "package p\nvar x = 1\n", "package p\nvar x = 2\n"),
    ],
)
def test_real_changes_change_tokens(path: str, before: str, after: str) -> None:
    assert tokens_for(path, before) != tokens_for(path, after)


def test_fingerprint_finds_moved_code_with_small_edit() -> None:
    body = "def charge(gw, card):\n    for i in range(3):\n        try:\n            return gw.c(card)\n" \
           "        except Timeout:\n            time.sleep(2)\n"
    edited = body.replace("range(3)", "range(4)")
    a = fingerprint(tokens_for("a.py", body))
    b = fingerprint(tokens_for("b.py", edited))
    assert a.exact != b.exact
    assert jaccard(a.shingles, b.shingles) > 0.5
    unrelated = fingerprint(tokens_for("c.py", "def other():\n    return {'k': [1, 2, 3]}\n"))
    assert not same_code(a, unrelated)
    big = fingerprint(tokens_for("d.py", "import os\n\n" + body + "\n\ndef z():\n    pass\n"))
    assert containment(a.shingles, big.shingles) == 1.0


def test_fingerprint_roundtrip() -> None:
    fp = fingerprint(["a", "b", "c"])
    from fence.anchors.fingerprint import Fingerprint

    assert Fingerprint.from_json(fp.to_json()) == fp
