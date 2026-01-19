#include "autoconf/private/checker/check_result.h"

#include "autoconf/private/checker/check.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

std::optional<CheckResult> CheckResult::from_json(const std::string& define_name, const void* json_value_ptr) {
    const nlohmann::json* json_value = static_cast<const nlohmann::json*>(json_value_ptr);
    
    if (!json_value->is_object() || !json_value->contains("success") || !(*json_value)["success"].is_boolean()) {
        return std::nullopt;
    }
    
    bool success = (*json_value)["success"].get<bool>();
    std::string value =
        json_value->contains("value") && (*json_value)["value"].is_string()
            ? (*json_value)["value"].get<std::string>()
            : "";
    
    // Check for is_define, define_flag, or define for backward compatibility
    bool is_define = false;
    if (json_value->contains("is_define") && (*json_value)["is_define"].is_boolean()) {
        is_define = (*json_value)["is_define"].get<bool>();
    } else if (json_value->contains("define_flag") && (*json_value)["define_flag"].is_boolean()) {
        is_define = (*json_value)["define_flag"].get<bool>();
    } else if (json_value->contains("define") && (*json_value)["define"].is_boolean()) {
        is_define = (*json_value)["define"].get<bool>();
    }
    
    // Check for is_subst, subst_flag, or subst for backward compatibility
    bool is_subst = false;
    if (json_value->contains("is_subst") && (*json_value)["is_subst"].is_boolean()) {
        is_subst = (*json_value)["is_subst"].get<bool>();
    } else if (json_value->contains("subst_flag") && (*json_value)["subst_flag"].is_boolean()) {
        is_subst = (*json_value)["subst_flag"].get<bool>();
    } else if (json_value->contains("subst") && (*json_value)["subst"].is_boolean()) {
        is_subst = (*json_value)["subst"].get<bool>();
    }
    
    // Check for type (defaults to kDefine for backward compatibility)
    CheckType type = CheckType::kDefine;
    if (json_value->contains("type") && (*json_value)["type"].is_string()) {
        std::string type_str = (*json_value)["type"].get<std::string>();
        // Parse type string to CheckType enum
        if (type_str == "header") {
            type = CheckType::kHeader;
        } else if (type_str == "function") {
            type = CheckType::kFunction;
        } else if (type_str == "lib") {
            type = CheckType::kLib;
        } else if (type_str == "symbol") {
            type = CheckType::kSymbol;
        } else if (type_str == "type") {
            type = CheckType::kType;
        } else if (type_str == "compile") {
            type = CheckType::kCompile;
        } else if (type_str == "link") {
            type = CheckType::kLink;
        } else if (type_str == "define") {
            type = CheckType::kDefine;
        } else if (type_str == "subst") {
            type = CheckType::kSubst;
        } else if (type_str == "m4_define") {
            type = CheckType::kM4Define;
        } else if (type_str == "sizeof") {
            type = CheckType::kSizeof;
        } else if (type_str == "alignof") {
            type = CheckType::kAlignof;
        } else if (type_str == "compute_int") {
            type = CheckType::kComputeInt;
        } else if (type_str == "endian") {
            type = CheckType::kEndian;
        } else if (type_str == "decl") {
            type = CheckType::kDecl;
        } else if (type_str == "member") {
            type = CheckType::kMember;
        }
    }
    
    CheckResult result(define_name, value, success, is_define, is_subst, type);
    return result;
}

}
