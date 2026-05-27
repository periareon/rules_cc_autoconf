"""Shared helper for looking up an autoconf check result by name across the
cache / define / subst buckets exposed by `CcAutoconfInfo`."""

def find_result_file(name, all_cache, all_define, all_subst, label):
    """Look up a check result name across cache/define/subst namespaces.

    Returns the result `File`, or fails with a clear error if not found or
    ambiguous. When the same result is exposed under multiple buckets that
    refer to the same underlying file (same path), the lookup succeeds; only
    distinct paths trigger the ambiguity error.

    Args:
        name: The result name to look up (e.g. `"HAVE_FOO"` or
            `"ac_cv_func_foo"`).
        all_cache: Mapping of cache name -> result `File`.
        all_define: Mapping of define name -> result `File`.
        all_subst: Mapping of subst name -> result `File`.
        label: The rule's label, used in error messages.

    Returns:
        The matched result `File`.
    """
    candidates = []
    if name in all_cache:
        candidates.append(("cache", all_cache[name]))
    if name in all_define:
        candidates.append(("define", all_define[name]))
    if name in all_subst:
        candidates.append(("subst", all_subst[name]))

    if not candidates:
        all_available = {
            "cache": sorted(all_cache.keys()),
            "define": sorted(all_define.keys()),
            "subst": sorted(all_subst.keys()),
        }
        fail("`{}` requires `{}` which is not provided by any deps. Available: {}".format(
            label,
            name,
            json.encode_indent(all_available, indent = " " * 4),
        ))

    distinct_paths = {}
    for bucket, f in candidates:
        distinct_paths[f.path] = (bucket, f)

    if len(distinct_paths) != 1:
        fail("`{}` requires `{}` but it is ambiguous across deps.\nMatches: {}".format(
            label,
            name,
            [(bucket, f.path) for (bucket, f) in candidates],
        ))

    return distinct_paths.values()[0][1]
