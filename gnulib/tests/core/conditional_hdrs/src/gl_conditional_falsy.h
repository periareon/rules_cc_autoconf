// Sentinel that must NOT make it into the consumer when this src is
// gated falsy: the rule writes no file at all in that case, so this
// header should be absent from the rule's TreeArtifact and the
// consumer's `__has_include` lookup should report 0.
#define GL_CONDITIONAL_HDRS_FALSY_SENTINEL 1
