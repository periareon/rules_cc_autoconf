"""C++ compiler options for autoconf tools."""

def cxxopts():
    """Returns a select statement for C++17 compiler options.

    Returns:
        A select statement suitable for use in cc_library or cc_binary cxxopts.
    """
    return select({
        "@rules_cc//cc/compiler:msvc-cl": ["/std:c++17"],
        "//conditions:default": ["-std=c++17"],
    })

def linkopts():
    """Returns a select statement for C++17 link options.

    Returns:
        A select statement suitable for use in cc_library or cc_binary linkopts.
    """

    return select({
        "@rules_cc//cc/compiler:gcc": ["-lstdc++fs"],
        "@rules_cc//cc/compiler:mingw-gcc": ["-lstdc++fs"],
        "//conditions:default": [],
    })
