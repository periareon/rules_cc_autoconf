/* This file is intentionally not valid C when compiled normally.
 * It should be completely hidden by the autoconf_srcs wrapper when
 * HAVE_BAD_SRC is not set.
 */

int use_incompatible_feature(void) {
    return unknown_symbol() + another_undefined_symbol;
}
