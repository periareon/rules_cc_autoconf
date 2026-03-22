#include "autoconf/private/checker/check_result.h"

#include <algorithm>
#include <cctype>

#include "autoconf/private/checker/check.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

std::optional<CheckResult> CheckResult::from_json(const std::string& name,
                                                  const void* json_value_ptr) {
    const nlohmann::json* json_value =
        static_cast<const nlohmann::json*>(json_value_ptr);

    if (!json_value->is_object() || !json_value->contains("success") ||
        !(*json_value)["success"].is_boolean()) {
        return std::nullopt;
    }

    bool success = (*json_value)["success"].get<bool>();

    // Backward compatibility: check for legacy "has_value" field.
    // In the old format, empty/null values used a separate bool to distinguish
    // "explicitly empty" from "not provided". In the new format, null = not
    // provided, empty string = explicitly empty.
    bool has_legacy_has_value = json_value->contains("has_value") &&
                                (*json_value)["has_value"].is_boolean();
    bool legacy_has_value =
        has_legacy_has_value && (*json_value)["has_value"].get<bool>();

    std::optional<std::string> value;
    if (json_value->contains("value")) {
        const nlohmann::json& value_json = (*json_value)["value"];

        // Keep value as JSON-encoded string to preserve type information
        // When reading from JSON file:
        // - If value_json is a string, it might be an already JSON-encoded
        // string
        //   from a previous write. Try to parse it as JSON first to avoid
        //   double-encoding.
        // - For numbers/booleans, dump() gives us the JSON representation
        // - For null, treat as no value (nullopt)
        if (value_json.is_null()) {
            // Null in JSON: no value, unless legacy has_value says otherwise
            value = legacy_has_value ? std::optional<std::string>("")
                                     : std::nullopt;
        } else if (value_json.is_string()) {
            // String value: always JSON-encode to preserve string type
            // This ensures "1" stays as "\"1\"" (JSON string), not 1 (JSON
            // number)
            std::string str_val = value_json.get<std::string>();
            if (str_val.empty()) {
                if (has_legacy_has_value) {
                    // Legacy format: use has_value to determine meaning
                    value = legacy_has_value ? std::optional<std::string>("")
                                             : std::nullopt;
                } else {
                    // New format: empty string in JSON = explicitly set to
                    // empty
                    value = std::optional<std::string>("");
                }
            } else {
                // Always use dump() to JSON-encode the string value
                // This preserves the fact that it's a string, not a number
                value = value_json.dump();
            }
        } else {
            // For numbers, booleans, and other types, use dump() to get
            // JSON-encoded string
            value = value_json.dump();
        }
    } else {
        value = std::nullopt;
    }

    // Flat result format: only success, value, type are in the JSON.
    // Consumer metadata (is_define, is_subst, define, subst, unquote) is
    // tracked in Starlark providers and supplied via the manifest at render
    // time. Defaults here are safe for the checker's own dep-loading path.
    bool is_define = false;
    bool is_subst = false;
    std::optional<std::string> define_name;
    std::optional<std::string> subst_name;
    bool unquote = false;

    CheckType type = CheckType::kDefine;
    if (json_value->contains("type") && (*json_value)["type"].is_string()) {
        std::string type_str = (*json_value)["type"].get<std::string>();
        if (type_str == "function") {
            type = CheckType::kFunction;
        } else if (type_str == "lib") {
            type = CheckType::kLib;
        } else if (type_str == "type") {
            type = CheckType::kType;
        } else if (type_str == "compile") {
            type = CheckType::kCompile;
        } else if (type_str == "link") {
            type = CheckType::kLink;
        } else if (type_str == "define") {
            type = CheckType::kDefine;
        } else if (type_str == "subst") {
            type = CheckType::kM4Variable;
        } else if (type_str == "m4_variable") {
            type = CheckType::kM4Variable;
        } else if (type_str == "sizeof") {
            type = CheckType::kSizeof;
        } else if (type_str == "alignof") {
            type = CheckType::kAlignof;
        } else if (type_str == "compute_int") {
            type = CheckType::kComputeInt;
        } else if (type_str == "decl") {
            type = CheckType::kDecl;
        } else if (type_str == "member") {
            type = CheckType::kMember;
        } else if (type_str == "GL_NEXT_HEADER") {
            type = CheckType::kGlNextHeader;
        }
    }

    CheckResult result(name, value, success, is_define, is_subst, type,
                       define_name, subst_name, unquote);
    return result;
}

}  // namespace rules_cc_autoconf
