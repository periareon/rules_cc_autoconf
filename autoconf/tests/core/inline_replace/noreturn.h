/* Define _Noreturn to empty if the compiler doesn't support it */
#ifndef _Noreturn
#if (defined __cplusplus && __cplusplus >= 201103L) || \
    (defined __STDC_VERSION__ && __STDC_VERSION__ >= 201112L)
#define _Noreturn _Noreturn
#elif defined __GNUC__
#define _Noreturn __attribute__((__noreturn__))
#else
#define _Noreturn
#endif
#endif
