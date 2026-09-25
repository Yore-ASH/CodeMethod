"""编程语言注册表.

每一种语言用一份 :class:`LanguageSpec` 描述, 语法高亮器 (:mod:`codemethod.ui.highlighter`)
与界面上的语言下拉框都由这张表驱动。新增语言只需要在这里追加一条记录,
无需改动高亮算法本身。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple


def _words(text: str) -> Tuple[str, ...]:
    """把以空白分隔的关键字字符串切成元组。"""
    return tuple(w for w in text.split() if w)


@dataclass(frozen=True)
class LanguageSpec:
    """一种语言的词法/元数据描述。"""

    id: str
    name: str
    extensions: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    # --- 词法 ---
    keywords: Tuple[str, ...] = ()
    control_keywords: Tuple[str, ...] = ()
    types: Tuple[str, ...] = ()
    constants: Tuple[str, ...] = ()
    builtins: Tuple[str, ...] = ()
    line_comment: Optional[str] = "//"
    extra_line_comments: Tuple[str, ...] = ()
    block_comment: Optional[Tuple[str, str]] = ("/*", "*/")
    nested_block_comment: bool = False
    string_delimiters: Tuple[str, ...] = ('"',)
    triple_string_delimiters: Tuple[str, ...] = ()
    char_delimiter: Optional[str] = None
    raw_string_prefixes: Tuple[str, ...] = ()
    preprocessor: Optional[str] = None
    annotation: Optional[str] = None
    variable_prefix: Optional[str] = None
    function_call_highlight: bool = True
    case_sensitive: bool = True
    # --- 展示 ---
    monaco_id: str = ""
    color: str = "#4EC9B0"
    extra_keywords: Tuple[str, ...] = field(default=(), repr=False)

    # ---- 便捷访问 ----
    @property
    def all_keywords(self) -> Tuple[str, ...]:
        return tuple(dict.fromkeys(self.keywords + self.control_keywords + self.extra_keywords))

    @property
    def all_line_comments(self) -> Tuple[str, ...]:
        """全部行注释标记 (例如 PHP 同时支持 ``//`` 与 ``#``)。

        按长度降序, 保证 ``//`` 之类的多字符标记优先于单字符标记。
        """
        markers = []
        if self.line_comment:
            markers.append(self.line_comment)
        markers.extend(self.extra_line_comments)
        return tuple(sorted(dict.fromkeys(markers), key=len, reverse=True))

    @property
    def first_line_comment(self) -> Optional[str]:
        """最长的行注释标记 (用于展示与文档)。"""
        markers = self.all_line_comments
        return markers[0] if markers else None

    def matches_extension(self, ext: str) -> bool:
        ext = ext.lower().lstrip(".")
        return ext in self.extensions


# --------------------------------------------------------------------------------------
# 语言定义
# --------------------------------------------------------------------------------------

_C = LanguageSpec(
    id="c",
    name="C",
    extensions=("c", "h"),
    aliases=("c99", "c11", "c17", "gnu-c", "c-lang"),
    keywords=_words(
        "auto extern register static typedef sizeof volatile const inline restrict "
        "struct union enum goto return"
    ),
    control_keywords=_words("if else for while do switch case default break continue"),
    types=_words(
        "void char short int long float double signed unsigned _Bool bool size_t ssize_t "
        "int8_t int16_t int32_t int64_t uint8_t uint16_t uint32_t uint64_t ptrdiff_t "
        "FILE va_list wchar_t"
    ),
    constants=_words("NULL true false EOF"),
    builtins=_words(
        "printf fprintf sprintf snprintf scanf fscanf malloc calloc realloc free memcpy "
        "memset memmove strlen strcmp strcpy strcat fopen fclose fread fwrite fgets "
        "exit abort assert"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    preprocessor="#",
    monaco_id="c",
    color="#5B9BD5",
)

_CPP = LanguageSpec(
    id="cpp",
    name="C++",
    extensions=("cpp", "cc", "cxx", "c++", "hpp", "hh", "hxx", "inl", "ipp", "tpp"),
    aliases=("c++", "cplusplus", "cpp17", "cpp20", "cpp23", "gnu++"),
    keywords=_words(
        "alignas alignof asm auto concept const consteval constexpr constinit const_cast "
        "decltype delete dynamic_cast explicit export extern friend inline mutable "
        "namespace new noexcept operator override final private protected public "
        "register reinterpret_cast requires sizeof static static_assert static_cast "
        "struct class template this thread_local throw try catch typedef typeid typename "
        "union using virtual volatile co_await co_return co_yield module import "
        "public private protected"
    ),
    control_keywords=_words(
        "if else for while do switch case default break continue goto return"
    ),
    types=_words(
        "void bool char short int long float double signed unsigned wchar_t char8_t "
        "char16_t char32_t size_t ptrdiff_t int8_t int16_t int32_t int64_t uint8_t "
        "uint16_t uint32_t uint64_t string wstring vector array deque list forward_list "
        "map set unordered_map unordered_set multimap multiset pair tuple optional "
        "variant any function shared_ptr unique_ptr weak_ptr string_view span "
        "atomic mutex thread condition_variable future promise ostream istream "
        "stringstream fstream ifstream ofstream"
    ),
    constants=_words("nullptr NULL true false EOF std numeric_limits"),
    builtins=_words(
        "std cout cin cerr endl printf malloc free memcpy std move forward make_shared "
        "make_unique static_cast dynamic_cast"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    raw_string_prefixes=("R", "u8", "u", "U", "L"),
    preprocessor="#",
    annotation="[[",
    monaco_id="cpp",
    color="#4EC9B0",
)

_GO = LanguageSpec(
    id="go",
    name="Go",
    extensions=("go",),
    aliases=("golang",),
    keywords=_words(
        "chan const defer fallthrough func go import interface map package range select "
        "struct type var"
    ),
    control_keywords=_words("break case continue default else for goto if return switch"),
    types=_words(
        "bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 "
        "rune string uint uint8 uint16 uint32 uint64 uintptr any comparable"
    ),
    constants=_words("true false iota nil"),
    builtins=_words(
        "append cap close complex copy delete imag len make new panic print println real "
        "recover min max clear"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    raw_string_prefixes=("`",),
    monaco_id="go",
    color="#00ADD8",
)

_JAVA = LanguageSpec(
    id="java",
    name="Java",
    extensions=("java",),
    aliases=("jdk", "java17", "java21"),
    keywords=_words(
        "abstract assert class extends final implements import instanceof interface "
        "native new package private protected public static strictfp super synchronized "
        "this throw throws transient try volatile record sealed permits yield var "
        "enum const goto"
    ),
    control_keywords=_words(
        "break case catch continue default do else finally for if return switch while"
    ),
    types=_words(
        "boolean byte char double float int long short void String Object Integer Long "
        "Double Float Boolean Character Byte Short List ArrayList Map HashMap Set HashSet "
        "Collection Optional Stream StringBuilder Thread Runnable Exception RuntimeException "
        "IllegalArgumentException BigInteger BigDecimal LocalDate LocalDateTime Duration"
    ),
    constants=_words("true false null"),
    builtins=_words(
        "System out println printf valueOf equals hashCode toString length size get set "
        "add remove contains stream of"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    annotation="@",
    monaco_id="java",
    color="#E76F00",
)

_PYTHON = LanguageSpec(
    id="python",
    name="Python",
    extensions=("py", "pyw", "pyi", "pyx"),
    aliases=("py", "python3", "python2", "cpython", "py3"),
    keywords=_words(
        "and as assert async await class def del elif else except finally from global "
        "import in is lambda nonlocal not or pass raise return walrus try while with yield "
        "match case type"
    ),
    control_keywords=_words("if for while break continue return"),
    types=_words(
        "bool bytes bytearray complex dict float frozenset int list object set str tuple "
        "type Any Optional Union List Dict Tuple Set Callable Iterable Iterator Sequence "
        "Mapping MutableMapping Path datetime timedelta Decimal Enum IntEnum dataclass "
        "NamedTuple Protocol TypeVar Generic"
    ),
    constants=_words("True False None NotImplemented Ellipsis __name__ __main__"),
    builtins=_words(
        "abs all any ascii bin bool breakpoint bytes callable chr classmethod compile "
        "complex delattr dir divmod enumerate eval exec filter format getattr globals "
        "hasattr hash help hex id input isinstance issubclass iter len locals map max "
        "memoryview min next object oct open ord pow print property range repr reversed "
        "round setattr slice sorted staticmethod sum super vars zip self cls"
    ),
    line_comment="#",
    block_comment=None,
    string_delimiters=('"', "'"),
    triple_string_delimiters=('"""', "'''"),
    raw_string_prefixes=("r", "b", "f", "u", "rb", "br", "fr", "rf"),
    annotation="@",
    monaco_id="python",
    color="#3572A5",
)

