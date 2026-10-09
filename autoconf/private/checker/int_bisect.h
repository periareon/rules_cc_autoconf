#pragma once

#include <functional>
#include <string>
#include <utility>

namespace rules_cc_autoconf {

/**
 * @brief Predicate reporting whether a probe program compiles.
 *
 * The bisection never looks at compiler output; it only needs to know
 * whether a given probe is accepted. Production code wires this to the
 * configured toolchain, tests can substitute a stand-in.
 */
using CompileProbe = std::function<bool(const std::string& code)>;

/**
 * @brief Split a bisect template into its C code and the probed expression.
 *
 * Templates (see `_AC_COMPUTE_INT_TEMPLATE` in `//autoconf:checks.bzl`) carry
 * the expression to evaluate inside a `{$EXPR}` marker. Anchoring on `{$`
 * rather than any `{` lets templates freely emit `struct {...};` or C99
 * designated initializers before the marker (needed by AC_CHECK_ALIGNOF).
 *
 * @param base_code_template Template containing exactly one `{$EXPR}` marker.
 * @return The template with the marker removed, and the expression text.
 * @throws std::runtime_error if the marker is missing or empty.
 */
std::pair<std::string, std::string> split_code_expr(
    const std::string& base_code_template);

/**
 * @brief Instantiate the `{lhs} < {rhs}` probe of a bisect template.
 *
 * @param code_template Template (marker already removed) containing `{lhs}`
 *                      and `{rhs}` placeholders.
 * @param lhs Replacement for every `{lhs}`.
 * @param rhs Replacement for every `{rhs}`.
 * @return The probe source.
 * @throws std::runtime_error if either placeholder is missing.
 */
std::string gen_less_compare(const std::string& code_template,
                             const std::string& lhs, const std::string& rhs);

/**
 * @brief Find the value of a compile-time integer expression by bisection.
 *
 * Each probe compiles iff `lhs < rhs` holds for its instantiation, so the
 * value can be located with O(log n) compilations without running anything
 * (cross-compile safe). Mirrors autoconf's `AC_COMPUTE_INT`.
 *
 * @param base_code_template Template with a `{$EXPR}` marker and
 *                           `{lhs} < {rhs}` probe.
 * @param search_begin Inclusive lower bound of the search range.
 * @param search_end Inclusive upper bound of the search range.
 * @param compiles Predicate deciding whether a probe compiles.
 * @return The value of the expression.
 * @throws std::runtime_error if the expression lies outside
 *         `[search_begin, search_end]`, or cannot be evaluated at compile
 *         time (unknown identifier, invalid expression, runtime call, ...).
 */
int bisect_compile_time_int(const std::string& base_code_template,
                            int search_begin, int search_end,
                            const CompileProbe& compiles);

}  // namespace rules_cc_autoconf
