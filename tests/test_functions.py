"""函数体测试: 变量含义表、必填规则、重新检测的合并语义、历史联动。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.functions import (  # noqa: E402
    REQUIRED_SYMBOL_KINDS,
    SYMBOL_KIND_LABELS,
    Function,
    Symbol,
    symbol_kind_label,
)
from codemethod.core.repository import Repository  # noqa: E402

SAMPLE = '''def reverse(s: str, sep: str = "") -> str:
    """把字符串逆序。"""
    result = list(s)
    for i in range(len(result) // 2):
        result[i], result[-i - 1] = result[-i - 1], result[i]
    return sep.join(result)
'''


class TestSymbol(unittest.TestCase):
    def test_kind_label(self):
        self.assertEqual(Symbol(name="x", kind="parameter").kind_label, "参数")
        self.assertEqual(symbol_kind_label("local"), "局部变量")
        self.assertEqual(symbol_kind_label("unknown-kind"), "unknown-kind")

    def test_meaning_required_only_for_contract_kinds(self):
        for kind in REQUIRED_SYMBOL_KINDS:
            self.assertTrue(Symbol(name="x", kind=kind).needs_meaning, kind)
        self.assertFalse(Symbol(name="i", kind="local").needs_meaning)
        self.assertFalse(Symbol(name="C", kind="constant").needs_meaning)

    def test_blank_and_signature_hint(self):
        symbol = Symbol(name="sep", kind="parameter", type="str", default='""')
        self.assertTrue(symbol.is_blank)
        self.assertEqual(symbol.signature_hint, 'sep: str = ""')
        symbol.meaning = "分隔符"
        self.assertFalse(symbol.is_blank)

    def test_key_of_ignores_language_but_not_kind(self):
        self.assertEqual(Symbol.key_of("x", "parameter"), "parameter:x")
        self.assertNotEqual(Symbol.key_of("x", "parameter"), Symbol.key_of("x", "local"))

    def test_roundtrip_and_clone(self):
        symbol = Symbol(name="a", kind="field", type="int", meaning="计数", detail="第 3 行")
        self.assertEqual(Symbol.from_dict(symbol.to_dict()).to_dict(), symbol.to_dict())
        self.assertEqual(symbol.clone().to_dict(), symbol.to_dict())

    def test_fields_are_trimmed(self):
        symbol = Symbol(name="  a  ", kind="  local  ", meaning="  含义  ")
        self.assertEqual((symbol.name, symbol.kind, symbol.meaning), ("a", "local", "含义"))


class TestFunctionModel(unittest.TestCase):
    def _function(self, **kwargs):
        defaults = dict(name="reverse", language="python", code=SAMPLE,
                        prerequisites="Python 3.10+")
        defaults.update(kwargs)
        return Function(**defaults)

    def test_kind_interface(self):
        function = self._function()
        self.assertEqual(function.kind, "function")
        self.assertEqual(function.kind_label, "函数体")
        self.assertEqual(function.display_title, "reverse")
        self.assertEqual(function.badge_label, "0 变量")
        self.assertEqual(function.languages, ["python"])

    def test_unnamed_function_title(self):
        self.assertEqual(Function().display_title, "未命名函数")

    def test_total_lines(self):
        self.assertEqual(self._function().total_lines, len(SAMPLE.splitlines()))

    def test_set_symbols_dedups_by_kind_and_name(self):
        function = self._function()
        function.set_symbols([
            Symbol(name="s", kind="parameter", meaning="第一"),
            Symbol(name="s", kind="parameter", meaning="第二"),
            Symbol(name="s", kind="local"),
            Symbol(name="", kind="local"),
        ])
        self.assertEqual(len(function.symbols), 2)

    def test_missing_meaning_reporting(self):
        function = self._function()
        function.set_symbols([
            Symbol(name="s", kind="parameter"),          # 必填未填
            Symbol(name="i", kind="local"),              # 可留空
            Symbol(name="r", kind="return", meaning="结果"),
        ])
        self.assertEqual([s.name for s in function.required_symbols_missing_meaning], ["s"])
        self.assertTrue(function.has_unresolved_symbols)
        self.assertEqual(len(function.symbols_missing_meaning), 2)

    def test_no_unresolved_when_all_filled(self):
        function = self._function()
        function.set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        self.assertFalse(function.has_unresolved_symbols)

    def test_apply_detected_adds_new(self):
        function = self._function()
        added = function.apply_detected([
            Symbol(name="a", kind="parameter", type="int"),
            Symbol(name="b", kind="local"),
        ])
        self.assertEqual(added, 2)
        self.assertEqual({s.name for s in function.symbols}, {"a", "b"})

    def test_apply_detected_never_overwrites_meaning(self):
        """这是整个功能最重要的不变量: 重新检测不能抹掉用户写的含义。"""
        function = self._function()
        function.set_symbols([Symbol(name="a", kind="parameter", meaning="我写的含义")])
        function.apply_detected([
            Symbol(name="a", kind="parameter", type="int", detail="第 1 行"),
            Symbol(name="b", kind="parameter"),
        ])
        a = next(s for s in function.symbols if s.name == "a")
        self.assertEqual(a.meaning, "我写的含义")
        self.assertEqual(a.type, "int")          # 类型会被刷新
        self.assertEqual(a.detail, "第 1 行")

    def test_apply_detected_keeps_vanished_symbols(self):
        function = self._function()
        function.set_symbols([Symbol(name="old", kind="parameter", meaning="保留我")])
        function.apply_detected([Symbol(name="new", kind="parameter")])
        names = {s.name for s in function.symbols}
        self.assertEqual(names, {"old", "new"})
        self.assertEqual(
            next(s for s in function.symbols if s.name == "old").meaning, "保留我"
        )

    def test_apply_detected_skips_locals_shadowing_parameters(self):
        """参数被重新赋值时检测器会同时报同名局部量, 不应重复出现在表里。"""
        function = self._function()
        function.apply_detected([
            Symbol(name="base", kind="parameter", type="int"),
            Symbol(name="base", kind="local", type="int"),
            Symbol(name="other", kind="local"),
        ])
        keys = [(s.name, s.kind) for s in function.symbols]
        self.assertIn(("base", "parameter"), keys)
        self.assertNotIn(("base", "local"), keys)
        self.assertIn(("other", "local"), keys)

    def test_apply_detected_orders_by_kind(self):
        function = self._function()
        function.apply_detected([
            Symbol(name="l", kind="local"),
            Symbol(name="p", kind="parameter"),
            Symbol(name="r", kind="return"),
            Symbol(name="c", kind="constant"),
        ])
        self.assertEqual([s.kind for s in function.symbols],
                         ["parameter", "return", "constant", "local"])

    def test_symbols_by_kind(self):
        function = self._function()
        function.set_symbols([
            Symbol(name="a", kind="parameter"),
            Symbol(name="b", kind="parameter"),
            Symbol(name="c", kind="local"),
        ])
        grouped = function.symbols_by_kind
        self.assertEqual(len(grouped["parameter"]), 2)
        self.assertEqual(len(grouped["local"]), 1)

    def test_get_symbol(self):
        function = self._function()
        function.set_symbols([Symbol(name="a", kind="parameter")])
        self.assertIsNotNone(function.get_symbol("parameter:a"))
        self.assertIsNone(function.get_symbol("local:a"))

    def test_search_blob_includes_meanings(self):
        function = self._function()
        function.set_symbols([Symbol(name="s", kind="parameter", meaning="待反转的字符串")])
        self.assertIn("待反转", function.search_blob)
        self.assertIn("sep", function.search_blob)

    def test_roundtrip(self):
        function = self._function()
        function.set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        restored = Function.from_dict(function.to_dict())
        self.assertEqual(restored.to_dict(), function.to_dict())
        self.assertEqual(restored.symbols[0].meaning, "输入")

    def test_clone_is_independent(self):
        function = self._function()
        function.set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        clone = function.clone()
        clone.symbols[0].meaning = "改过了"
        self.assertEqual(function.symbols[0].meaning, "输入")

    def test_invalid_status_resets(self):
        self.assertEqual(Function(name="f", status="bogus").status, "planned")


class TestFunctionRepository(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="r")
        self.function = self.repo.create_function(
            "reverse", "python", SAMPLE, prerequisites="Python 3.10+"
        )

    def test_create_detects_symbols(self):
        self.assertGreater(len(self.function.symbols), 0)
        names = {s.name for s in self.function.symbols}
        self.assertIn("s", names)
        self.assertIn("sep", names)

    def test_detection_can_be_skipped(self):
        function = self.repo.create_function("x", "python", SAMPLE, detect=False)
        self.assertEqual(function.symbols, [])

    def test_create_records_history(self):
        actions = [r.action for r in self.repo.revisions(self.function.id, descending=False)]
        self.assertEqual(actions, ["create"])

    def test_update_produces_single_revision(self):
        before = self.repo.history.count(self.function.id)
        self.repo.update_function(
            self.function.id, name="rev2", prerequisites="Python 3.12+",
            symbols=[Symbol(name="s", kind="parameter", meaning="输入")],
        )
        self.assertEqual(self.repo.history.count(self.function.id), before + 1)
        self.assertIn("prerequisites", self.repo.revisions(self.function.id)[0].snapshot)

    def test_update_records_meaning_changes_in_summary(self):
        symbols = [s.clone() for s in self.function.symbols]
        for symbol in symbols:
            if symbol.name == "s":
                symbol.meaning = "待反转的字符串"
        self.repo.set_function_symbols(self.function.id, symbols)
        summary = self.repo.revisions(self.function.id)[0].summary
        self.assertIn("含义", summary)

    def test_redetect_preserves_meanings_and_is_tracked(self):
        symbols = [s.clone() for s in self.function.symbols]
        for symbol in symbols:
            symbol.meaning = f"含义-{symbol.name}"
        self.repo.set_function_symbols(self.function.id, symbols)
        before = {s.name: s.meaning for s in self.repo.require_function(self.function.id).symbols}

        self.repo.detect_function_symbols(self.function.id)
        after = {s.name: s.meaning for s in self.repo.require_function(self.function.id).symbols}
        for name, meaning in before.items():
            self.assertEqual(after[name], meaning, name)

    def test_diff_shows_meaning_change(self):
        symbols = [s.clone() for s in self.function.symbols]
        target = next(s for s in symbols if s.name == "s")
        target.meaning = "输入字符串"
        self.repo.set_function_symbols(self.function.id, symbols)
        diff = self.repo.revision_diff(self.repo.revisions(self.function.id)[0].id)
        self.assertIn("含义", diff)
        self.assertIn("输入字符串", diff)

    def test_soft_delete_and_restore(self):
        self.repo.delete_function(self.function.id)
        self.assertEqual(len(self.repo.items("function")), 0)
        self.repo.restore_function(self.function.id)
        self.assertEqual(len(self.repo.items("function")), 1)

    def test_hard_delete_keeps_history(self):
        self.repo.delete_function(self.function.id, hard=True)
        self.assertIsNone(self.repo.functions.get(self.function.id))
        self.assertGreater(self.repo.history.count(self.function.id), 0)

    def test_restore_revision(self):
        first = self.repo.revisions(self.function.id, descending=False)[0]
        self.repo.update_function(self.function.id, name="改过的名字")
        self.repo.restore_revision(self.function.id, first.id)
        self.assertEqual(self.repo.require_function(self.function.id).name, "reverse")

    def test_undo_redo(self):
        self.repo.update_function(self.function.id, name="A")
        self.repo.update_function(self.function.id, name="B")
        self.repo.undo(self.function.id)
        self.assertEqual(self.repo.require_function(self.function.id).name, "A")
        self.repo.redo(self.function.id)
        self.assertEqual(self.repo.require_function(self.function.id).name, "B")

    def test_repository_roundtrip_keeps_symbols_and_meanings(self):
        symbols = [s.clone() for s in self.function.symbols]
        next(s for s in symbols if s.name == "s").meaning = "输入"
        self.repo.set_function_symbols(self.function.id, symbols)

        restored = Repository.from_dict(self.repo.to_dict())
        function = restored.require_function(self.function.id)
        self.assertEqual(
            [(s.name, s.kind, s.meaning) for s in function.symbols],
            [(s.name, s.kind, s.meaning) for s in self.repo.require_function(self.function.id).symbols],
        )
        self.assertEqual(restored.history.count(self.function.id),
                         self.repo.history.count(self.function.id))

    def test_language_change_redetects(self):
        self.repo.update_function(
            self.function.id, language="go",
            code='package main\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n',
        )
        self.repo.detect_function_symbols(self.function.id)
        names = {s.name for s in self.repo.require_function(self.function.id).symbols}
        self.assertIn("a", names)
        self.assertIn("b", names)

    def test_statistics_counts_functions_and_symbols(self):
        stats = self.repo.statistics()
        self.assertEqual(stats["functions"], 1)
        self.assertGreater(stats["function_symbols"], 0)
        self.assertEqual(stats["symbols_missing_meaning"],
                         len(self.function.required_symbols_missing_meaning)
                         + len([s for s in self.function.symbols_missing_meaning
                                if not s.needs_meaning]))

    def test_kind_of_function(self):
        self.assertEqual(self.repo.kind_of(self.function.id), "function")
        self.assertIs(self.repo.get_item(self.function.id), self.function)


class TestFunctionAcrossKinds(unittest.TestCase):
    """三类实体互不干扰, 但同处一个库与一套历史里。"""

    def setUp(self):
        self.repo = Repository(name="all")
        self.module = self.repo.create_entry("模块", "d", "p", ["t"])
        self.space = self.repo.create_space("空间", "d", "p", ["t"])
        self.function = self.repo.create_function(
            "fn", "python", "def fn(a: int) -> int:\n    return a\n", prerequisites="3.10+"
        )

    def test_three_kinds_coexist(self):
        kinds = {item.kind for item in self.repo.items()}
        self.assertEqual(kinds, {"module", "space", "function"})

    def test_each_has_independent_prerequisites(self):
        self.repo.update_entry(self.module.id, prerequisites="模块的")
        self.repo.update_space(self.space.id, prerequisites="空间的")
        self.repo.update_function(self.function.id, prerequisites="函数的")
        self.assertEqual(self.repo.require(self.module.id).prerequisites, "模块的")
        self.assertEqual(self.repo.require_space(self.space.id).prerequisites, "空间的")
        self.assertEqual(self.repo.require_function(self.function.id).prerequisites, "函数的")

    def test_history_is_isolated_per_entity(self):
        self.repo.put_space_file(self.space.id, "a.py", "1")
        self.assertEqual(len(self.repo.revisions(self.module.id)), 1)
        self.assertEqual(len(self.repo.revisions(self.space.id)), 2)
        self.assertEqual(len(self.repo.revisions(self.function.id)), 1)

    def test_one_container_holds_everything(self):
        self.repo.put_space_file(self.space.id, "README.md", "# hi")
        restored = Repository.from_dict(self.repo.to_dict())
        self.assertEqual(len(restored.entries), 1)
        self.assertEqual(len(restored.spaces), 1)
        self.assertEqual(len(restored.functions), 1)
        self.assertEqual(len(restored.items()), 3)

    def test_tag_registry_covers_all_kinds(self):
        self.repo.update_space(self.space.id, tags=["空间标签"])
        self.repo.update_function(self.function.id, tags=["函数标签"])
        tags = self.repo.all_tags()
        self.assertIn("空间标签", tags)
        self.assertIn("函数标签", tags)

    def test_kind_of_snapshot_autodetects(self):
        from codemethod.core.history import kind_of_snapshot

        self.assertEqual(kind_of_snapshot(self.repo.to_dict()["entries"][0]), "module")
        self.assertEqual(kind_of_snapshot(self.repo.to_dict()["spaces"][0]), "space")
        self.assertEqual(kind_of_snapshot(self.repo.to_dict()["functions"][0]), "function")
        self.assertEqual(kind_of_snapshot({}), "module")


if __name__ == "__main__":
    unittest.main()
