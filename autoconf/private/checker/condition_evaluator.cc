#include "autoconf/private/checker/condition_evaluator.h"

#include <cctype>
#include <cstring>
#include <stdexcept>

namespace rules_cc_autoconf {

// ---------------------------------------------------------------------------
// ConditionParser
// ---------------------------------------------------------------------------

ConditionParser::ConditionParser(const std::string& input)
    : input_(input), src(input_.c_str()), pos(0), len(input_.size()) {}

void ConditionParser::ws() {
    while (pos < len && std::isspace(static_cast<unsigned char>(src[pos])))
        ++pos;
}

bool ConditionParser::at(const char* tok) const {
    size_t tlen = std::strlen(tok);
    return pos + tlen <= len && std::strncmp(src + pos, tok, tlen) == 0;
}

void ConditionParser::eat(const char* tok) {
    size_t tlen = std::strlen(tok);
    if (!at(tok)) {
        throw std::runtime_error(std::string("Expected '") + tok +
                                 "' at position " + std::to_string(pos));
    }
    pos += tlen;
    ws();
}

std::string ConditionParser::ident() {
    ws();
    size_t start = pos;
    if (pos < len && std::isdigit(static_cast<unsigned char>(src[pos]))) {
        throw std::runtime_error("Expected identifier at position " +
                                 std::to_string(pos) +
                                 " (identifiers cannot start with a digit)");
    }
    while (pos < len && (std::isalnum(static_cast<unsigned char>(src[pos])) ||
                         src[pos] == '_'))
        ++pos;
    if (pos == start) {
        throw std::runtime_error("Expected identifier at position " +
                                 std::to_string(pos));
    }
    std::string id(src + start, pos - start);
    ws();
    return id;
}

std::string ConditionParser::value() {
    ws();
    size_t start = pos;
    while (pos < len && !std::isspace(static_cast<unsigned char>(src[pos])) &&
           src[pos] != ')' && src[pos] != '&' && src[pos] != '|')
        ++pos;
    if (pos == start) {
        throw std::runtime_error("Expected value at position " +
                                 std::to_string(pos));
    }
    std::string v(src + start, pos - start);
    ws();
    return v;
}

Cond ConditionParser::atom() {
    std::string id = ident();
    Cond c;
    c.tag = Cond::Var;
    c.name = id;
    vars.push_back(id);

    if (at("<=")) {
        eat("<=");
        c.cmp_op = CmpOp::kLe;
        c.cmp_value = value();
    } else if (at(">=")) {
        eat(">=");
        c.cmp_op = CmpOp::kGe;
        c.cmp_value = value();
    } else if (at("!=")) {
        eat("!=");
        c.cmp_op = CmpOp::kNeq;
        c.cmp_value = value();
    } else if (at("==")) {
        eat("==");
        c.cmp_op = CmpOp::kEq;
        c.cmp_value = value();
    } else if (at("<")) {
        eat("<");
        c.cmp_op = CmpOp::kLt;
        c.cmp_value = value();
    } else if (at(">")) {
        eat(">");
        c.cmp_op = CmpOp::kGt;
        c.cmp_value = value();
    } else if (at("=")) {
        // Bare '=' treated as '==' for backward compatibility
        eat("=");
        c.cmp_op = CmpOp::kEq;
        c.cmp_value = value();
    }
    return c;
}

Cond ConditionParser::unary() {
    ws();
    if (at("!")) {
        eat("!");
        Cond inner = unary();
        Cond c;
        c.tag = Cond::Not;
        c.children.push_back(std::move(inner));
        return c;
    }
    if (at("(")) {
        eat("(");
        Cond c = parse_or();
        eat(")");
        return c;
    }
    return atom();
}

Cond ConditionParser::parse_and() {
    Cond left = unary();
    while (at("&&")) {
        eat("&&");
        Cond right = unary();
        Cond c;
        c.tag = Cond::And;
        c.children.push_back(std::move(left));
        c.children.push_back(std::move(right));
        left = std::move(c);
    }
    return left;
}

Cond ConditionParser::parse_or() {
    Cond left = parse_and();
    while (at("||")) {
        eat("||");
        Cond right = parse_and();
        Cond c;
        c.tag = Cond::Or;
        c.children.push_back(std::move(left));
        c.children.push_back(std::move(right));
        left = std::move(c);
    }
    return left;
}

Cond ConditionParser::parse() {
    ws();
    if (pos == len) {
        throw std::runtime_error("Empty condition expression");
    }
    Cond c = parse_or();
    ws();
    if (pos != len) {
        throw std::runtime_error("Unexpected trailing content at position " +
                                 std::to_string(pos) + ": '" +
                                 std::string(src + pos) + "'");
    }
    return c;
}

std::vector<std::string> ConditionParser::extract_variable_names(
    const std::string& expr) {
    ConditionParser p(expr);
    p.parse();
    return p.vars;
}

// ---------------------------------------------------------------------------
// eval_cond
// ---------------------------------------------------------------------------

namespace {

const CheckResult* find_result(
    const std::string& name,
    const std::map<std::string, CheckResult>& results) {
    auto it = results.find(name);
    if (it != results.end()) return &it->second;

    for (const auto& [key, result] : results) {
        if (result.define.has_value() && *result.define == name) return &result;
        if (result.subst.has_value() && *result.subst == name) return &result;
        if (result.name == name) return &result;
    }
    return nullptr;
}

std::string result_value(const CheckResult* r) {
    if (!r) return "";
    return r->value.value_or("");
}

bool compare_values(const std::string& actual, CmpOp op,
                    const std::string& expected) {
    switch (op) {
        case CmpOp::kEq:
            return actual == expected;
        case CmpOp::kNeq:
            return actual != expected;
        case CmpOp::kLt:
        case CmpOp::kGt:
        case CmpOp::kLe:
        case CmpOp::kGe: {
            int a = 0, b = 0;
            try {
                a = std::stoi(actual);
            } catch (...) {
            }
            try {
                b = std::stoi(expected);
            } catch (...) {
            }
            if (op == CmpOp::kLt) return a < b;
            if (op == CmpOp::kGt) return a > b;
            if (op == CmpOp::kLe) return a <= b;
            return a >= b;
        }
    }
    return false;
}

bool is_truthy(const CheckResult* r) {
    if (!r) return false;
    if (!r->success) return false;
    const std::string& v = r->value.value_or("");
    return !v.empty() && v != "0";
}

}  // namespace

bool eval_cond(const Cond& c,
               const std::map<std::string, CheckResult>& results) {
    switch (c.tag) {
        case Cond::Var: {
            const CheckResult* r = find_result(c.name, results);
            if (!c.cmp_value.empty()) {
                return compare_values(result_value(r), c.cmp_op, c.cmp_value);
            }
            return is_truthy(r);
        }
        case Cond::Not:
            return !eval_cond(c.children[0], results);
        case Cond::Or:
            return eval_cond(c.children[0], results) ||
                   eval_cond(c.children[1], results);
        case Cond::And:
            return eval_cond(c.children[0], results) &&
                   eval_cond(c.children[1], results);
    }
    return false;
}

// ---------------------------------------------------------------------------
// ConditionEvaluator (wrapper)
// ---------------------------------------------------------------------------

ConditionEvaluator::ConditionEvaluator(const std::string& condition_expr) {
    ConditionParser parser(condition_expr);
    tree_ = parser.parse();
    vars_ = parser.vars;
    first_var_ = vars_.empty() ? "" : vars_[0];
}

bool ConditionEvaluator::compute(
    const std::map<std::string, CheckResult>& dep_results) const {
    return eval_cond(tree_, dep_results);
}

std::vector<std::string> ConditionEvaluator::extract_variable_names(
    const std::string& expr) {
    return ConditionParser::extract_variable_names(expr);
}

}  // namespace rules_cc_autoconf
