// Truthy condition: the rule wrote the src content into its TreeArtifact
// at `gl_conditional_truthy.h`, so the file is reachable and exposes its
// sentinel.
#include "gl_conditional_truthy.h"

#ifndef GL_CONDITIONAL_HDRS_TRUTHY_SENTINEL
#error "Truthy header is missing its sentinel macro"
#endif

// Falsy condition: the rule emitted no file for this src (matching
// gnulib's `rm -f $@` semantics), so the file must be absent from the
// rule's include dir.  Nothing else on the include path supplies a
// `gl_conditional_falsy.h`, so `__has_include` reports 0.
#if __has_include("gl_conditional_falsy.h")
#error "Falsy header must not be present in the rule's TreeArtifact"
#endif

// `COND_FALSE==false` compares the string: the value is "false", so the
// condition holds and the header is present.
#include "gl_conditional_eq_false.h"

#ifndef GL_CONDITIONAL_HDRS_EQ_FALSE_SENTINEL
#error "A header gated on VAR==false must be present when VAR is \"false\""
#endif

// strip_include_prefix: `src/_gnulib_cond/sub/x.h` is included as <sub/x.h>,
// and the falsy sibling is absent.
#include <sub/gl_conditional_nested.h>

#ifndef GL_CONDITIONAL_HDRS_NESTED_SENTINEL
#error "A header under strip_include_prefix is missing its sentinel"
#endif

#if __has_include(<sub/gl_conditional_nested_absent.h>)
#error "The falsy nested header must not be present"
#endif

int main(void) { return 0; }
