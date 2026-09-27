"""变量/符号声明探测测试 (codemethod.core.symbols)。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from codemethod.core.symbols import (  # noqa: E402
    KIND_ORDER,
    DetectedSymbol,
    describe_symbols,
    detect_symbols,
    mask_code,
    merge_symbols,
    supported_languages,
    symbol_key,
)


def names(symbols, kind=None):
    """取出符号名列表, 可按类别过滤。"""
    return [item.name for item in symbols if kind is None or item.kind == kind]


def first(symbols, name):
    """按名字取第一个符号, 取不到返回 None。"""
    for item in symbols:
        if item.name == name:
            return item
    return None


# --------------------------------------------------------------------------------------
# 单语言形参识别: 每种语言一个"小而真实"的函数
# --------------------------------------------------------------------------------------


class TestLanguageParameters(unittest.TestCase):
    def test_python_function_parameters(self):
        code = "def greet(name, greeting='hi'):\n    return greeting\n"
        symbols = detect_symbols("python", code)
        self.assertEqual(names(symbols, "parameter"), ["name", "greeting"])
        self.assertEqual(first(symbols, "greeting").default, "'hi'")

    def test_c_function_parameters(self):
        code = "#include <stdio.h>\nint add(int a, int b) {\n    return a + b;\n}\n"
        symbols = detect_symbols("c", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "a").type, "int")

    def test_cpp_function_parameters(self):
        code = "void log(const std::string &tag, int level, double ratio) {}\n"
        symbols = detect_symbols("cpp", code)
        self.assertEqual(names(symbols, "parameter"), ["tag", "level", "ratio"])
        self.assertIn("string", first(symbols, "tag").type)

    def test_go_function_parameters(self):
        code = 'package main\n\nfunc greet(name string, times int) string {\n\treturn name\n}\n'
        symbols = detect_symbols("go", code)
        self.assertEqual(names(symbols, "parameter"), ["name", "times"])
        self.assertEqual(first(symbols, "times").type, "int")

    def test_java_method_parameters(self):
        code = "public class A {\n    public int add(int a, int b) {\n        return a + b;\n    }\n}\n"
        symbols = detect_symbols("java", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "b").type, "int")

    def test_php_function_parameters(self):
        code = "<?php\nfunction add($a, $b = 2) {\n    return $a + $b;\n}\n"
        symbols = detect_symbols("php", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "b").default, "2")

    def test_rust_function_parameters(self):
        code = "fn add(a: u32, b: u32) -> u32 {\n    a + b\n}\n"
        symbols = detect_symbols("rust", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "a").type, "u32")

    def test_javascript_function_parameters(self):
        code = "function add(a, b = 1) {\n    return a + b;\n}\n"
        symbols = detect_symbols("javascript", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "b").default, "1")

    def test_typescript_function_parameters(self):
        code = "function add(a: number, b: number = 1): number {\n    return a + b;\n}\n"
        symbols = detect_symbols("typescript", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "a").type, "number")
        self.assertEqual(first(symbols, "return").type, "number")

    def test_csharp_method_parameters(self):
        code = "class A {\n    public int Add(int a, string label) {\n        return a;\n    }\n}\n"
        symbols = detect_symbols("csharp", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "label"])
        self.assertEqual(first(symbols, "label").type, "string")

    def test_kotlin_function_parameters(self):
        code = "fun add(a: Int, b: Int): Int {\n    return a + b\n}\n"
        symbols = detect_symbols("kotlin", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "b").type, "Int")

    def test_swift_function_parameters(self):
        code = "func add(a: Int, b: Int) -> Int {\n    return a + b\n}\n"
        symbols = detect_symbols("swift", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "return").type, "Int")

    def test_ruby_method_parameters(self):
        code = "def add(a, b = 1)\n  a + b\nend\n"
        symbols = detect_symbols("ruby", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])
        self.assertEqual(first(symbols, "b").default, "1")

    def test_lua_function_parameters(self):
        code = "function add(a, b)\n  return a + b\nend\n"
        symbols = detect_symbols("lua", code)
        self.assertEqual(names(symbols, "parameter"), ["a", "b"])

    def test_bash_positional_parameters(self):
        code = '#!/usr/bin/env bash\ngreet() {\n  echo "hello $1 and $2"\n}\n'
        symbols = detect_symbols("bash", code)
        self.assertEqual(names(symbols, "parameter"), ["$1", "$2"])

    def test_sql_declare_and_alias(self):
        code = "DECLARE total INT;\nSELECT COUNT(*) AS item_count FROM items;\n"
        symbols = detect_symbols("sql", code)
        self.assertIn("total", names(symbols))
        self.assertIn("item_count", names(symbols))


# --------------------------------------------------------------------------------------
# Python 细节
# --------------------------------------------------------------------------------------


class TestPythonDetails(unittest.TestCase):
    def test_annotated_parameters_and_defaults(self):
        code = 'def f(a: int, b: str = "x") -> bool:\n    return True\n'
        symbols = detect_symbols("python", code)
        self.assertEqual(first(symbols, "a").type, "int")
        self.assertEqual(first(symbols, "b").type, "str")
        self.assertEqual(first(symbols, "b").default, '"x"')
        self.assertEqual(first(symbols, "return").type, "bool")

    def test_varargs_and_kwargs(self):
        code = "def f(*args, **kwargs):\n    pass\n"
        symbols = detect_symbols("python", code)
        self.assertEqual(names(symbols, "parameter"), ["args", "kwargs"])

    def test_self_and_cls_are_skipped(self):
        code = "class A:\n    def m(self, cls, value):\n        return value\n"
        symbols = detect_symbols("python", code)
        self.assertEqual(names(symbols, "parameter"), ["value"])

    def test_destructuring_assignment_is_marked(self):
        code = "def f():\n    first, second = compute()\n    return first\n"
        symbols = detect_symbols("python", code)
        self.assertIn("(解构)", first(symbols, "first").detail)
        self.assertIn("(解构)", first(symbols, "second").detail)

    def test_self_attribute_is_field(self):
        code = "class A:\n    def __init__(self):\n        self.name = 'x'\n"
        symbols = detect_symbols("python", code)
        field = first(symbols, "name")
        self.assertIsNotNone(field)
        self.assertEqual(field.kind, "field")

    def test_global_statement(self):
        code = "def f():\n    global counter\n    counter = 1\n"
        symbols = detect_symbols("python", code)
        self.assertIn("counter", names(symbols, "global"))

    def test_module_level_upper_case_is_constant(self):
        code = "MAX_SIZE = 100\n\ndef f():\n    return MAX_SIZE\n"
        symbols = detect_symbols("python", code)
        constant = first(symbols, "MAX_SIZE")
        self.assertIsNotNone(constant)
        self.assertEqual(constant.kind, "constant")

    def test_lowercase_local_is_not_constant(self):
        code = "def f():\n    total = 1\n    return total\n"
        symbols = detect_symbols("python", code)
        self.assertEqual(first(symbols, "total").kind, "local")

    def test_function_name_filter_limits_results(self):
        code = "def one(a):\n    return a\n\n\ndef two(b):\n    return b\n"
        self.assertEqual(names(detect_symbols("python", code, "one"), "parameter"), ["a"])
        self.assertEqual(names(detect_symbols("python", code, "two"), "parameter"), ["b"])


# --------------------------------------------------------------------------------------
# Rust / Go 细节
# --------------------------------------------------------------------------------------


class TestRustDetails(unittest.TestCase):
    def test_let_mut_and_typed_let(self):
        code = "fn f() {\n    let mut total = 1;\n    let name: String = g();\n}\n"
        symbols = detect_symbols("rust", code)
        self.assertEqual(first(symbols, "total").kind, "local")
        self.assertEqual(first(symbols, "name").type, "String")

    def test_arrow_return_type(self):
        code = "fn f(a: u32) -> Result<u32, String> {\n    Ok(a)\n}\n"
        symbols = detect_symbols("rust", code)
        self.assertEqual(first(symbols, "return").type, "Result<u32, String>")

    def test_struct_fields(self):
        code = "struct Point {\n    x: i32,\n    y: i32,\n}\n"
        symbols = detect_symbols("rust", code)
        self.assertEqual(names(symbols, "field"), ["x", "y"])
        self.assertEqual(first(symbols, "y").type, "i32")

    def test_const_and_static(self):
        code = "const LIMIT: u32 = 10;\nstatic NAME: &str = \"x\";\n"
        symbols = detect_symbols("rust", code)
        self.assertEqual(first(symbols, "LIMIT").kind, "constant")
        self.assertEqual(first(symbols, "NAME").kind, "constant")

    def test_reference_parameter_type(self):
        code = "fn f(text: &str) -> usize {\n    0\n}\n"
        self.assertEqual(first(detect_symbols("rust", code), "text").type, "&str")


class TestGoDetails(unittest.TestCase):
    def test_named_results(self):
        code = "func div(a int) (result int, err error) {\n\treturn 0, nil\n}\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(names(symbols, "return"), ["result", "err"])
        self.assertEqual(first(symbols, "err").type, "error")

    def test_short_declaration(self):
        code = "func f() {\n\tcount := 0\n\t_ = count\n}\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(first(symbols, "count").kind, "local")

    def test_var_declaration_with_type(self):
        code = "func f() {\n\tvar total int = 3\n\t_ = total\n}\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(first(symbols, "total").type, "int")

    def test_const_block_with_iota(self):
        code = "const (\n\tA = iota\n\tB = iota\n)\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(first(symbols, "A").kind, "constant")
        self.assertEqual(first(symbols, "B").kind, "constant")

    def test_struct_fields(self):
        code = "type Server struct {\n\tHost string\n\tPort int\n}\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(names(symbols, "field"), ["Host", "Port"])


# --------------------------------------------------------------------------------------
# 注释与字符串免疫 —— 最重要的正确性属性
# --------------------------------------------------------------------------------------


class TestCommentAndStringImmunity(unittest.TestCase):
    def test_python_comment_is_ignored(self):
        code = "ghostvar = 1\n# phantom = 2\n"
        symbols = detect_symbols("python", code)
        self.assertIn("ghostvar", names(symbols))
        self.assertNotIn("phantom", names(symbols))

    def test_python_string_contents_are_ignored(self):
        code = 'text = "phantom_name"\nother = 1\n'
        symbols = detect_symbols("python", code)
        self.assertNotIn("phantom_name", names(symbols))

    def test_python_triple_quoted_string_is_ignored(self):
        code = 'DOC = """\nphantom = 1\n"""\nreal = 2\n'
        symbols = detect_symbols("python", code)
        self.assertNotIn("phantom", names(symbols))

    def test_constant_after_triple_quoted_string_is_detected(self):
        """三引号掩码不能把后面的代码一起吃掉。"""
        code = 'DOC = """\ntext\n"""\nREAL_CONST = 2\n'
        symbols = detect_symbols("python", code)
        self.assertIn("REAL_CONST", names(symbols, "constant"))

    def test_code_after_triple_quoted_string_in_function_is_detected(self):
        code = 'def f():\n    doc = """\ntext\n"""\n    value = 1\n    return value\n'
        symbols = detect_symbols("python", code)
        self.assertIn("value", names(symbols, "local"))

    def test_python_comment_after_string_is_ignored(self):
        code = 'name = "x"  # phantom = 1\n'
        symbols = detect_symbols("python", code)
        self.assertNotIn("phantom", names(symbols))

    def test_c_line_comment_is_ignored(self):
        code = "int x = 1; // int phantom = 2;\nint y = 2;\n"
        symbols = detect_symbols("c", code)
        self.assertIn("x", names(symbols))
        self.assertIn("y", names(symbols))
        self.assertNotIn("phantom", names(symbols))

    def test_c_block_comment_is_ignored(self):
        code = "/*\nint phantom = 1;\n*/\nint real = 2;\n"
        symbols = detect_symbols("c", code)
        self.assertNotIn("phantom", names(symbols))
        self.assertIn("real", names(symbols))

    def test_c_string_content_is_ignored(self):
        code = 'const char *p = "int phantom = 1;";\n'
        symbols = detect_symbols("c", code)
        self.assertNotIn("phantom", names(symbols))
        self.assertIn("p", names(symbols))

    def test_rust_comment_is_ignored(self):
        code = "// const PHANTOM: u32 = 1;\nconst REAL: u32 = 2;\n"
        symbols = detect_symbols("rust", code)
        self.assertNotIn("PHANTOM", names(symbols))
        self.assertIn("REAL", names(symbols))

    def test_rust_return_keyword_not_swallowed_by_raw_prefix(self):
        """标识符 r 不能被当成 raw string 前缀, 否则 return 之后整段代码都会被吞掉。"""
        code = "fn f() -> u32 {\n    let value = 1;\n    return value;\n}\n"
        symbols = detect_symbols("rust", code)
        self.assertIn("value", names(symbols))

    def test_go_comment_is_ignored(self):
        code = "// func phantom(a int) {}\nfunc real(b int) int {\n\treturn b\n}\n"
        symbols = detect_symbols("go", code)
        self.assertEqual(names(symbols, "parameter"), ["b"])

    def test_bash_comment_is_ignored(self):
        code = "# PHANTOM=1\nREAL=2\n"
        symbols = detect_symbols("bash", code)
        self.assertNotIn("PHANTOM", names(symbols))
        self.assertIn("REAL", names(symbols))

    def test_sql_line_comment_is_ignored(self):
        code = "-- DECLARE phantom INT;\nDECLARE real INT;\n"
        symbols = detect_symbols("sql", code)
        self.assertNotIn("phantom", names(symbols))
        self.assertIn("real", names(symbols))

    def test_javascript_string_is_ignored(self):
        code = 'const label = "phantom_var";\n'
        symbols = detect_symbols("javascript", code)
        self.assertNotIn("phantom_var", names(symbols))
        self.assertIn("label", names(symbols))

    def test_mask_preserves_offsets_and_newlines(self):
        code = 'x = 1\nname = "hi"\n# y = 2\n'
        masked = mask_code("python", code)
        self.assertEqual(len(masked), len(code))
        self.assertEqual(masked.count("\n"), code.count("\n"))
        self.assertTrue(masked.startswith("x = 1\n"))
        self.assertNotIn("hi", masked)
        self.assertNotIn("y = 2", masked)

    def test_mask_of_empty_and_plaintext(self):
        self.assertEqual(mask_code("python", ""), "")
        self.assertEqual(mask_code("plaintext", "abc"), "abc")


# --------------------------------------------------------------------------------------
# merge_symbols / symbol_key
# --------------------------------------------------------------------------------------


class TestMergeSymbols(unittest.TestCase):
    def test_meaning_is_preserved_across_redetect(self):
        old = [DetectedSymbol("a", "parameter", type="", meaning="输入列表")]
        fresh = detect_symbols("python", "def f(a: int):\n    return a\n")
        merged = merge_symbols(old, fresh)
        self.assertEqual(first(merged, "a").meaning, "输入列表")

    def test_type_detail_default_refreshed_from_detection(self):
        old = [DetectedSymbol("a", "parameter", type="", detail="旧位置", default="")]
        fresh = [DetectedSymbol("a", "parameter", type="int", detail="函数签名", default="3")]
        merged = merge_symbols(old, fresh)
        self.assertEqual(first(merged, "a").type, "int")
        self.assertEqual(first(merged, "a").detail, "函数签名")
        self.assertEqual(first(merged, "a").default, "3")

    def test_vanished_symbol_is_kept_but_detail_cleared(self):
        old = [DetectedSymbol("gone", "local", type="int", detail="第 9 行", meaning="我加的")]
        merged = merge_symbols(old, [])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].name, "gone")
        self.assertEqual(merged[0].detail, "")
        self.assertEqual(merged[0].meaning, "我加的")
        self.assertEqual(merged[0].type, "int")

    def test_new_symbols_are_appended_in_order(self):
        old = [DetectedSymbol("a", "parameter")]
        fresh = [DetectedSymbol("a", "parameter"), DetectedSymbol("b", "local"), DetectedSymbol("c", "local")]
        merged = merge_symbols(old, fresh)
        self.assertEqual([item.name for item in merged], ["a", "b", "c"])

    def test_existing_order_is_kept(self):
        old = [DetectedSymbol("z", "local"), DetectedSymbol("a", "parameter")]
        fresh = [DetectedSymbol("a", "parameter"), DetectedSymbol("z", "local")]
        merged = merge_symbols(old, fresh)
        self.assertEqual([item.name for item in merged], ["z", "a"])

    def test_never_duplicates_same_key(self):
        old = [DetectedSymbol("a", "parameter"), DetectedSymbol("a", "parameter")]
        fresh = [DetectedSymbol("a", "parameter"), DetectedSymbol("a", "parameter")]
        merged = merge_symbols(old, fresh)
        self.assertEqual(len(merged), 1)

    def test_same_name_different_kind_is_kept_separately(self):
        old = [DetectedSymbol("count", "field"), DetectedSymbol("count", "local")]
        merged = merge_symbols(old, [])
        self.assertEqual(len(merged), 2)

    def test_merge_tolerates_garbage(self):
        self.assertEqual(merge_symbols(None, None), [])
        merged = merge_symbols([DetectedSymbol("a", "local")], ["not a symbol"])
        self.assertEqual(len(merged), 1)


class TestSymbolKey(unittest.TestCase):
    def test_stable_and_case_insensitive_on_name(self):
        self.assertEqual(symbol_key("python", "Value", "local"), symbol_key("python", "value", "local"))

    def test_kind_matters(self):
        self.assertNotEqual(symbol_key("python", "x", "local"), symbol_key("python", "x", "field"))

    def test_language_aliases_normalise(self):
        self.assertEqual(symbol_key("py", "x", "local"), symbol_key("python", "x", "local"))
        self.assertEqual(symbol_key("c++", "x", "local"), symbol_key("cpp", "x", "local"))

    def test_unknown_language_degrades_to_wildcard(self):
        self.assertEqual(symbol_key("brainfuck", "x", "local"), symbol_key("", "x", "local"))


# --------------------------------------------------------------------------------------
# 健壮性与辅助 API
# --------------------------------------------------------------------------------------


class TestRobustness(unittest.TestCase):
    def test_empty_input_returns_empty(self):
        for language in supported_languages():
            self.assertEqual(detect_symbols(language, ""), [])
            self.assertEqual(detect_symbols(language, "   \n\n  "), [])

    def test_malformed_input_does_not_raise(self):
        broken = [
            "def f(:::",
            "int x = (((;",
            "{{{[[[(((",
            "function f(a, b",
            "class A { struct B {",
            "'''",
            '"unterminated',
            "/* unterminated comment",
            "fn f(a: -> {",
        ]
        for language in supported_languages() + ["plaintext", "brainfuck"]:
            for snippet in broken:
                self.assertIsInstance(detect_symbols(language, snippet), list)

    def test_binary_ish_input_does_not_raise(self):
        blob = "\x00\x01\xff\xfe\x00 int x = 1;\x00"
        for language in ("c", "python", "go", "plaintext"):
            self.assertIsInstance(detect_symbols(language, blob), list)

    def test_non_string_input_does_not_raise(self):
        self.assertEqual(detect_symbols("python", None), [])
        self.assertIsInstance(detect_symbols("python", 12345), list)

    def test_unknown_language_falls_back_to_generic(self):
        symbols = detect_symbols("brainfuck", "let x = 1\n")
        self.assertIsInstance(symbols, list)

    def test_result_order_is_deterministic(self):
        code = "MAX = 1\n\ndef f(a):\n    local = a\n    return local\n"
        first_run = detect_symbols("python", code)
        second_run = detect_symbols("python", code)
        self.assertEqual(
            [(item.name, item.kind, item.detail) for item in first_run],
            [(item.name, item.kind, item.detail) for item in second_run],
        )

    def test_kind_order_parameters_before_locals(self):
        code = "def f(a):\n    local = a\n    return local\n"
        kinds = [item.kind for item in detect_symbols("python", code)]
        self.assertLess(kinds.index("parameter"), kinds.index("local"))
        self.assertLess(kinds.index("return"), kinds.index("local"))

    def test_no_duplicate_keys_in_result(self):
        code = "def f(a, b):\n    x = a\n    x = b\n    return x\n"
        symbols = detect_symbols("python", code)
        keys = [symbol_key("python", item.name, item.kind) for item in symbols]
        self.assertEqual(len(keys), len(set(keys)))


class TestSupportedLanguagesAndDescribe(unittest.TestCase):
    REQUIRED = (
        "python", "c", "cpp", "go", "java", "php", "rust", "javascript", "typescript",
        "csharp", "kotlin", "swift", "ruby", "lua", "bash", "sql",
    )

    def test_supported_languages_covers_required(self):
        supported = supported_languages()
        for language in self.REQUIRED:
            self.assertIn(language, supported, f"缺少语言 {language}")

    def test_supported_languages_has_no_duplicates(self):
        supported = supported_languages()
        self.assertEqual(len(supported), len(set(supported)))

    def test_describe_symbols_shape(self):
        code = "def f(a: int, b: int) -> int:\n    total = a + b\n    return total\n"
        described = describe_symbols("python", code, "f")
        self.assertEqual(described["language"], "python")
        self.assertEqual(described["function"], "f")
        self.assertEqual(set(described["counts"]), set(KIND_ORDER))
        self.assertEqual(described["counts"]["parameter"], 2)
        self.assertEqual(described["counts"]["local"], 1)
        self.assertEqual(len(described["symbols"]), len(detect_symbols("python", code, "f")))
        self.assertIsInstance(described["symbols"][0], dict)

    def test_describe_counts_match_symbols(self):
        code = "MAX = 1\n\nclass A:\n    field: int = 0\n\n    def m(self, x):\n        global g\n        return x\n"
        described = describe_symbols("python", code)
        counted = {}
        for item in described["symbols"]:
            counted[item["kind"]] = counted.get(item["kind"], 0) + 1
        for kind, value in described["counts"].items():
            self.assertEqual(value, counted.get(kind, 0), kind)

    def test_describe_empty_code(self):
        described = describe_symbols("python", "")
        self.assertEqual(described["symbols"], [])
        self.assertEqual(sum(described["counts"].values()), 0)

    def test_describe_normalises_language(self):
        self.assertEqual(describe_symbols("py", "x = 1")["language"], "python")


if __name__ == "__main__":
    unittest.main()
