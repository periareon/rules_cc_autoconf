// Unit tests for the AC_COMPUTE_INT bisection.
//
// These replace the former `autoconf/tests/integration/negative_compute_int`
// workspace, which asserted that an `autoconf` target fails to build for
// out-of-range or non-evaluable expressions. The failure originates here, in
// `bisect_compile_time_int` throwing, so the same cases are exercised against
// a stand-in compiler that behaves like a real one on constant expressions.

#include "autoconf/private/checker/int_bisect.h"

#include <iostream>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

using rules_cc_autoconf::bisect_compile_time_int;
using rules_cc_autoconf::gen_less_compare;
using rules_cc_autoconf::split_code_expr;

static int test_count = 0;
static int pass_count = 0;

#define TEST(name)                          \
    std::cout << "  " << #name << "... ";   \
    test_count++;                           \
    if (test_##name()) {                    \
        std::cout << "PASSED" << std::endl; \
        pass_count++;                       \
    } else {                                \
        std::cout << "FAILED" << std::endl; \
    }

namespace {

// Default AC_COMPUTE_INT search range (see CheckRunner::check_compute_int).
constexpr int kSearchBegin = -1024;
constexpr int kSearchEnd = 1024;

/**
 * Reproduce `_AC_COMPUTE_INT_TEMPLATE.format(preamble, expr)` from
 * //autoconf:checks.bzl so the probes have the real shape.
 */
std::string compute_int_template(const std::string& expr,
                                 const std::string& preamble = "") {
    return "\n" + preamble + "\n{$" + expr +
           "}\n"
           "#ifdef _MSC_VER\n"
           "int main(void) {\n"
           "    switch(0) { case 0: break; case ({lhs} < {rhs}): break; }\n"
           "    return 0;\n"
           "}\n"
           "#else\n"
           "typedef int _array_with_length[{lhs} < {rhs} ? 1 : -1];\n"
           "int main(void) {\n"
           "    return 0;\n"
           "}\n"
           "#endif\n";
}

/**
 * Stand-in for the C compiler.
 *
 * Knows the value of a handful of compile-time constants. Anything else that
 * is not an integer literal is a compile error, which is how a real compiler
 * treats an undeclared identifier, a syntax error, or a runtime call inside a
 * constant expression. A probe "compiles" iff `lhs < rhs` holds, matching the
 * negative-array-size / duplicate-case trick in the template.
 */
class FakeCompiler {
   public:
    explicit FakeCompiler(std::map<std::string, int> constants)
        : constants_(std::move(constants)) {}

    bool operator()(const std::string& code) const {
        const std::string open = "_array_with_length[";
        const std::string close = " ? 1 : -1]";
        const size_t start = code.find(open);
        if (start == std::string::npos) return false;
        const size_t expr_begin = start + open.size();
        const size_t expr_end = code.find(close, expr_begin);
        if (expr_end == std::string::npos) return false;

        const std::string compare =
            code.substr(expr_begin, expr_end - expr_begin);
        const size_t lt = compare.find(" < ");
        if (lt == std::string::npos) return false;

        std::optional<int> lhs = evaluate(compare.substr(0, lt));
        std::optional<int> rhs = evaluate(compare.substr(lt + 3));
        if (!lhs.has_value() || !rhs.has_value()) return false;
        return *lhs < *rhs;
    }

   private:
    std::optional<int> evaluate(const std::string& token) const {
        auto it = constants_.find(token);
        if (it != constants_.end()) return it->second;

        // Integer literal, optionally negative.
        if (token.empty()) return std::nullopt;
        size_t i = token[0] == '-' ? 1 : 0;
        if (i == token.size()) return std::nullopt;
        for (; i < token.size(); ++i) {
            if (token[i] < '0' || token[i] > '9') return std::nullopt;
        }
        return std::stoi(token);
    }

    std::map<std::string, int> constants_;
};

int compute(const std::string& expr, const FakeCompiler& cc,
            int begin = kSearchBegin, int end = kSearchEnd) {
    return bisect_compile_time_int(compute_int_template(expr), begin, end, cc);
}

/** True iff `fn()` throws std::runtime_error whose message contains needle. */
template <typename F>
bool throws_with(F&& fn, const std::string& needle) {
    try {
        fn();
    } catch (const std::runtime_error& ex) {
        return std::string(ex.what()).find(needle) != std::string::npos;
    }
    return false;
}

const char* const kOutOfRange = "outside the search range";
const char* const kNotEvaluable = "can't be evaluated";

}  // namespace

// --- Negative cases (formerly negative_compute_int) -----------------------

static bool test_underflow_rejected() {
    FakeCompiler cc({});
    return throws_with([&] { compute("-1025", cc); }, kOutOfRange);
}

