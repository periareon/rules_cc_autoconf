"""Language standards as compiler flags.

A configure run that demands a standard (`AC_PROG_CC_C99`,
`AX_CXX_COMPILE_STDCXX`, `CFLAGS="$CFLAGS -std=gnu11"`) demands it of its
checks and of its compiles.  The standard is named, not spelled as a flag:
GCC and clang take `-std=gnu99`, MSVC takes `/std:c11` and has no C99 switch
at all, so `std_copts` picks the flag per compiler with a `select()`.  It is
not transitive and not a build setting: a standard for a whole build,
dependencies included, is `--copt=-std=...`, which every check honours, and
`--@rules_cc_autoconf//autoconf/settings:std_copts=false` turns every
`std_copts()` off so that the command line is the only source.
"""

C_STANDARDS = [
    "c89",
    "c90",
    "c99",
    "c11",
    "c17",
    "c18",
    "c23",
    "c2x",
    "gnu89",
    "gnu90",
    "gnu99",
    "gnu11",
    "gnu17",
    "gnu18",
    "gnu23",
    "gnu2x",
]
CXX_STANDARDS = [
    "c++98",
    "c++03",
    "c++11",
    "c++14",
    "c++17",
    "c++20",
    "c++23",
    "c++26",
    "gnu++98",
    "gnu++03",
    "gnu++11",
    "gnu++14",
    "gnu++17",
    "gnu++20",
    "gnu++23",
    "gnu++26",
]

# MSVC's /std: switches; C before C11 and C++ before C++14 have none (the
# compiler's default is at least that), the newest standards are /std:*latest.
_MSVC_C = {
    "c11": "/std:c11",
    "c17": "/std:c17",
    "c18": "/std:c17",
    "c23": "/std:clatest",
    "c2x": "/std:clatest",
}
_MSVC_CXX = {
    "c++11": "/std:c++14",
    "c++14": "/std:c++14",
    "c++17": "/std:c++17",
    "c++20": "/std:c++20",
    "c++23": "/std:c++latest",
    "c++26": "/std:c++latest",
}

def _base(standard):
    """`gnu++17` -> `c++17`, `gnu99` -> `c99`: the GNU dialects name the same standard."""
    return standard.replace("gnu++", "c++").replace("gnu", "c", 1) if standard.startswith("gnu") else standard

def standard_flags(standard, msvc, cxx = False):
    """The compiler flags for `standard`: `-std=X`, or MSVC's `/std:` switch when there is one.

    Args:
        standard: a member of C_STANDARDS or CXX_STANDARDS, or None for none.
        msvc: whether the compiler takes MSVC's switches (msvc-cl, clang-cl).
        cxx: whether `standard` is a C++ standard.

    Returns:
        A list of flags, empty when the compiler has no switch for it.
    """
    if not standard:
        return []
    if msvc:
        flag = (_MSVC_CXX if cxx else _MSVC_C).get(_base(standard))
        return [flag] if flag else []
    return ["-std=" + standard]

def std_copts(c = None, cxx = None):
    """The flags for a language standard, spelled for the compiler in use.

    For `conlyopts` (a C standard), `cxxopts` (a C++ standard) or `copts`, of
    an `autoconf` target's checks or of a `cc_*` target.  Empty when
    `//autoconf/settings:std_copts` is off.

    Args:
        c: a C standard (C_STANDARDS).
        cxx: a C++ standard (CXX_STANDARDS).

    Returns:
        A `select()` over the compiler and the `std_copts` setting.
    """
    if c and c not in C_STANDARDS:
        fail("std_copts: unknown C standard {}; one of {}".format(c, C_STANDARDS))
    if cxx and cxx not in CXX_STANDARDS:
        fail("std_copts: unknown C++ standard {}; one of {}".format(cxx, CXX_STANDARDS))
    msvc = standard_flags(c, True) + standard_flags(cxx, True, cxx = True)
    return select({
        Label("//autoconf/settings:std_copts_clang_cl"): msvc,
        Label("//autoconf/settings:std_copts_disabled"): [],
        Label("//autoconf/settings:std_copts_msvc_cl"): msvc,
        "//conditions:default": standard_flags(c, False) + standard_flags(cxx, False, cxx = True),
    })
