"""函数体测试: 多语言实现、变量含义表、必填规则、重新检测的合并语义、历史联动。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.functions import (  # noqa: E402
    REQUIRED_SYMBOL_KINDS,
    SYMBOL_KIND_LABELS,
    Function,
    FunctionImplementation,
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

GO_SAMPLE = '''package main

func Reverse(s string, sep string) string {
	result := []rune(s)
	for i := 0; i < len(result)/2; i++ {
		result[i], result[len(result)-i-1] = result[len(result)-i-1], result[i]
	}
	return strings.Join(strings.Split(string(result), ""), sep)
}
'''


def make_implementation(**kwargs) -> FunctionImplementation:
    defaults = dict(language="python", code=SAMPLE, prerequisites="Python 3.10+")
    defaults.update(kwargs)
    return FunctionImplementation(**defaults)


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


class TestFunctionImplementationModel(unittest.TestCase):
    """每种语言的实现自己持有一张变量含义表与前置要求。"""

    def _impl(self, **kwargs):
        return make_implementation(**kwargs)

    def test_kind_interface(self):
        impl = self._impl()
        self.assertEqual(impl.language, "python")
        self.assertEqual(impl.language_name, "Python")
        self.assertEqual(impl.badge_label, "0 变量")

    def test_set_symbols_dedups_by_kind_and_name(self):
        impl = self._impl()
        impl.set_symbols([
            Symbol(name="s", kind="parameter", meaning="第一"),
            Symbol(name="s", kind="parameter", meaning="第二"),
            Symbol(name="s", kind="local"),
            Symbol(name="", kind="local"),
        ])
        self.assertEqual(len(impl.symbols), 2)

    def test_missing_meaning_reporting(self):
        impl = self._impl()
        impl.set_symbols([
            Symbol(name="s", kind="parameter"),          # 必填未填
            Symbol(name="i", kind="local"),              # 可留空
            Symbol(name="r", kind="return", meaning="结果"),
        ])
        self.assertEqual([s.name for s in impl.required_symbols_missing_meaning], ["s"])
        self.assertTrue(impl.has_unresolved_symbols)
        self.assertEqual(len(impl.symbols_missing_meaning), 2)

    def test_no_unresolved_when_all_filled(self):
        impl = self._impl()
        impl.set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        self.assertFalse(impl.has_unresolved_symbols)

    def test_apply_detected_adds_new(self):
        impl = self._impl()
        added = impl.apply_detected([
            Symbol(name="a", kind="parameter", type="int"),
            Symbol(name="b", kind="local"),
        ])
        self.assertEqual(added, 2)
        self.assertEqual({s.name for s in impl.symbols}, {"a", "b"})

    def test_apply_detected_never_overwrites_meaning(self):
        """这是整个功能最重要的不变量: 重新检测不能抹掉用户写的含义。"""
        impl = self._impl()
        impl.set_symbols([Symbol(name="a", kind="parameter", meaning="我写的含义")])
        impl.apply_detected([
            Symbol(name="a", kind="parameter", type="int", detail="第 1 行"),
            Symbol(name="b", kind="parameter"),
        ])
        a = next(s for s in impl.symbols if s.name == "a")
        self.assertEqual(a.meaning, "我写的含义")
        self.assertEqual(a.type, "int")          # 类型会被刷新
        self.assertEqual(a.detail, "第 1 行")

    def test_apply_detected_keeps_vanished_symbols(self):
        impl = self._impl()
        impl.set_symbols([Symbol(name="old", kind="parameter", meaning="保留我")])
        impl.apply_detected([Symbol(name="new", kind="parameter")])
        names = {s.name for s in impl.symbols}
        self.assertEqual(names, {"old", "new"})
        self.assertEqual(
            next(s for s in impl.symbols if s.name == "old").meaning, "保留我"
        )

    def test_apply_detected_skips_locals_shadowing_parameters(self):
        """参数被重新赋值时检测器会同时报同名局部量, 不应重复出现在表里。"""
        impl = self._impl()
        impl.apply_detected([
            Symbol(name="base", kind="parameter", type="int"),
            Symbol(name="base", kind="local", type="int"),
            Symbol(name="other", kind="local"),
        ])
        keys = [(s.name, s.kind) for s in impl.symbols]
        self.assertIn(("base", "parameter"), keys)
        self.assertNotIn(("base", "local"), keys)
        self.assertIn(("other", "local"), keys)

    def test_apply_detected_orders_by_kind(self):
        impl = self._impl()
        impl.apply_detected([
            Symbol(name="l", kind="local"),
            Symbol(name="p", kind="parameter"),
            Symbol(name="r", kind="return"),
            Symbol(name="c", kind="constant"),
        ])
        self.assertEqual([s.kind for s in impl.symbols],
                         ["parameter", "return", "constant", "local"])

    def test_symbols_by_kind(self):
        impl = self._impl()
        impl.set_symbols([
            Symbol(name="a", kind="parameter"),
            Symbol(name="b", kind="parameter"),
            Symbol(name="c", kind="local"),
        ])
        grouped = impl.symbols_by_kind
        self.assertEqual(len(grouped["parameter"]), 2)
        self.assertEqual(len(grouped["local"]), 1)

    def test_get_symbol(self):
        impl = self._impl()
        impl.set_symbols([Symbol(name="a", kind="parameter")])
        self.assertIsNotNone(impl.get_symbol("parameter:a"))
        self.assertIsNone(impl.get_symbol("local:a"))

    def test_roundtrip_and_clone_is_independent(self):
        impl = self._impl()
        impl.set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        restored = FunctionImplementation.from_dict(impl.to_dict())
        self.assertEqual(restored.to_dict(), impl.to_dict())
        clone = impl.clone()
        clone.symbols[0].meaning = "改过了"
        self.assertEqual(impl.symbols[0].meaning, "输入")


class TestFunctionModel(unittest.TestCase):
    def _function(self, **kwargs):
        defaults = dict(
            name="reverse",
            implementations=[make_implementation()],
        )
        defaults.update(kwargs)
        return Function(**defaults)

    def _primary(self, function: Function):
        return function.active_implementations[0]

    def test_kind_interface(self):
        function = self._function()
        self.assertEqual(function.kind, "function")
        self.assertEqual(function.kind_label, "函数体")
        self.assertEqual(function.display_title, "reverse")
        self.assertEqual(function.badge_label, "1 语言")
        self.assertEqual(function.languages, ["python"])
        self.assertEqual(function.language, "python")

    def test_unnamed_function_title(self):
        self.assertEqual(Function().display_title, "未命名函数")

    def test_total_lines(self):
        self.assertEqual(self._function().total_lines, len(SAMPLE.splitlines()))

    def test_prerequisites_belong_to_each_language(self):
        function = self._function()
        function.prerequisites = "所有语言共用"
        function.implementations.append(
            make_implementation(language="go", code=GO_SAMPLE, prerequisites="Go 1.21+")
        )
        self.assertEqual(function.prerequisites, "所有语言共用")
        self.assertEqual(
            [impl.prerequisites for impl in function.active_implementations],
            ["Python 3.10+", "Go 1.21+"],
        )

    def test_badge_counts_languages(self):
        function = self._function()
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        self.assertEqual(function.badge_count, 2)
        self.assertEqual(function.badge_label, "2 语言")
        self.assertEqual(function.languages, ["python", "go"])

    def test_find_by_language(self):
        function = self._function()
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        self.assertEqual(len(function.find_by_language("go")), 1)
        self.assertEqual(len(function.find_by_language("rust")), 0)

    def test_symbols_are_aggregated_across_languages(self):
        function = self._function()
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter")])
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        function.active_implementations[1].set_symbols(
            [Symbol(name="s", kind="parameter"), Symbol(name="sep", kind="parameter")]
        )
        self.assertEqual(function.total_symbols, 3)
        self.assertEqual(len(function.all_symbols), 3)
        self.assertEqual(len(function.required_symbols_missing_meaning), 3)

    def test_meanings_summary_reports_missing(self):
        function = self._function()
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter")])
        self.assertIn("待填写", function.meanings_summary)
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        self.assertIn("已完整", function.meanings_summary)

    def test_deleted_implementation_is_ignored(self):
        function = self._function()
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        function.active_implementations[1].deleted = True
        self.assertEqual(function.languages, ["python"])
        self.assertEqual(function.badge_label, "1 语言")

    def test_search_blob_includes_meanings_and_every_language(self):
        function = self._function()
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter", meaning="待反转的字符串")])
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        blob = function.search_blob
        self.assertIn("待反转", blob)
        self.assertIn("sep", blob)
        self.assertIn("go", blob)

    def test_roundtrip(self):
        function = self._function()
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        function.implementations.append(make_implementation(language="go", code=GO_SAMPLE))
        restored = Function.from_dict(function.to_dict())
        self.assertEqual(restored.to_dict(), function.to_dict())
        self.assertEqual(restored.active_implementations[0].symbols[0].meaning, "输入")
        self.assertEqual(len(restored.active_implementations), 2)

    def test_clone_is_independent(self):
        function = self._function()
        self._primary(function).set_symbols([Symbol(name="s", kind="parameter", meaning="输入")])
        clone = function.clone()
        clone.active_implementations[0].symbols[0].meaning = "改过了"
        self.assertEqual(function.active_implementations[0].symbols[0].meaning, "输入")

    def test_legacy_flat_dict_migrates_to_one_implementation(self):
        """1.2 及以前把单个实现的字段平铺在函数上, 读旧文件必须还能打开。"""
        legacy = {
            "id": "fn_old",
            "name": "legacy",
            "language": "go",
            "code": GO_SAMPLE,
            "signature": "func Reverse(s, sep string) string",
            "prerequisites": "Go 1.21+",
            "symbols": [{"name": "s", "kind": "parameter", "meaning": "输入"}],
            "tags": ["old"],
        }
        function = Function.from_dict(legacy)
        self.assertEqual(len(function.active_implementations), 1)
        impl = function.active_implementations[0]
        self.assertEqual(impl.language, "go")
        self.assertEqual(impl.prerequisites, "Go 1.21+")   # 旧的前置要求归到实现上
        self.assertEqual(function.prerequisites, "")
        self.assertEqual(impl.symbols[0].meaning, "输入")
        self.assertEqual(function.tags, ["old"])

    def test_invalid_status_resets(self):
        self.assertEqual(Function(name="f", status="bogus").status, "planned")


class TestFunctionRepository(unittest.TestCase):
    def setUp(self):
        self.repo = Repository(name="r")
        self.function = self.repo.create_function(
            "reverse", "python", SAMPLE,
            prerequisites="通用要求",
            implementation_prerequisites="Python 3.10+",
        )

    def test_create_detects_symbols(self):
        impl = self.function.active_implementations[0]
        self.assertGreater(len(impl.symbols), 0)
        names = {s.name for s in impl.symbols}
        self.assertIn("s", names)
        self.assertIn("sep", names)

    def test_create_keeps_common_and_per_language_prerequisites(self):
        self.assertEqual(self.function.prerequisites, "通用要求")
        self.assertEqual(self.function.active_implementations[0].prerequisites, "Python 3.10+")

    def test_detection_can_be_skipped(self):
        function = self.repo.create_function("x", "python", SAMPLE, detect=False)
        self.assertEqual(function.total_symbols, 0)

    def test_create_records_history(self):
        actions = [r.action for r in self.repo.revisions(self.function.id, descending=False)]
        self.assertEqual(actions, ["create"])

    def test_add_and_delete_implementation(self):
        go = self.repo.add_function_implementation(
            self.function.id, "go", GO_SAMPLE, prerequisites="Go 1.21+"
        )
        self.assertIn("go", self.repo.require_function(self.function.id).languages)
        self.assertEqual(go.prerequisites, "Go 1.21+")
        self.assertGreater(len(go.symbols), 0)
        self.repo.delete_function_implementation(self.function.id, go.id)
        self.assertEqual(self.repo.require_function(self.function.id).languages, ["python"])
        self.repo.restore_function_implementation(self.function.id, go.id)
        self.assertIn("go", self.repo.require_function(self.function.id).languages)

    def test_update_implementation_keeps_others(self):
        go = self.repo.add_function_implementation(self.function.id, "go", GO_SAMPLE)
        rust = self.repo.add_function_implementation(
            self.function.id, "rust", "pub fn reverse(s: &str) -> String { s.chars().rev().collect() }\n"
        )
        self.repo.update_function_implementation(
            self.function.id, go.id, prerequisites="Go 1.22+"
        )
        function = self.repo.require_function(self.function.id)
        self.assertEqual(function.get_implementation(go.id).prerequisites, "Go 1.22+")
        self.assertEqual(function.get_implementation(rust.id).language, "rust")

    def test_update_produces_single_revision(self):
        before = self.repo.history.count(self.function.id)
        self.repo.update_function(
            self.function.id, name="rev2", prerequisites="通用要求 2",
            symbols=[Symbol(name="s", kind="parameter", meaning="输入")],
        )
        self.assertEqual(self.repo.history.count(self.function.id), before + 1)
        self.assertIn("prerequisites", self.repo.revisions(self.function.id)[0].snapshot)

    def test_update_records_meaning_changes_in_summary(self):
        symbols = [s.clone() for s in self.function.active_implementations[0].symbols]
        for symbol in symbols:
            if symbol.name == "s":
                symbol.meaning = "待反转的字符串"
        self.repo.set_function_symbols(self.function.id, symbols)
        summary = self.repo.revisions(self.function.id)[0].summary
        self.assertIn("含义", summary)

    def test_set_symbols_only_touches_one_implementation(self):
        go = self.repo.add_function_implementation(self.function.id, "go", GO_SAMPLE)
        before_python = [
            (s.name, s.meaning) for s in self.function.active_implementations[0].symbols
        ]
        self.repo.set_function_symbols(
            self.function.id,
            [Symbol(name="s", kind="parameter", meaning="Go 的输入")],
            implementation_id=go.id,
        )
        function = self.repo.require_function(self.function.id)
        self.assertEqual(
            [(s.name, s.meaning) for s in function.active_implementations[0].symbols],
            before_python,
        )
        self.assertEqual(function.get_implementation(go.id).symbols[0].meaning, "Go 的输入")

    def test_redetect_preserves_meanings_and_is_tracked(self):
        symbols = [s.clone() for s in self.function.active_implementations[0].symbols]
        for symbol in symbols:
            symbol.meaning = f"含义-{symbol.name}"
        self.repo.set_function_symbols(self.function.id, symbols)
        before = {
            s.name: s.meaning
            for s in self.repo.require_function(self.function.id).active_implementations[0].symbols
        }

        self.repo.detect_function_symbols(self.function.id)
        after = {
            s.name: s.meaning
            for s in self.repo.require_function(self.function.id).active_implementations[0].symbols
        }
        for name, meaning in before.items():
            self.assertEqual(after[name], meaning, name)

    def test_redetect_single_implementation_scope(self):
        go = self.repo.add_function_implementation(
            self.function.id, "go", GO_SAMPLE, detect=False
        )
        self.assertEqual(self.repo.require_function(self.function.id).get_implementation(go.id).symbols, [])
        added, function = self.repo.detect_function_symbols(self.function.id, go.id)
        self.assertGreater(added, 0)
        self.assertEqual(len(function.get_implementation(go.id).symbols), added)
        self.assertIn("Go", self.repo.revisions(self.function.id)[0].summary)

    def test_diff_shows_meaning_change(self):
        symbols = [s.clone() for s in self.function.active_implementations[0].symbols]
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

    def test_restore_revision_restores_all_languages(self):
        first = self.repo.revisions(self.function.id, descending=False)[0]
        self.repo.add_function_implementation(self.function.id, "go", GO_SAMPLE)
        self.assertEqual(len(self.repo.require_function(self.function.id).active_implementations), 2)
        self.repo.restore_revision(self.function.id, first.id)
        self.assertEqual(len(self.repo.require_function(self.function.id).active_implementations), 1)

    def test_undo_redo(self):
        self.repo.update_function(self.function.id, name="A")
        self.repo.update_function(self.function.id, name="B")
        self.repo.undo(self.function.id)
        self.assertEqual(self.repo.require_function(self.function.id).name, "A")
        self.repo.redo(self.function.id)
        self.assertEqual(self.repo.require_function(self.function.id).name, "B")

    def test_repository_roundtrip_keeps_symbols_and_meanings(self):
        symbols = [s.clone() for s in self.function.active_implementations[0].symbols]
        next(s for s in symbols if s.name == "s").meaning = "输入"
        self.repo.set_function_symbols(self.function.id, symbols)
        self.repo.add_function_implementation(self.function.id, "go", GO_SAMPLE)

        restored = Repository.from_dict(self.repo.to_dict())
        function = restored.require_function(self.function.id)
        original = self.repo.require_function(self.function.id)
        self.assertEqual(function.languages, original.languages)
        for impl, before in zip(function.active_implementations, original.active_implementations):
            self.assertEqual(
                [(s.name, s.kind, s.meaning) for s in impl.symbols],
                [(s.name, s.kind, s.meaning) for s in before.symbols],
            )
            self.assertEqual(impl.prerequisites, before.prerequisites)
        self.assertEqual(restored.history.count(self.function.id),
                         self.repo.history.count(self.function.id))

    def test_language_change_redetects(self):
        self.repo.update_function(
            self.function.id, language="go",
            code='package main\n\nfunc Add(a int, b int) int {\n\treturn a + b\n}\n',
        )
        self.repo.detect_function_symbols(self.function.id)
        names = {s.name for s in self.repo.require_function(self.function.id).all_symbols}
        self.assertIn("a", names)
        self.assertIn("b", names)

    def test_statistics_counts_functions_and_symbols(self):
        stats = self.repo.statistics()
        self.assertEqual(stats["functions"], 1)
        self.assertGreater(stats["function_symbols"], 0)
        self.assertGreater(stats["function_implementations"], 0)
        self.assertEqual(stats["symbols_missing_meaning"],
                         len(self.function.symbols_missing_meaning))

    def test_kind_of_function(self):
        self.assertEqual(self.repo.kind_of(self.function.id), "function")
        self.assertIs(self.repo.get_item(self.function.id), self.function)

    def test_generic_operations_work_for_functions(self):
        self.repo.toggle_favorite_item(self.function.id)
        self.assertTrue(self.repo.require_function(self.function.id).favorite)
        self.repo.set_item_tags(self.function.id, ["a", "b"])
        self.assertEqual(self.repo.require_function(self.function.id).tags, ["a", "b"])
        self.repo.set_item_status(self.function.id, "done")
        self.assertEqual(self.repo.require_function(self.function.id).status, "done")
        clone = self.repo.duplicate_item(self.function.id)
        self.assertEqual(clone.kind, "function")
        self.assertEqual(len(clone.active_implementations), 1)
        self.repo.delete_item(self.function.id)
        self.assertTrue(self.repo.item_is_deleted(self.function.id))
        self.assertEqual(len(self.repo.items("function")), 1)
        self.repo.restore_item(self.function.id)
        self.assertEqual(len(self.repo.items("function")), 2)


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
        self.repo.add_function_implementation(self.function.id, "go", GO_SAMPLE)
        restored = Repository.from_dict(self.repo.to_dict())
        self.assertEqual(len(restored.entries), 1)
        self.assertEqual(len(restored.spaces), 1)
        self.assertEqual(len(restored.functions), 1)
        self.assertEqual(len(restored.items()), 3)
        self.assertEqual(
            restored.functions[self.function.id].languages, ["python", "go"]
        )

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
        # 函数体与模块都有 implementations, 靠实现里的 symbols 区分
        self.assertEqual(
            kind_of_snapshot({"name": "f", "implementations": [{"symbols": []}]}), "function"
        )
        self.assertEqual(
            kind_of_snapshot({"title": "m", "implementations": [{"code": ""}]}), "module"
        )

    def test_history_kind_recorded(self):
        """历史面板要能靠 Revision.kind 正确渲染三类实体。"""
        self.assertEqual(self.repo.revisions(self.function.id)[0].kind, "function")
        self.assertEqual(self.repo.revisions(self.space.id)[0].kind, "space")
        self.assertEqual(self.repo.revisions(self.module.id)[0].kind, "module")


class TestFunctionExport(unittest.TestCase):
    """多语言函数体的导出必须把每种语言都带上。"""

    def setUp(self):
        self.repo = Repository(name="exp")
        self.function = self.repo.create_function("mod_pow", "python", SAMPLE)
        self.repo.set_function_symbols(
            self.function.id,
            [Symbol(name="s", kind="parameter", meaning="输入字符串")],
        )
        self.repo.add_function_implementation(
            self.function.id, "go", GO_SAMPLE, prerequisites="Go 1.21+"
        )

    def test_markdown_lists_every_language(self):
        from codemethod.storage import exporter

        text = exporter.function_to_markdown(
            self.repo.require_function(self.function.id),
            revisions=self.repo.revisions(self.function.id),
        )
        self.assertIn("实现 1: Python", text)
        self.assertIn("实现 2: Go", text)
        self.assertIn("Go 1.21+", text)
        self.assertIn("输入字符串", text)
        self.assertIn("package main", text)

    def test_json_contains_implementations(self):
        import json

        from codemethod.storage import exporter

        data = json.loads(
            exporter.function_to_json(self.repo.require_function(self.function.id))
        )
        self.assertEqual(len(data["implementations"]), 2)
        self.assertEqual(data["implementations"][1]["language"], "go")


if __name__ == "__main__":
    unittest.main()