static bool test_overflow_rejected() {
    FakeCompiler cc({});
    return throws_with([&] { compute("1025", cc); }, kOutOfRange);
}

static bool test_non_exist_constant_rejected() {
    FakeCompiler cc({});
    return throws_with([&] { compute("NOT_DEFINED_CONSTANT", cc); },
                       kNotEvaluable);
}

static bool test_invalid_expr_rejected() {
    FakeCompiler cc({});
    return throws_with([&] { compute("int b=3", cc); }, kNotEvaluable);
}

static bool test_runtime_eval_rejected() {
    FakeCompiler cc({});
    return throws_with([&] { compute("rand()", cc); }, kNotEvaluable);
}

static bool test_known_constant_out_of_range_rejected() {
    // A valid constant whose value is beyond the range must also fail rather
    // than clamp to the boundary.
    FakeCompiler cc({{"BIG", 5000}, {"SMALL", -5000}});
    return throws_with([&] { compute("BIG", cc); }, kOutOfRange) &&
           throws_with([&] { compute("SMALL", cc); }, kOutOfRange);
}

// --- Positive cases --------------------------------------------------------

static bool test_literals_found() {
    FakeCompiler cc({});
    for (int v :
         {-1024, -1023, -513, -42, -1, 0, 1, 42, 100, 513, 1023, 1024}) {
        if (compute(std::to_string(v), cc) != v) return false;
    }
    return true;
}

static bool test_named_constants_found() {
    FakeCompiler cc({{"ANSWER", 42}, {"1 + 1", 2}, {"NEGATIVE", -333}});
    return compute("ANSWER", cc) == 42 && compute("1 + 1", cc) == 2 &&
           compute("NEGATIVE", cc) == -333;
}

static bool test_custom_ranges() {
    // sizeof-style [0, 65536] and alignof-style [1, 4096] ranges.
    FakeCompiler cc({{"sizeof(long)", 8}, {"ALIGN", 16}, {"HUGE", 65536}});
    return compute("sizeof(long)", cc, 0, 65536) == 8 &&
           compute("ALIGN", cc, 1, 4096) == 16 &&
           compute("HUGE", cc, 0, 65536) == 65536 &&
           compute("0", cc, 0, 65536) == 0 &&
           throws_with([&] { compute("sizeof(long)", cc, 16, 32); },
                       kOutOfRange);
}

static bool test_preamble_preserved_in_probes() {
    // Header code placed before the marker must reach every probe, and the
    // marker itself must not.
    bool probes_ok = true;
    const FakeCompiler fake({{"RAND_MAX_ISH", 7}});
    auto cc = [&](const std::string& code) {
        if (code.find("#include <stdlib.h>") == std::string::npos ||
            code.find("{$") != std::string::npos) {
            probes_ok = false;
        }
        return fake(code);
    };
    int value = bisect_compile_time_int(
        compute_int_template("RAND_MAX_ISH", "#include <stdlib.h>"),
        kSearchBegin, kSearchEnd, cc);
    return value == 7 && probes_ok;
}

// --- Template plumbing -----------------------------------------------------

static bool test_split_code_expr() {
    auto split = split_code_expr("struct {int a;} s;\n{$sizeof(s)}\n{lhs}");
    return split.first == "struct {int a;} s;\n\n{lhs}" &&
           split.second == "sizeof(s)";
}

static bool test_split_code_expr_rejects_missing_marker() {
    const char* needle = "{$EXPR}";
    return throws_with([] { split_code_expr("{lhs} < {rhs}"); }, needle) &&
           throws_with([] { split_code_expr("{$}"); }, needle) &&
           throws_with([] { split_code_expr("{$unterminated"); }, needle);
}

static bool test_gen_less_compare_rejects_missing_placeholders() {
    return gen_less_compare("{lhs} < {rhs}", "a", "b") == "a < b" &&
           throws_with([] { gen_less_compare("{lhs} < x", "a", "b"); },
                       "{rhs}") &&
           throws_with([] { gen_less_compare("x < {rhs}", "a", "b"); },
                       "{lhs}");
}

int main() {
    std::cout << "int_bisect_test:" << std::endl;
    TEST(underflow_rejected)
    TEST(overflow_rejected)
    TEST(non_exist_constant_rejected)
    TEST(invalid_expr_rejected)
    TEST(runtime_eval_rejected)
    TEST(known_constant_out_of_range_rejected)
    TEST(literals_found)
    TEST(named_constants_found)
    TEST(custom_ranges)
    TEST(preamble_preserved_in_probes)
    TEST(split_code_expr)
    TEST(split_code_expr_rejects_missing_marker)
    TEST(gen_less_compare_rejects_missing_placeholders)

    std::cout << std::endl
              << pass_count << "/" << test_count << " tests passed."
              << std::endl;
    return pass_count == test_count ? 0 : 1;
}
