"""# providers"""

def _cc_autoconf_info_init(
        owner,
        deps = None,
        cache_results = {},
        content_cache = {},
        define_results = {},
        subst_results = {},
        unquoted_defines = []):
    """Preprocess CcAutoconfInfo fields, filling in safe defaults."""
    return {
        "cache_results": cache_results,
        "content_cache": content_cache,
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
        "define_results": "dict[str, File]: A map of define names to flat result JSON files produced by `CcAutoconfCheck` actions.",
        "deps": "depset[CcAutoconfInfo]: Dependencies of the current info target.",
        "owner": "Label: The label of the owner of the results.",
        "subst_results": "dict[str, File]: A map of subst names to flat result JSON files produced by `CcAutoconfCheck` actions.",
        "unquoted_defines": "list[str]: Define names that should be rendered unquoted (AC_DEFINE_UNQUOTED).",
    },
    init = _cc_autoconf_info_init,
)