_PHP = LanguageSpec(
    id="php",
    name="PHP",
    extensions=("php", "php3", "php4", "php5", "phtml", "inc"),
    aliases=("php7", "php8", "phtml"),
    keywords=_words(
        "abstract and array as break callable case catch class clone const continue "
        "declare default do echo else elseif empty enddeclare endfor endforeach endif "
        "endswitch endwhile enum extends final finally fn for foreach function global "
        "goto if implements include include_once instanceof insteadof interface isset "
        "list match namespace new or print private protected public readonly require "
        "require_once return static switch throw trait try unset use var while xor yield "
        "die eval exit"
    ),
    control_keywords=_words("if else elseif for foreach while do switch case default break continue return match"),
    types=_words(
        "bool boolean int integer float double string array object mixed void never "
        "iterable self parent static null false true callable"
    ),
    constants=_words("true false null TRUE FALSE NULL PHP_EOL PHP_INT_MAX __DIR__ __FILE__ __LINE__"),
    builtins=_words(
        "strlen count array_map array_filter array_merge implode explode sprintf printf "
        "var_dump print_r json_encode json_decode preg_match preg_replace substr str_replace "
        "trim strtolower strtoupper in_array array_keys array_values isset unset empty"
    ),
    line_comment="//",
    extra_line_comments=("#",),
    block_comment=("/*", "*/"),
    string_delimiters=('"', "'"),
    raw_string_prefixes=("<<<",),
    variable_prefix="$",
    annotation="#[",
    monaco_id="php",
    color="#8993BE",
)

