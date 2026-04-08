#include "autoconf/private/checker/condition_evaluator.h"

#include <cassert>
#include <iostream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

using namespace rules_cc_autoconf;

namespace {

// Helpers to build CheckResult values concisely.
CheckResult make_result(const std::string& name, const std::string& value,
                        bool success) {
    return CheckResult(name, value, success);
}

// ---------------------------------------------------------------------------
// Parse tests
// ---------------------------------------------------------------------------

void test_parse_simple_var() {
    ConditionParser p("FOO");
    Cond c = p.parse();
    assert(c.tag == Cond::Var);
    assert(c.name == "FOO");
    assert(c.cmp_value.empty());
}

void test_parse_negation() {
    ConditionParser p("!FOO");
    Cond c = p.parse();
    assert(c.tag == Cond::Not);
    assert(c.children.size() == 1);
    assert(c.children[0].tag == Cond::Var);
    assert(c.children[0].name == "FOO");
}

void test_parse_double_negation() {
    ConditionParser p("!!FOO");
    Cond c = p.parse();
    assert(c.tag == Cond::Not);
    assert(c.children[0].tag == Cond::Not);
    assert(c.children[0].children[0].tag == Cond::Var);
    assert(c.children[0].children[0].name == "FOO");
}

void test_parse_equality() {
    ConditionParser p("FOO==1");
    Cond c = p.parse();
    assert(c.tag == Cond::Var);
    assert(c.name == "FOO");
    assert(c.cmp_op == CmpOp::kEq);
    assert(c.cmp_value == "1");
}

void test_parse_inequality() {
    ConditionParser p("FOO!=0");
    Cond c = p.parse();
    assert(c.tag == Cond::Var);
    assert(c.name == "FOO");
    assert(c.cmp_op == CmpOp::kNeq);
    assert(c.cmp_value == "0");
}

void test_parse_relational() {
    {
        ConditionParser p("FOO<32");
        Cond c = p.parse();
        assert(c.cmp_op == CmpOp::kLt);
        assert(c.cmp_value == "32");
    }
    {
        ConditionParser p("FOO>=10");
        Cond c = p.parse();
        assert(c.cmp_op == CmpOp::kGe);
        assert(c.cmp_value == "10");
    }
    {
        ConditionParser p("FOO<=5");
        Cond c = p.parse();
        assert(c.cmp_op == CmpOp::kLe);
        assert(c.cmp_value == "5");
    }
    {
        ConditionParser p("FOO>0");
        Cond c = p.parse();
        assert(c.cmp_op == CmpOp::kGt);
        assert(c.cmp_value == "0");
    }
}

void test_parse_or() {
    ConditionParser p("A || B");
    Cond c = p.parse();
    assert(c.tag == Cond::Or);
    assert(c.children[0].name == "A");
    assert(c.children[1].name == "B");
}

void test_parse_and() {
    ConditionParser p("A && B");
    Cond c = p.parse();
    assert(c.tag == Cond::And);
    assert(c.children[0].name == "A");
    assert(c.children[1].name == "B");
}

void test_parse_multi_or() {
    ConditionParser p("A || B || C");
    Cond c = p.parse();
    // Left-associative: (A || B) || C
    assert(c.tag == Cond::Or);
    assert(c.children[0].tag == Cond::Or);
    assert(c.children[1].name == "C");
}

void test_parse_multi_and() {
    ConditionParser p("A && B && C");
    Cond c = p.parse();
    assert(c.tag == Cond::And);
    assert(c.children[0].tag == Cond::And);
    assert(c.children[1].name == "C");
}

void test_parse_precedence() {
    // && binds tighter than ||
    ConditionParser p("A || B && C");
    Cond c = p.parse();
    assert(c.tag == Cond::Or);
    assert(c.children[0].name == "A");
    assert(c.children[1].tag == Cond::And);
}

void test_parse_parens_override() {
    ConditionParser p("(A || B) && C");
    Cond c = p.parse();
    assert(c.tag == Cond::And);
    assert(c.children[0].tag == Cond::Or);
    assert(c.children[1].name == "C");
}

void test_parse_negated_group() {
    ConditionParser p("!(A || B)");
    Cond c = p.parse();
    assert(c.tag == Cond::Not);
    assert(c.children[0].tag == Cond::Or);
}

void test_parse_gnulib_pattern_1() {
    // "HAVE_X==0 || REPLACE_X"
    ConditionParser p("HAVE_X==0 || REPLACE_X");
    Cond c = p.parse();
    assert(c.tag == Cond::Or);
    assert(c.children[0].tag == Cond::Var);
    assert(c.children[0].name == "HAVE_X");
    assert(c.children[0].cmp_op == CmpOp::kEq);
    assert(c.children[0].cmp_value == "0");
    assert(c.children[1].name == "REPLACE_X");
}

void test_parse_gnulib_pattern_2() {
    // "!(HAVE_ISWCNTRL==0 || REPLACE_ISWCNTRL) && REPLACE_ISWDIGIT"
    ConditionParser p(
        "!(HAVE_ISWCNTRL==0 || REPLACE_ISWCNTRL) && REPLACE_ISWDIGIT");
    Cond c = p.parse();
    assert(c.tag == Cond::And);
    assert(c.children[0].tag == Cond::Not);
    assert(c.children[0].children[0].tag == Cond::Or);
    assert(c.children[1].name == "REPLACE_ISWDIGIT");
}

void test_parse_gnulib_pattern_3() {
    // "ac_cv_func_getmntent || ac_cv_func_getmntinfo"
    ConditionParser p("ac_cv_func_getmntent || ac_cv_func_getmntinfo");
    Cond c = p.parse();
    assert(c.tag == Cond::Or);
    assert(c.children[0].name == "ac_cv_func_getmntent");
    assert(c.children[1].name == "ac_cv_func_getmntinfo");
}

// ---------------------------------------------------------------------------
// Parse error tests
// ---------------------------------------------------------------------------

void test_parse_error_empty() {
    try {
        ConditionParser p("");
        p.parse();
        assert(false && "Expected exception for empty expression");
    } catch (const std::runtime_error&) {
    }
}

void test_parse_error_trailing() {
    try {
        ConditionParser p("A B");
        p.parse();
        assert(false && "Expected exception for trailing content");
    } catch (const std::runtime_error&) {
    }
}

void test_parse_error_missing_paren() {
    try {
        ConditionParser p("(A || B");
        p.parse();
        assert(false && "Expected exception for missing paren");
    } catch (const std::runtime_error&) {
    }
}

void test_parse_error_dangling_op() {
    try {
        ConditionParser p("A ||");
        p.parse();
        assert(false && "Expected exception for dangling operator");
    } catch (const std::runtime_error&) {
    }
}

void test_parse_error_leading_digit() {
    try {
        ConditionParser p("123abc");
        p.parse();
        assert(false && "Expected exception for leading digit");
    } catch (const std::runtime_error&) {
    }
}

// ---------------------------------------------------------------------------
// Eval tests
// ---------------------------------------------------------------------------

void test_eval_truthy() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "1", true));
    ConditionEvaluator e("FOO");
    assert(e.compute(results) == true);
}

