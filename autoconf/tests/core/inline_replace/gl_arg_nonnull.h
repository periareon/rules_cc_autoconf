/* Define _GL_ARG_NONNULL to empty if the compiler doesn't support it */
/* Package: @PACKAGE_NAME@ version @PACKAGE_VERSION@ */
#ifndef _GL_ARG_NONNULL
#if defined(__GNUC__) && \
    (__GNUC__ >= 3 || (__GNUC__ == 2 && __GNUC_MINOR__ >= 7))
#define _GL_ARG_NONNULL(params) __attribute__((__nonnull__ params))
#else
#define _GL_ARG_NONNULL(params)
#endif
#endif
/* Have stdio: @HAVE_STDIO_H@ */
