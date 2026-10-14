"""Language standards for the checks and targets of a project: `std_copts`.

`std_copts(c = "gnu99")` / `std_copts(cxx = "c++17")` spell a standard for the
compiler in use, for the `conlyopts` / `cxxopts` of an `autoconf` target (its
checks) and of the project's `cc_*` targets.  Not transitive: a standard for
every check of a build is `--copt=-std=...`, and
`--@rules_cc_autoconf//autoconf/settings:std_copts=false` empties every
`std_copts()` so that the command line is the only source.
"""

load("//autoconf/private:standards.bzl", _CXX_STANDARDS = "CXX_STANDARDS", _C_STANDARDS = "C_STANDARDS", _std_copts = "std_copts")

C_STANDARDS = _C_STANDARDS
CXX_STANDARDS = _CXX_STANDARDS
std_copts = _std_copts
