# rules_cc_autoconf converter: stand-ins for pkg-config's pkg.m4.
#
# The pinned autotools carry no pkg.m4, and a project's own copy would expand
# to pkg-config invocations that have no Bazel meaning.  In a Bazel build a
# module found through pkg-config is a bazel_dep instead, so the macros take
# the "found" path and substitute the *_CFLAGS / *_LIBS variables empty; the
# consumer adds the dep where the library is linked.

AC_DEFUN([PKG_PROG_PKG_CONFIG], [AC_SUBST([PKG_CONFIG], [pkg-config])])
AC_DEFUN([PKG_CHECK_EXISTS], [$2])
AC_DEFUN([PKG_CHECK_MODULES],
[AC_SUBST([$1][_CFLAGS], [])
AC_SUBST([$1][_LIBS], [])
m4_default([$3], [:])])
AC_DEFUN([PKG_CHECK_MODULES_STATIC], [PKG_CHECK_MODULES($@)])
AC_DEFUN([PKG_CHECK_VAR], [AC_SUBST([$1], [])])
AC_DEFUN([PKG_INSTALLDIR], [AC_SUBST([pkgconfigdir], [${libdir}/pkgconfig])])
AC_DEFUN([PKG_NOARCH_INSTALLDIR], [AC_SUBST([noarch_pkgconfigdir], [${datadir}/pkgconfig])])
AC_DEFUN([PKG_PREREQ], [])
AC_DEFUN([PKG_HAVE_DEFINE_WITH_MODULES], [AC_DEFINE([HAVE_][$1], [1], [Define if $2 is available.])])
AC_DEFUN([PKG_WITH_MODULES], [m4_default([$3], [:])])
AC_DEFUN([PKG_HAVE_WITH_MODULES], [m4_default([$3], [:])])