void test_eval_falsy_zero() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "0", true));
    ConditionEvaluator e("FOO");
    assert(e.compute(results) == false);
}

void test_eval_falsy_not_success() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "1", false));
    ConditionEvaluator e("FOO");
    assert(e.compute(results) == false);
}

void test_eval_falsy_missing() {
    std::map<std::string, CheckResult> results;
    ConditionEvaluator e("FOO");
    assert(e.compute(results) == false);
}

void test_eval_negation() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "0", true));
    ConditionEvaluator e("!FOO");
    assert(e.compute(results) == true);
}

void test_eval_eq_match() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "1", true));
    ConditionEvaluator e("FOO==1");
    assert(e.compute(results) == true);
}

void test_eval_eq_no_match() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "0", true));
    ConditionEvaluator e("FOO==1");
    assert(e.compute(results) == false);
}

void test_eval_neq_match() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "0", true));
    ConditionEvaluator e("FOO!=1");
    assert(e.compute(results) == true);
}

void test_eval_neq_no_match() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "1", true));
    ConditionEvaluator e("FOO!=1");
    assert(e.compute(results) == false);
}

void test_eval_relational() {
    std::map<std::string, CheckResult> results;
    results.emplace("FOO", make_result("FOO", "5", true));
    assert(ConditionEvaluator("FOO<10").compute(results) == true);
    assert(ConditionEvaluator("FOO>3").compute(results) == true);
    assert(ConditionEvaluator("FOO<=5").compute(results) == true);
    assert(ConditionEvaluator("FOO>=6").compute(results) == false);
}

void test_eval_or() {
    std::map<std::string, CheckResult> results;
    results.emplace("A", make_result("A", "0", true));
    results.emplace("B", make_result("B", "1", true));
    ConditionEvaluator e("A || B");
    assert(e.compute(results) == true);
}

void test_eval_and() {
    std::map<std::string, CheckResult> results;
    results.emplace("A", make_result("A", "1", true));
    results.emplace("B", make_result("B", "0", true));
    ConditionEvaluator e("A && B");
    assert(e.compute(results) == false);
}

void test_eval_and_both_true() {
    std::map<std::string, CheckResult> results;
    results.emplace("A", make_result("A", "1", true));
    results.emplace("B", make_result("B", "1", true));
    ConditionEvaluator e("A && B");
    assert(e.compute(results) == true);
}

void test_eval_negated_group() {
    std::map<std::string, CheckResult> results;
    results.emplace("HAVE_X", make_result("HAVE_X", "0", true));
    results.emplace("REPLACE_X", make_result("REPLACE_X", "1", true));
    results.emplace("REPLACE_Y", make_result("REPLACE_Y", "1", true));

    // !(HAVE_X==0 || REPLACE_X) && REPLACE_Y
    // HAVE_X==0 is true, so (true || true) is true, !true = false
    // false && true = false
    ConditionEvaluator e("!(HAVE_X==0 || REPLACE_X) && REPLACE_Y");
    assert(e.compute(results) == false);
}

