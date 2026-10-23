"""The registry library table and its name lookup."""

from __future__ import annotations

from tools.convert import libraries
from tools.convert.decisions import Decisions, Word
from tools.convert.ir import Call, Dep, Target
from tools.convert.results import _library_check_targets, _registry_libraries_in_configure
from tools.convert.tests.helpers import calls, must
from tools.convert.translate import ProjectState

# ---- The library table and its name lookup


def known(name: str) -> libraries.Library:
    """The table's entry for `name`, which the test expects to be there."""
    return must(libraries.known(name))


def test_link_names() -> None:
    assert known("z").label == "@zlib//:z"
    assert known("-lz").label == "@zlib//:z"
    assert known("gmp").module == "gmp"
    assert libraries.known("m") is None  # the host's, a link option
    assert libraries.known("nosuchlibrary") is None


def test_alternate_spellings() -> None:
    """`libz`, the module or pkg-config name and a versioned pkg-config name find the entry."""
    assert known("libz").label == "@zlib//:z"
    assert known("zlib").label == "@zlib//:z"
    assert known("libcurl").module == "curl"
    assert known("libpng16").label == "@libpng//:png"
    assert known("libssl").label == "@openssl//:ssl"
    # openssl backs ssl and crypto: the module name alone names neither.
    assert libraries.known("openssl") is None
    # ncurses backs several names and is one of them.
    assert known("ncurses").label == known("tinfo").label


def test_system_libraries_are_not_modules() -> None:
    """Windows' crypt32 is not libxcrypt with a version suffix; libpng16 is libpng."""
    assert libraries.known("-lcrypt32") is None
    assert libraries.known("ws2_32") is None
    assert libraries.known("m") is None
    assert known("libpng16").module == "libpng"


def test_configure_links_registry_library() -> None:
    """`LIBS="-lgmp $LIBS"` in configure's own shell: the module is recorded, and the checks
    that need the library move to a target of their own with it as cc_deps; the main target
    stays toolchain-only and depends on that target."""
    state = ProjectState()
    notes = _registry_libraries_in_configure(
        {
            "configure.ac": 'dnl -lfoo in a comment is not a link\nLIBS="-lgmp $LIBS"\n',
            "acinclude.m4": "#include <zlib.h>\n",
            "gnulib-comp.m4": 'LIBS="-lpng $LIBS"',
        },
        {"configure.ac", "acinclude.m4"},
        state,
        Decisions(),
        ".",
    )
    assert state.configure_libs == {"gmp": "@gmp//:gmp", "z": "@zlib//:z"}
    assert set(state.external_modules) == {"gmp", "zlib"}
    assert len(notes) == 2
    segment = Target(
        name="autoconf_1", checks=[Call("checks.AC_CHECK_HEADER", ["gmp.h"]), Call("checks.AC_CHECK_FUNC", ["strdup"])]
    )
    main = Target(
        name="autoconf",
        checks=[
            Call("checks.AC_CHECK_LIB", ["gmp", "__gmpz_init"]),
            Call("checks.AC_SEARCH_LIBS", ["inflate", ["z"]]),
            Call(
                "checks.AC_TRY_LINK", [], {"name": "my_cv_z", "code": "#include <zlib.h>\nint main(void) { return 0; }"}
            ),
            Call("checks.AC_DEFINE", ["USE_GMP", 1], {"requires": ["ac_cv_lib_gmp___gmpz_init"]}),
        ],
    )
    main.deps = [Dep("//autoconf/macros/AC_PROG_CC"), Dep(":autoconf_1")]
    notes = []
    targets = _library_check_targets(main, [segment], state, notes)
    assert [t.name for t in targets] == ["autoconf_gmp", "autoconf_gmp_2", "autoconf_z"]
    assert [c.args[0] for c in calls(targets[0].checks)] == ["gmp.h"]
    assert [c.args[0] for c in calls(targets[1].checks)] == ["gmp"]
    assert [c.fn for c in calls(targets[2].checks)] == ["checks.AC_SEARCH_LIBS", "checks.AC_TRY_LINK"]
    assert targets[0].attrs == {"cc_deps": ["@gmp//:gmp"]}
    assert [d.label for d in targets[0].deps] == ["//autoconf/macros/AC_PROG_CC"]
    assert [d.label for d in targets[1].deps] == ["//autoconf/macros/AC_PROG_CC", ":autoconf_1"]
    assert "cc_deps" not in main.attrs
    assert [c.args[0] for c in calls(segment.checks)] == ["strdup"]
    assert [c.args[0] for c in calls(main.checks)] == ["USE_GMP"]
    assert [d.label for d in main.deps] == [
        "//autoconf/macros/AC_PROG_CC",
        ":autoconf_1",
        ":autoconf_gmp",
        ":autoconf_gmp_2",
        ":autoconf_z",
    ]
    assert [d.label for d in segment.deps] == [":autoconf_gmp"]  # its own checks may require them
    assert any("autoconf_gmp" in n for n in notes)


