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

int main(void) { return 0; }
