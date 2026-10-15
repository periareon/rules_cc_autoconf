"""# providers"""

def _cc_autoconf_info_init(
        owner,
        deps = None,
        cache_results = {},
        content_cache = {},
        define_results = {},
        subst_results = {},
        unquoted_defines = [],
        defaults = None):
    """Preprocess CcAutoconfInfo fields, filling in safe defaults."""
    return {
        "cache_results": cache_results,
        "content_cache": content_cache,
        "defaults": defaults if defaults != None else depset(),
        "define_results": define_results,
        "deps": deps if deps != None else depset(),
        "owner": owner,
        "subst_results": subst_results,
        "unquoted_defines": unquoted_defines,
    }

CcAutoconfInfo, _new_cc_autoconf_info = provider(
    doc = "A provider containing autoconf configuration and results.",
    fields = {
        "cache_results": "dict[str, File]: A map of cache variable names to flat result JSON files. Used for requires/condition resolution.",
        "content_cache": "dict[str, File]: A map of content-based keys to result files. Used for action deduplication across deps and toolchains.",
        "defaults": "depset[CcAutoconfDefaultsInfo]: The defaults this target reads and passes on: its `default_deps` and the defaults of its `deps`. Kept apart from the results above; a consumer reads them only where no ordinary result of the name exists.",
        "define_results": "dict[str, File]: A map of define names to flat result JSON files produced by `CcAutoconfCheck` actions.",
        "deps": "depset[CcAutoconfInfo]: Dependencies of the current info target.",
        "owner": "Label: The label of the owner of the results.",
        "subst_results": "dict[str, File]: A map of subst names to flat result JSON files produced by `CcAutoconfCheck` actions.",
        "unquoted_defines": "list[str]: Define names that should be rendered unquoted (AC_DEFINE_UNQUOTED).",
    },
    init = _cc_autoconf_info_init,
)

def _cc_autoconf_defaults_info_init(
        owner,
        cache_results = {},
        define_results = {},
        subst_results = {},
        unquoted_defines = [],
        content = {}):
    """Preprocess CcAutoconfDefaultsInfo fields, filling in safe defaults."""
    return {
        "cache_results": cache_results,
        "content": content,
        "define_results": define_results,
        "owner": owner,
        "subst_results": subst_results,
        "unquoted_defines": unquoted_defines,
    }

CcAutoconfDefaultsInfo, _new_cc_autoconf_defaults_info = provider(
    doc = """\
Default values for autoconf results, produced by `autoconf_defaults`.

Defaults are not check results: nothing probes for them, and they never take
part in duplicate detection.  A target reads them through `default_deps`,
beneath every ordinary result, so a probe of the same name overrides a default
silently.  This is gnulib's `*_H_DEFAULTS` pattern (`HAVE_X=1`, `REPLACE_X=0`
before the probes may reassign them).  Only `default_deps` accepts this
provider; `deps` does not.
""",
    fields = {
        "cache_results": "dict[str, File]: Cache variable name to result file, for `requires`/`condition` resolution.",
        "content": "dict[str, str]: Result file path to its content. Two defaults of one name must agree, which this is compared on.",
        "define_results": "dict[str, File]: Define name to result file.",
        "owner": "Label: The `autoconf_defaults` target.",
        "subst_results": "dict[str, File]: Subst name to result file.",
        "unquoted_defines": "list[str]: Define names rendered unquoted.",
    },
    init = _cc_autoconf_defaults_info_init,
)
