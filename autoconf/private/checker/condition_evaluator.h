#pragma once

#include <map>
#include <stdexcept>
#include <string>
#include <vector>

#include "autoconf/private/checker/check_result.h"

namespace rules_cc_autoconf {

enum class CmpOp { kEq, kNeq, kLt, kGt, kLe, kGe };

struct Cond {
    enum Tag { Var, Not, Or, And };

    Tag tag = Var;
    std::string name;
    CmpOp cmp_op = CmpOp::kEq;
    std::string cmp_value;
    std::vector<Cond> children;
};

struct ConditionParser {
    std::string input_;
    const char* src = nullptr;
    size_t pos = 0;
    size_t len = 0;
    std::vector<std::string> vars;

    explicit ConditionParser(const std::string& input);

    Cond parse();

    static std::vector<std::string> extract_variable_names(
        const std::string& expr);

   private:
    void ws();
    bool at(const char* tok) const;
    void eat(const char* tok);
    std::string ident();
    std::string value();
    Cond atom();
    Cond unary();
    Cond parse_and();
    Cond parse_or();
};

bool eval_cond(const Cond& c,
               const std::map<std::string, CheckResult>& results);

/**
 * Slim wrapper around ConditionParser + eval_cond for backward compatibility
 * with checker.cc's existing call sites.
 */
class ConditionEvaluator {
   public:
    explicit ConditionEvaluator(const std::string& condition_expr);

    bool compute(const std::map<std::string, CheckResult>& dep_results) const;

    const std::string& define_name() const { return first_var_; }

    const std::vector<std::string>& all_define_names() const { return vars_; }

    static std::vector<std::string> extract_variable_names(
        const std::string& expr);

   private:
    Cond tree_;
    std::string first_var_;
    std::vector<std::string> vars_;
};

}  // namespace rules_cc_autoconf
