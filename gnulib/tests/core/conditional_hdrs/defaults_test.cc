// `default_deps`: the conditions also see a header port's defaults.
// GL_GENERATE_ICONV_H is "false" in iconv_h's defaults target, a variable
// the deps of this test never set.
#include "gl_conditional_default.h"

#ifndef GL_CONDITIONAL_HDRS_DEFAULT_SENTINEL
#error "A header conditioned on a toolchain default is missing"
#endif

// A dependency's result wins over the default: with GL_GENERATE_ICONV_H
// set to "true" by a dep, the ==true header is present and the !VAR one
// is not.
#include "gl_conditional_override.h"

#ifndef GL_CONDITIONAL_HDRS_OVERRIDE_SENTINEL
#error "A header conditioned on a dep's override of a default is missing"
#endif

#if __has_include("gl_conditional_override_absent.h")
#error "The default must not shadow a dependency's result"
#endif

int main(void) { return 0; }