_RUST = LanguageSpec(
    id="rust",
    name="Rust",
    extensions=("rs",),
    aliases=("rustlang", "rust2021", "rust2024"),
    keywords=_words(
        "as async await const crate dyn enum extern fn impl in let loop macro match mod "
        "move mut pub ref self Self static struct super trait type unsafe use where "
        "union box try"
    ),
    control_keywords=_words("break continue else for if return while match"),
    types=_words(
        "bool char f32 f64 i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize str String "
        "Vec Option Result Box Rc Arc RefCell Cell Mutex RwLock HashMap HashSet BTreeMap "
        "BTreeSet Cow Pin Duration Instant Path PathBuf io fmt collections thread sync "
        "any mem ptr"
    ),
    constants=_words("true false None Some Ok Err"),
    builtins=_words(
        "println print eprintln eprint format vec panic assert assert_eq assert_ne "
        "write writeln todo unimplemented unreachable dbg include_str include_bytes "
        "drop clone into from default"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    nested_block_comment=True,
    string_delimiters=('"',),
    char_delimiter="'",
    raw_string_prefixes=("r", "b", "br"),
    annotation="#[",
    monaco_id="rust",
    color="#DEA584",
)

_JAVASCRIPT = LanguageSpec(
    id="javascript",
    name="JavaScript",
    extensions=("js", "mjs", "cjs", "jsx"),
    aliases=("js", "ecmascript", "es6", "node", "nodejs"),
    keywords=_words(
        "async await break case catch class const continue debugger default delete do "
        "else export extends finally for function get if import in instanceof let new "
        "of return set static super switch this throw try typeof var void while with yield "
        "null true false undefined NaN Infinity"
    ),
    control_keywords=_words("if else for while do switch case default break continue return"),
    types=_words(
        "Array Object String Number Boolean Symbol BigInt Function Promise Map Set WeakMap "
        "WeakSet Date RegExp Error TypeError JSON Math console document window"
    ),
    constants=_words("true false null undefined NaN Infinity"),
    builtins=_words(
        "console log error warn require module exports parseInt parseFloat isNaN "
        "setTimeout setInterval fetch"
    ),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"', "'", "`"),
    monaco_id="javascript",
    color="#F1E05A",
)

_TYPESCRIPT = LanguageSpec(
    id="typescript",
    name="TypeScript",
    extensions=("ts", "tsx", "mts", "cts"),
    aliases=("ts",),
    keywords=_words(
        "abstract any as asserts async await declare enum export extends implements "
        "infer interface is keyof module namespace never readonly satisfies type unique "
        "unknown const let var function class return if else for while do switch case "
        "break continue new delete typeof instanceof this super import from default public "
        "private protected static override get set"
    ),
    control_keywords=_words("if else for while do switch case default break continue return"),
    types=_words(
        "string number boolean object symbol bigint void never any unknown null undefined "
        "Array Promise Record Partial Required Readonly Pick Omit Map Set Date Error"
    ),
    constants=_words("true false null undefined NaN Infinity"),
    builtins=_words("console log require parseInt parseFloat JSON Promise"),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"', "'", "`"),
    annotation="@",
    monaco_id="typescript",
    color="#3178C6",
)

_CSHARP = LanguageSpec(
    id="csharp",
    name="C#",
    extensions=("cs", "csx"),
    aliases=("c#", "dotnet", "csharp-lang"),
    keywords=_words(
        "abstract as base break case catch checked class const continue default delegate "
        "do else enum event explicit extern finally fixed for foreach goto if implicit in "
        "interface internal is lock namespace new operator out override params private "
        "protected public readonly ref return sealed sizeof stackalloc static struct switch "
        "this throw try typeof unchecked unsafe using virtual volatile while yield record "
        "init required file scoped global var dynamic async await nameof when where"
    ),
    control_keywords=_words("if else for foreach while do switch case default break continue return try catch finally throw"),
    types=_words(
        "bool byte char decimal double float int long sbyte short string uint ulong ushort "
        "object void dynamic Task List Dictionary IEnumerable IList IDictionary Span Memory "
        "Guid DateTime TimeSpan Exception Action Func Nullable"
    ),
    constants=_words("true false null"),
    builtins=_words("Console WriteLine ReadLine ToString Equals GetHashCode"),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    raw_string_prefixes=("@", "$"),
    annotation="#[",
    monaco_id="csharp",
    color="#68217A",
)

_SQL = LanguageSpec(
    id="sql",
    name="SQL",
    extensions=("sql", "ddl", "dml"),
    aliases=("mysql", "postgres", "postgresql", "sqlite", "tsql", "plsql", "ansi-sql"),
    keywords=_words(
        "add all alter and any as asc backup between by case check column constraint create "
        "database default delete desc distinct drop exec exists foreign from full group "
        "having in index inner insert into is join key left like limit not null offset on "
        "or order outer primary procedure right rownum select set table top truncate union "
        "unique update values view where with distinct grant revoke begin commit rollback "
        "transaction if else end declare returning cascade"
    ),
    control_keywords=_words("select insert update delete from where join on group by order having"),
    types=_words(
        "int integer bigint smallint tinyint decimal numeric float real double char varchar "
        "nvarchar text blob clob date datetime timestamp time boolean json uuid serial "
        "primary_key foreign_key"
    ),
    constants=_words("null true false current_date current_timestamp"),
    builtins=_words("count sum avg min max coalesce nullif cast convert now length upper lower trim"),
    line_comment="--",
    block_comment=("/*", "*/"),
    string_delimiters=("'", '"', "`"),
    case_sensitive=False,
    monaco_id="sql",
    color="#E38C00",
)

_BASH = LanguageSpec(
    id="bash",
    name="Bash / Shell",
    extensions=("sh", "bash", "zsh", "ksh"),
    aliases=("shell", "sh", "zsh", "posix-sh"),
    keywords=_words(
        "if then else elif fi for while until do done case esac function select in return "
        "break continue local export readonly declare typeset unset shift source alias "
        "eval exec trap set unset times"
    ),
    control_keywords=_words("if then else elif fi for while until do done case esac in return"),
    types=(),
    constants=_words("true false"),
    builtins=_words(
        "echo printf read cd pwd ls cp mv rm mkdir rmdir touch cat grep sed awk cut sort "
        "uniq head tail wc find xargs chmod chown ps kill sleep test exit which"
    ),
    line_comment="#",
    block_comment=None,
    string_delimiters=('"', "'"),
    variable_prefix="$",
    monaco_id="shell",
    color="#89E051",
)

_JSON = LanguageSpec(
    id="json",
    name="JSON",
    extensions=("json", "jsonc", "geojson"),
    aliases=("jsonc",),
    keywords=(),
    control_keywords=(),
    types=(),
    constants=_words("true false null"),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    function_call_highlight=False,
    monaco_id="json",
    color="#CBCB41",
)

_YAML = LanguageSpec(
    id="yaml",
    name="YAML",
    extensions=("yaml", "yml"),
    aliases=("yml",),
    keywords=_words(
        "true false null yes no on off"
    ),
    control_keywords=(),
    types=(),
    constants=_words("true false null yes no on off"),
    line_comment="#",
    block_comment=None,
    string_delimiters=('"', "'"),
    function_call_highlight=False,
    monaco_id="yaml",
    color="#CB171E",
)

_HTML = LanguageSpec(
    id="html",
    name="HTML",
    extensions=("html", "htm", "xhtml", "vue", "svelte"),
    aliases=("htm", "xhtml"),
    keywords=_words(
        "html head body div span p a img ul ol li table tr td th form input button select "
        "option textarea script style link meta title h1 h2 h3 h4 h5 h6 br hr section "
        "article header footer nav main aside template if else"
    ),
    control_keywords=(),
    types=(),
    constants=(),
    line_comment=None,
    block_comment=("<!--", "-->"),
    string_delimiters=('"', "'"),
    function_call_highlight=False,
    monaco_id="html",
    color="#E34C26",
)

_CSS = LanguageSpec(
    id="css",
    name="CSS / SCSS",
    extensions=("css", "scss", "sass", "less"),
    aliases=("scss", "sass", "less"),
    keywords=_words(
        "important media import keyframes font-face supports charset namespace page "
        "mixin include extend if else each for while function return"
    ),
    control_keywords=(),
    types=_words("px em rem vh vw percent fr deg s ms"),
    constants=(),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"', "'"),
    function_call_highlight=False,
    variable_prefix="$",
    monaco_id="css",
    color="#563D7C",
)

_MARKDOWN = LanguageSpec(
    id="markdown",
    name="Markdown",
    extensions=("md", "markdown", "mdx"),
    aliases=("md",),
    keywords=(),
    control_keywords=(),
    types=(),
    constants=(),
    line_comment=None,
    block_comment=None,
    string_delimiters=('"', "'", "`"),
    function_call_highlight=False,
    monaco_id="markdown",
    color="#083FA1",
)

_KOTLIN = LanguageSpec(
    id="kotlin",
    name="Kotlin",
    extensions=("kt", "kts"),
    aliases=("kt",),
    keywords=_words(
        "abstract actual annotation as break by catch class companion const constructor "
        "continue crossinline data delegate do dynamic else enum expect external final "
        "finally for fun get if import in infix init inline inner interface internal is "
        "lateinit noinline object open operator out override package private protected "
        "public reified return sealed set setparam super suspend tailrec this throw try "
        "typealias typeof val var vararg when where while"
    ),
    control_keywords=_words("if else for while do when break continue return try catch finally throw"),
    types=_words(
        "Any Boolean Byte Char Double Float Int Long Short String Unit Nothing List MutableList "
        "Map MutableMap Set MutableSet Array IntArray Sequence Flow"
    ),
    constants=_words("true false null"),
    builtins=_words("println print listOf mapOf setOf arrayOf mutableListOf require check"),
    line_comment="//",
    block_comment=("/*", "*/"),
    string_delimiters=('"',),
    char_delimiter="'",
    triple_string_delimiters=('"""',),
    annotation="@",
    monaco_id="kotlin",
    color="#A97BFF",
)

_SWIFT = LanguageSpec(
    id="swift",
    name="Swift",
    extensions=("swift",),
    aliases=("swift5",),
    keywords=_words(
        "actor any as associatedtype async await borrowing break case catch class consume "
        "consuming continue convenience copy default defer deinit didSet distributing do "
        "dynamic each else enum extension fallthrough false fileprivate final for func get "
        "guard if import in indirect infix init inout internal is isolated lazy let mutating "
        "nonisolated nonmutating open operator optional override package postfix precedencegroup "
        "prefix private protocol public repeat required rethrows return self set some static "
        "struct subscript super switch throw throws true try typealias var weak where while willSet"
    ),
    control_keywords=_words("if else for while repeat switch case default break continue return guard defer throw do catch"),
    types=_words(
        "Int Int8 Int16 Int32 Int64 UInt Double Float Bool String Character Array Dictionary "
        "Set Optional Result Error Any AnyObject Void Never URL Data Date UUID Task"
    ),
    constants=_words("true false nil"),
    builtins=_words("print debugPrint dump assert precondition fatalError min max abs map filter reduce"),
    line_comment="//",
    block_comment=("/*", "*/"),
    nested_block_comment=True,
    string_delimiters=('"',),
    triple_string_delimiters=('"""',),
    annotation="@",
    monaco_id="swift",
    color="#F05138",
)

_RUBY = LanguageSpec(
    id="ruby",
    name="Ruby",
    extensions=("rb", "rake", "gemspec"),
    aliases=("rb", "rails"),
    keywords=_words(
        "alias and begin break case class def defined do else elsif end ensure false for "
        "if in module next nil not or redo rescue retry return self super then true undef "
        "unless until when while yield require require_relative attr_accessor attr_reader "
        "attr_writer lambda proc puts raise"
    ),
    control_keywords=_words("if elsif else unless case when while until for do end begin rescue ensure return break next"),
    types=_words("String Integer Float Array Hash Symbol Range Struct Module Class Proc Lambda IO File"),
    constants=_words("true false nil self __FILE__ __LINE__"),
    builtins=_words("puts print p gets require require_relative attr_accessor new each map select reject"),
    line_comment="#",
    block_comment=("=begin", "=end"),
    string_delimiters=('"', "'"),
    annotation="@",
    variable_prefix="$",
    monaco_id="ruby",
    color="#701516",
)

_LUA = LanguageSpec(
    id="lua",
    name="Lua",
    extensions=("lua",),
    aliases=("lua5",),
    keywords=_words(
        "and break do else elseif end false for function goto if in local nil not or "
        "repeat return then true until while"
    ),
    control_keywords=_words("if else elseif for while repeat until do then end break return"),
    types=(),
    constants=_words("true false nil _G _VERSION"),
    builtins=_words(
        "print type pairs ipairs tostring tonumber require setmetatable getmetatable "
        "rawget rawset pcall xpcall error assert select unpack table string math io os coroutine"
    ),
    line_comment="--",
    block_comment=("--[[", "]]"),
    string_delimiters=('"', "'"),
    monaco_id="lua",
    color="#000080",
)

_PLAINTEXT = LanguageSpec(
    id="plaintext",
    name="Plain Text",
    extensions=("txt", "text", "log", "ini", "cfg", "conf", "toml", "env"),
    aliases=("text", "txt", "none", "toml", "ini"),
    keywords=(),
    control_keywords=(),
    types=(),
    constants=(),
    line_comment=None,
    block_comment=None,
    string_delimiters=(),
    function_call_highlight=False,
    monaco_id="plaintext",
    color="#9E9E9E",
)


# 顺序即界面下拉框的顺序 (常用语言在前)。
_SPECS: Tuple[LanguageSpec, ...] = (
    _PYTHON,
    _C,
    _CPP,
    _GO,
    _JAVA,
    _PHP,
    _RUST,
    _JAVASCRIPT,
    _TYPESCRIPT,
    _CSHARP,
    _KOTLIN,
    _SWIFT,
    _RUBY,
    _LUA,
    _SQL,
    _BASH,
    _JSON,
    _YAML,
    _HTML,
    _CSS,
    _MARKDOWN,
    _PLAINTEXT,
)

LANGUAGES: Dict[str, LanguageSpec] = {spec.id: spec for spec in _SPECS}
DEFAULT_LANGUAGE = _PYTHON.id

# 别名 -> 规范 id
_ALIAS_INDEX: Dict[str, str] = {}


def _build_alias_index() -> None:
    for spec in _SPECS:
        _ALIAS_INDEX[spec.id.lower()] = spec.id
        for alias in spec.aliases:
            _ALIAS_INDEX.setdefault(alias.lower(), spec.id)
        _ALIAS_INDEX.setdefault(spec.name.lower(), spec.id)


_build_alias_index()

# 扩展名 -> 规范 id
_EXTENSION_INDEX: Dict[str, str] = {}
for _spec in _SPECS:
    for _ext in _spec.extensions:
        _EXTENSION_INDEX.setdefault(_ext.lower(), _spec.id)


def normalize_language(value: Optional[str]) -> str:
    """把用户输入的语言标识规范化为注册表 id。

    识别 ``py`` / ``Python`` / ``python3`` / ``.py`` 等写法; 无法识别时返回 ``plaintext``。
    """
    if not value:
        return _PLAINTEXT.id
    key = str(value).strip().lower().lstrip(".")
    if key in LANGUAGES:
        return key
    if key in _ALIAS_INDEX:
        return _ALIAS_INDEX[key]
    if key in _EXTENSION_INDEX:
        return _EXTENSION_INDEX[key]
    return _PLAINTEXT.id


def get_language(value: Optional[str]) -> LanguageSpec:
    """取得 :class:`LanguageSpec`; 未知语言退化为 ``plaintext``。"""
    return LANGUAGES[normalize_language(value)]


def is_known_language(value: Optional[str]) -> bool:
    """判断语言标识是否在注册表内 (而非退化结果)。"""
    if not value:
        return False
    key = str(value).strip().lower().lstrip(".")
    return key in LANGUAGES or key in _ALIAS_INDEX or key in _EXTENSION_INDEX


def language_choices(include_plain: bool = True) -> List[Tuple[str, str]]:
    """返回 ``[(id, 显示名), ...]`` 供下拉框使用。"""
    items = [(spec.id, spec.name) for spec in _SPECS if include_plain or spec.id != "plaintext"]
    return items


def detect_language_from_filename(filename: str) -> str:
    """根据文件名后缀猜测语言。"""
    if not filename:
        return _PLAINTEXT.id
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if "." not in name:
        lowered = name.lower()
        if lowered in {"dockerfile", "makefile", "rakefile", "gemfile", "cmakelists"}:
            return _BASH.id if lowered in {"dockerfile", "makefile", "cmakelists"} else _RUBY.id
        return _PLAINTEXT.id
    ext = name.rsplit(".", 1)[-1].lower()
    return _EXTENSION_INDEX.get(ext, _PLAINTEXT.id)


def default_filename(language: str, slug: str = "main") -> str:
    """为该语言生成一个默认文件名。"""
    spec = get_language(language)
    ext = spec.extensions[0] if spec.extensions else "txt"
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (slug or "main"))
    return f"{safe or 'main'}.{ext}"


def all_languages() -> Iterable[LanguageSpec]:
    """遍历全部语言定义 (按注册顺序)。"""
    return iter(_SPECS)


def languages_supporting(*, comment: bool = False) -> List[LanguageSpec]:
    """按能力筛选语言 (目前仅演示按注释能力筛选)。"""
    if comment:
        return [spec for spec in _SPECS if spec.line_comment or spec.block_comment]
    return list(_SPECS)
