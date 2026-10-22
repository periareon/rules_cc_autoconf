# rules_cc_autoconf converter: stand-ins for pkg-config's pkg.m4.
#
# The pinned autotools carry no pkg.m4, and a project's own copy would expand
# to pkg-config invocations that have no Bazel meaning.  In a Bazel build a
# module found through pkg-config is a bazel_dep instead.  Whether a package is
# found is the converter's call (tools/convert/libraries.toml: the registry
# modules it knows and their versions), made at conversion time: each macro
# expands both of its actions, the found one in RCCAC_PKG_FOUND and the other
# in RCCAC_PKG_MISSING, and the converter keeps the one its verdict picks.
# Found: the *_CFLAGS / *_LIBS variables start empty (the dependency carries
# headers and archive), the action-if-found may append to them, and both are
# substituted; the consumer's targets that link $(X_LIBS) get the dependency.

AC_DEFUN([PKG_PROG_PKG_CONFIG], [AC_SUBST([PKG_CONFIG], [pkg-config])])
AC_DEFUN([RCCAC_PKG_FOUND], [$3])
AC_DEFUN([RCCAC_PKG_MISSING], [$3])
AC_DEFUN([PKG_CHECK_EXISTS],
[RCCAC_PKG_FOUND([], [$1], [m4_default([$2], [:])])
RCCAC_PKG_MISSING([], [$1], [m4_default([$3], [:])])])
AC_DEFUN([PKG_CHECK_MODULES],
[RCCAC_PKG_FOUND([$1], [$2], [$1[]_CFLAGS=""
$1[]_LIBS=""
m4_default([$3], [:])
AC_SUBST([$1][_CFLAGS])
AC_SUBST([$1][_LIBS])])
RCCAC_PKG_MISSING([$1], [$2], [m4_default([$4], [AC_MSG_ERROR([Package requirements ($2) were not met])])])])
AC_DEFUN([PKG_CHECK_MODULES_STATIC], [PKG_CHECK_MODULES($@)])
AC_DEFUN([PKG_CHECK_VAR], [AC_SUBST([$1], [])])
AC_DEFUN([PKG_INSTALLDIR], [AC_SUBST([pkgconfigdir], [${libdir}/pkgconfig])])
AC_DEFUN([PKG_NOARCH_INSTALLDIR], [AC_SUBST([noarch_pkgconfigdir], [${datadir}/pkgconfig])])
AC_DEFUN([PKG_PREREQ], [])
AC_DEFUN([PKG_HAVE_DEFINE_WITH_MODULES], [AC_DEFINE([HAVE_][$1], [1], [Define if $2 is available.])])
AC_DEFUN([PKG_WITH_MODULES], [m4_default([$3], [:])])
AC_DEFUN([PKG_HAVE_WITH_MODULES], [m4_default([$3], [:])])
