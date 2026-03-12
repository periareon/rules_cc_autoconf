"""C++ compiler options for autoconf tools."""

def cxxopts():
    """Returns a select statement for C++17 compiler options.

    Returns:
        A select statement suitable for use in cc_library or cc_binary cxxopts.
    """
    return select({
        "@rules_cc//cc/compiler:msvc-cl": ["/std:c++17"],
        "//conditions:default": [
            "-std=c++17",
            # Fixes compatibility issues between old and new versions of macOS `std::filesystem`
            # For more details see: <LINK>
            "-D_LIBCPP_DISABLE_AVAILABILITY",
        ],
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
