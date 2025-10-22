"""C++ compiler options for autoconf tools."""

def cxxopts():
    """Returns a select statement for C++17 compiler options.

    Returns a select statement that chooses the appropriate C++17 compiler flag
    based on the compiler type:
    - MSVC: /std:c++17
    - GCC/Clang: -std=c++17

    Returns:
        A select statement suitable for use in cc_library or cc_binary cxxopts.
    """
    return select({
        "@rules_cc//cc/compiler:msvc-cl": ["/std:c++17"],
        "//conditions:default": ["-std=c++17"],
    })