void test_eval_gnulib_or_pattern() {
    // "HAVE_X==0 || REPLACE_X"  with HAVE_X=1, REPLACE_X=0
    // 1==0 -> false, REPLACE_X is "0" -> falsy: false || false = false
    std::map<std::string, CheckResult> results;
    results.emplace("HAVE_X", make_result("HAVE_X", "1", true));
    results.emplace("REPLACE_X", make_result("REPLACE_X", "0", true));
    ConditionEvaluator e("HAVE_X==0 || REPLACE_X");
    assert(e.compute(results) == false);

    // HAVE_X=0: 0==0 -> true, result = true
    results.erase("HAVE_X");
    results.emplace("HAVE_X", make_result("HAVE_X", "0", true));
    assert(e.compute(results) == true);
}

// ---------------------------------------------------------------------------
// extract_variable_names tests
// ---------------------------------------------------------------------------

void test_extract_simple() {
    auto vars = ConditionEvaluator::extract_variable_names("FOO");
    assert(vars.size() == 1);
    assert(vars[0] == "FOO");
}

void test_extract_negation() {
    auto vars = ConditionEvaluator::extract_variable_names("!FOO");
    assert(vars.size() == 1);
    assert(vars[0] == "FOO");
}

void test_extract_compound() {
    auto vars = ConditionEvaluator::extract_variable_names(
        "!(HAVE_ISWCNTRL==0 || REPLACE_ISWCNTRL) && REPLACE_ISWDIGIT");
    assert(vars.size() == 3);
    assert(vars[0] == "HAVE_ISWCNTRL");
    assert(vars[1] == "REPLACE_ISWCNTRL");
    assert(vars[2] == "REPLACE_ISWDIGIT");
}

void test_extract_or() {
    auto vars = ConditionEvaluator::extract_variable_names("A || B");
    assert(vars.size() == 2);
    assert(vars[0] == "A");
    assert(vars[1] == "B");
}

void test_extract_with_comparison() {
    auto vars = ConditionEvaluator::extract_variable_names("FOO==0");
    assert(vars.size() == 1);
    assert(vars[0] == "FOO");
}

// ---------------------------------------------------------------------------
// ConditionEvaluator wrapper tests
// ---------------------------------------------------------------------------

void test_evaluator_define_name() {
    ConditionEvaluator e("A || B");
    assert(e.define_name() == "A");
    assert(e.all_define_names().size() == 2);
    assert(e.all_define_names()[0] == "A");
    assert(e.all_define_names()[1] == "B");
}

void test_evaluator_lookup_by_define_field() {
    // Result keyed by cache variable but with define field set
    std::map<std::string, CheckResult> results;
    CheckResult r("ac_cv_func_foo", "1", true, true, false,
                  CheckType::kFunction, std::string("HAVE_FOO"));
    results.emplace("ac_cv_func_foo", r);

    ConditionEvaluator e("HAVE_FOO");
    assert(e.compute(results) == true);
}

}  // namespace

#define RUN(fn)                             \
    do {                                    \
        std::cout << "  " #fn << std::endl; \
        fn();                               \
    } while (0)

int main() {
    std::cout << "Parse tests:\n";
    RUN(test_parse_simple_var);
    RUN(test_parse_negation);
    RUN(test_parse_double_negation);
    RUN(test_parse_equality);
    RUN(test_parse_inequality);
    RUN(test_parse_relational);
    RUN(test_parse_or);
    RUN(test_parse_and);
    RUN(test_parse_multi_or);
    RUN(test_parse_multi_and);
    RUN(test_parse_precedence);
    RUN(test_parse_parens_override);
    RUN(test_parse_negated_group);
    RUN(test_parse_gnulib_pattern_1);
    RUN(test_parse_gnulib_pattern_2);
    RUN(test_parse_gnulib_pattern_3);

    std::cout << "\nParse error tests:\n";
    RUN(test_parse_error_empty);
    RUN(test_parse_error_trailing);
    RUN(test_parse_error_missing_paren);
    RUN(test_parse_error_dangling_op);
    RUN(test_parse_error_leading_digit);

    std::cout << "\nEval tests:\n";
    RUN(test_eval_truthy);
    RUN(test_eval_falsy_zero);
    RUN(test_eval_falsy_not_success);
    RUN(test_eval_falsy_missing);
    RUN(test_eval_negation);
    RUN(test_eval_eq_match);
    RUN(test_eval_eq_no_match);
    RUN(test_eval_neq_match);
    RUN(test_eval_neq_no_match);
    RUN(test_eval_relational);
    RUN(test_eval_or);
    RUN(test_eval_and);
    RUN(test_eval_and_both_true);
    RUN(test_eval_negated_group);
    RUN(test_eval_gnulib_or_pattern);

    std::cout << "\nExtract variable names tests:\n";
    RUN(test_extract_simple);
    RUN(test_extract_negation);
    RUN(test_extract_compound);
    RUN(test_extract_or);
    RUN(test_extract_with_comparison);

    std::cout << "\nWrapper tests:\n";
    RUN(test_evaluator_define_name);
    RUN(test_evaluator_lookup_by_define_field);

    std::cout << "\nAll tests passed.\n";
    return 0;
}
