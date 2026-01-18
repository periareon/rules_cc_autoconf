#include "autoconf/private/checker/check_result.h"

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
    
    CheckResult result(define_name, value, success, is_define, is_subst);
    return result;
}

}
