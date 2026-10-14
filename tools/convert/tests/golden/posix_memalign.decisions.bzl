"""Decisions for converting posix_memalign.

Written by `bazel run @rules_cc_autoconf//tools/convert -- convert` and read
back on every run.  Each key below is a construct of the configure.ac or m4
that the converter cannot translate on its own; the comments above it show
the source it stands for and what the converter found in it.  Replace each
`UNDECIDED` with one of these values, then run the same command again:

    UNDECIDED        not decided yet: the construct stays out, the run exits 3
    IGNORE           leave the construct out for good
    ACCEPT           keep the converter's own translation of the probe as it is
    ACCEPT_PARTIAL   keep the assignments the converter could follow, drop the rest
    ALWAYS / NEVER   the guarded checks or sources always / never apply
    "text", 0, 1     a literal: the cache value, the substituted value, a dep
                     label, a condition expression or a config_setting label
    True / False     the default of a configure option's bool_flag
    select({...})    a literal per configuration label ("//conditions:default" last)
    checks.X(...)    a check written exactly as it would appear in BUILD.bazel,
    macros.X(...)    or a list of them; a replacement probe must set the cache
                     variable it replaces (name = "...")

Which of these a key takes depends on its kind, the part before the colon:

    probe:<cache variable>    IGNORE | ACCEPT | a literal | select() | a replacement
                              probe (checks.AC_TRY_COMPILE / checks.AC_TRY_LINK) | checks
    variable:<NAME>           IGNORE | ACCEPT_PARTIAL | a literal | select() | checks
    guard:<target>:<check>    ALWAYS | NEVER | ["<cache variable>", ...] (requires) |
                              select({"<label>": ["<cache variable>", ...]})
    macro:<NAME>              IGNORE | "<dep label>" | checks
    actions:<macro>:<arg>     IGNORE | checks
    flag:<option>             IGNORE | True | False
    am_conditional:<NAME>     ALWAYS | NEVER | ":<config_setting>"
    src_condition:<NAME>      ALWAYS | NEVER | "<condition expression>"

These words are read by the converter, not by Bazel: the file is parsed, never
loaded.  Entries the converter stops asking for are reported as unused.
"""

DECISIONS = {
    # gl_FUNC_POSIX_MEMALIGN (m4/posix_memalign.m4): REPLACE_POSIX_MEMALIGN: some assignments have conditions that cannot be expressed
    # m4 (shell): m4/posix_memalign.m4:~59: *) REPLACE_POSIX_MEMALIGN=1 ;;
    # assignments:
    #   REPLACE_POSIX_MEMALIGN=1  when ac_cv_func_posix_memalign && !gl_cv_func_posix_memalign_works
    #   REPLACE_POSIX_MEMALIGN=1  when gl_cv_onwards_func_posix_memalign matches future*
    # Decision: The dropped assignment is the Android `future*` case, not modeled, as in the hand port.
    "variable:REPLACE_POSIX_MEMALIGN": ACCEPT_PARTIAL,
}
