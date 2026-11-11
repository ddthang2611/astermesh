from pathlib import Path

from astermesh.parser.engine import ASTEngine


def test_parser_supported_extensions():
    # arrange
    engine = ASTEngine()

    # act
    exts = engine.supported_extensions

    # assert
    assert ".py" in exts
    assert ".js" in exts
    assert ".ts" in exts
    assert engine.supports(".py")
    assert not engine.supports(".cpp")


def test_parse_python_file(tmp_path: Path):
    # arrange
    code = """import os
from math import sqrt as square_root

GLOBAL_LIMIT = 100

class Calculator:
    def add(self, a, b):
        return a + b

    async def compute(self, val):
        res = square_root(val)
        return self.add(res, GLOBAL_LIMIT)

def top_function():
    calc = Calculator()
    return calc.add(1, 2)
"""
    fpath = tmp_path / "math_mod.py"
    fpath.write_text(code, encoding="utf-8")
    engine = ASTEngine()

    # act
    result = engine.parse_file(fpath, tmp_path)

    # assert
    assert result is not None
    assert result.module_fqn == "math_mod"
    assert result.imports["square_root"] == "math.sqrt"

    names = {e.name for e in result.entities}
    assert "Calculator" in names
    assert "add" in names
    assert "compute" in names
    assert "top_function" in names
    assert "GLOBAL_LIMIT" in names

    calc_ent = next(e for e in result.entities if e.name == "Calculator")
    assert calc_ent.kind == "class"

    compute_ent = next(e for e in result.entities if e.name == "compute")
    assert compute_ent.is_async is True
    assert compute_ent.is_method is True
    assert "square_root" in compute_ent.calls
    assert "GLOBAL_LIMIT" in compute_ent.references


def test_parser_caching(tmp_path: Path):
    # arrange
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    f1.write_text("x = 1\n", encoding="utf-8")
    f2.write_text("y = 2\n", encoding="utf-8")
    engine = ASTEngine()

    # act
    engine.parse_file(f1, tmp_path)
    engine.parse_file(f2, tmp_path)

    # assert
    assert ".py" in engine._parsers