def test_library_checks_under_select_arms() -> None:
    """libssh2's openssl probe sits in the `:crypto_openssl` arm (a `guard:` decision put it
    there): it moves to the same arm of the library target, with both libraries it links as
    cc_deps; the body's arm keeps what reads its result."""
    state = ProjectState()
    state.configure_libs = {"ssl": "@openssl//:ssl", "crypto": "@openssl//:crypto"}
    main = Target(name="autoconf", checks=[Call("checks.AC_CHECK_FUNC", ["strdup"])])
    main.deps = [Dep("//autoconf/macros/AC_PROG_CC")]
    probe = Call(
        "checks.AC_TRY_LINK",
        [],
        {
            "name": "ac_cv_libssl",
            "code": "#include <openssl/ssl.h>\nint main(void) { return 0; }",
            "define": "HAVE_LIBSSL",
            "linkopts": ["-lssl", "-lcrypto"],
        },
    )
    subst = Call("checks.AC_SUBST", ["HAVE_LIBSSL"], {"condition": "ac_cv_libssl", "if_true": "yes", "if_false": "no"})
    main.arm(":crypto_openssl", "crypto").extend([probe, subst])
    main.arm("//conditions:default", "crypto")
    notes: list[str] = []
    targets = _library_check_targets(main, [], state, notes)
    assert [t.name for t in targets] == ["autoconf_crypto"]
    (target,) = targets
    assert target.checks == []
    assert {
        label: [c.kwargs.get("name") for c in calls(items)] for label, items in target.selects["crypto"].items()
    } == {":crypto_openssl": ["ac_cv_libssl"], "//conditions:default": []}
    assert target.attrs == {"cc_deps": ["@openssl//:crypto", "@openssl//:ssl"]}
    assert [c.fn for c in calls(main.selects["crypto"][":crypto_openssl"])] == ["checks.AC_SUBST"]
    assert [d.label for d in main.deps] == ["//autoconf/macros/AC_PROG_CC", ":autoconf_crypto"]
    assert any(":crypto_openssl arm" in n for n in notes), notes


def test_library_decision_ignore() -> None:
    """`library:<package>:<name>` IGNORE leaves the registry library out of that package."""
    state = ProjectState()
    decisions = Decisions({"library:extension:gmp": Word("IGNORE")})
    notes = _registry_libraries_in_configure(
        {"configure.ac": 'LIBS="-lgmp $LIBS"\n'}, {"configure.ac"}, state, decisions, "extension"
    )
    assert state.configure_libs == {}
    assert state.external_modules == {}
    assert "library:extension:gmp" in notes[0]
    assert decisions.unused == []


def test_table_entries_are_labels() -> None:
    for name, entry in libraries.table().items():
        assert entry.label.startswith(f"@{entry.module}//:"), (name, entry)


# ---- pkg-config specs and versions


def test_specs_and_versions() -> None:
    """PKG_CHECK_MODULES package lists and pkg-config's version order."""
    assert libraries.parse_specs("fuse3 >= 3.1 glib-2.0 libpcre2-8 = 10.42") == [
        ("fuse3", ">=", "3.1"),
        ("glib-2.0", None, None),
        ("libpcre2-8", "=", "10.42"),
    ]
    assert libraries.parse_specs("ogg >= $THEORA_LIBOGG_REQ_VERSION") == [("ogg", ">=", "$THEORA_LIBOGG_REQ_VERSION")]
    assert [lib.module for lib in must(libraries.known_pkg("alsa"))] == ["alsa_lib"]
    assert [lib.label for lib in must(libraries.known_pkg("openssl"))] == ["@openssl//:ssl", "@openssl//:crypto"]
    assert [lib.module for lib in must(libraries.known_pkg("zlib"))] == ["zlib"]
    assert libraries.upstream_version("1.3.2.bcr.2") == "1.3.2"
    assert libraries.upstream_version("3.16.2") == "3.16.2"
    assert libraries.version_satisfies("1.3.2", ">=", "1.2")
    assert libraries.version_satisfies("1.3", ">=", "1.3.0")
    assert not libraries.version_satisfies("1.3.2", ">=", "1.10")  # numeric, not lexical
    assert libraries.version_satisfies("10.42", "<", "10.45")
    assert libraries.version_satisfies("2.0", None, None)
    assert not libraries.version_satisfies("1.0", "!=", "1.0")
    # The table resolves pkg-config names the way it resolves -l names.
    assert known("zlib").module == "zlib"
    assert known("fuse3").module == "libfuse"
    assert known("freetype2").module == "freetype"
    assert libraries.known("glib-2.0") is None
