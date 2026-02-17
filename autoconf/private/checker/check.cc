#include "autoconf/private/checker/check.h"

#include "autoconf/private/checker/debug_logger.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

std::string check_type_to_string(CheckType type) {
    switch (type) {
        case CheckType::kFunction:
            return "function";
        case CheckType::kLib:
            return "lib";
        case CheckType::kType:
            return "type";
        case CheckType::kCompile:
            return "compile";
        case CheckType::kLink:
            return "link";
        case CheckType::kDefine:
            return "define";
        case CheckType::kM4Variable:
            return "m4_variable";
        case CheckType::kSizeof:
            return "sizeof";
        case CheckType::kAlignof:
            return "alignof";
        case CheckType::kComputeInt:
            return "compute_int";
        case CheckType::kEndian:
            return "endian";
        case CheckType::kDecl:
            return "decl";
        case CheckType::kMember:
            return "member";
        default:
            return "unknown";
    }
}

bool check_type_is_define(CheckType type) {
    // All CheckTypes are defines except kM4Variable
    return type != CheckType::kM4Variable;
}

std::optional<Check> Check::from_json(const void* json_data) {
    const nlohmann::json& json = *static_cast<const nlohmann::json*>(json_data);

    // Parse required fields
    if (!json.contains("type") || !json["type"].is_string()) {
        throw std::runtime_error("Check missing required string field: 'type'");
    }

    if (!json.contains("name") || !json["name"].is_string()) {
        throw std::runtime_error("Check missing required string field: 'name'");
    }

    // Parse check type
    std::string type_str = json["type"].get<std::string>();
    CheckType type = CheckType::kUnknown;

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
    } else if (type_str == "define") {
        type = CheckType::kDefine;
    } else if (type_str == "subst") {
        // Backward compatibility: subst -> kM4Variable with subst=true
        type = CheckType::kM4Variable;
    } else if (type_str == "m4_variable") {
        // Backward compatibility: m4_define -> kM4Variable
        type = CheckType::kM4Variable;
    } else if (type_str == "m4_variable") {
        type = CheckType::kM4Variable;
    } else {
        throw std::runtime_error("Unknown check type: " + type_str);
    }

    // Parse optional define field
    std::optional<std::string> define_value;
    if (json.contains("define") && json["define"].is_string()) {
        define_value = json["define"].get<std::string>();
    }

    // Create check with required fields
    Check check(type, json["name"].get<std::string>(), define_value,
                json.contains("language") && json["language"].is_string()
                    ? json["language"].get<std::string>()
                    : "c");

    // Parse optional subst field
    if (json.contains("subst") && json["subst"].is_string()) {
        check.subst_ = json["subst"].get<std::string>();
    }

    // Parse optional fields
    if (json.contains("code") && json["code"].is_string()) {
        check.code_ = json["code"].get<std::string>();
    }

    if (json.contains("file_path") && json["file_path"].is_string()) {
        check.file_path_ = json["file_path"].get<std::string>();
    }

    // Parse define_value - always use dump() to preserve type information
    // String "1" -> "\"1\"" (renders as "1" in C code - a string literal)
    // Integer 1 -> "1" (renders as 1 in C code - a number)
    // Exception: null values are kept as nullopt (will render as /**/ for
    // AC_DEFINE) Exception: If value is already a JSON-encoded string (starts
    // and ends with quotes), use it as-is to avoid double-encoding (e.g.,
    // package_info values)
    if (json.contains("define_value")) {
        if (json["define_value"].is_null()) {
            // Explicit None/null - keep as nullopt to render as /**/
            check.define_value_ = std::nullopt;
        } else {
            if (json["define_value"].is_string()) {
                std::string str_value = json["define_value"].get<std::string>();
                // Always use dump() to properly JSON-encode the value
                // This ensures values containing quotes (e.g., "\"Hello\"") are
                // properly encoded The previous check for "already
                // JSON-encoded" was too simplistic and could incorrectly detect
                // strings containing quotes as "already encoded"
                check.define_value_ = json["define_value"].dump();
            } else {
                // Non-string, use dump()
                check.define_value_ = json["define_value"].dump();
            }
        }
    }

    // Parse define_value_fail - always use dump() to preserve type information
    // Exception: If value is already a JSON-encoded string, use it as-is
    // Note: null values are now handled (set to nullopt) to match define_value
    // behavior
    if (json.contains("define_value_fail")) {
        if (json["define_value_fail"].is_null()) {
            // Explicit None/null - keep as nullopt to render as /**/ (matches
            // define_value behavior)
            check.define_value_fail_ = std::nullopt;
        } else {
            if (json["define_value_fail"].is_string()) {
                std::string str_value =
                    json["define_value_fail"].get<std::string>();
                // Check if it's already a JSON-encoded string (starts and ends
                // with quotes)
                if (str_value.size() >= 2 && str_value.front() == '"' &&
                    str_value.back() == '"') {
                    // Already JSON-encoded, use as-is
                    check.define_value_fail_ = str_value;
                } else {
                    // Regular string, use dump()
                    check.define_value_fail_ = json["define_value_fail"].dump();
                }
            } else {
                // Non-string, use dump()
                check.define_value_fail_ = json["define_value_fail"].dump();
            }
        }
    }

    if (json.contains("library") && json["library"].is_string()) {
        check.library_ = json["library"].get<std::string>();
    }

    if (json.contains("requires") && json["requires"].is_array()) {
        std::vector<std::string> requires_list;
        for (const nlohmann::json& req : json["requires"]) {
            if (req.is_string()) {
                requires_list.push_back(req.get<std::string>());
            }
        }
        if (!requires_list.empty()) {
            check.requires_ = requires_list;
        }
    }

    if (json.contains("condition") && json["condition"].is_string()) {
        check.condition_ = json["condition"].get<std::string>();
    }

    if (json.contains("compile_defines") &&
        json["compile_defines"].is_array()) {
        std::vector<std::string> compile_defines_list;
        for (const nlohmann::json& def : json["compile_defines"]) {
            if (def.is_string()) {
                compile_defines_list.push_back(def.get<std::string>());
            }
        }
        if (!compile_defines_list.empty()) {
            check.compile_defines_ = compile_defines_list;
        }
    }

    // Parse unquote field (for AC_DEFINE_UNQUOTED)
    if (json.contains("unquote") && json["unquote"].is_boolean()) {
        check.unquote_ = json["unquote"].get<bool>();
    }

    // Validate structure: some check types require code (or code/file_path).
    // This keeps parsing strict so runtime failures are not silent/misleading.
    switch (type) {
        case CheckType::kSizeof:
        case CheckType::kAlignof:
        case CheckType::kComputeInt:
        case CheckType::kEndian:
        case CheckType::kDecl:
        case CheckType::kMember:
            if (!check.code().has_value()) {
                throw std::runtime_error(
                    "Check type '" + check_type_to_string(type) +
                    "' requires 'code' but it was not provided (check name: " +
                    check.name() + ")");
            }
            break;
        case CheckType::kCompile:
        case CheckType::kLink:
            if (!check.code().has_value() && !check.file_path().has_value()) {
                throw std::runtime_error(
                    "Check type '" + check_type_to_string(type) +
                    "' requires either 'code' or 'file_path' but neither was "
                    "provided (check name: " +
                    check.name() + ")");
            }
            break;
        default:
            break;
    }

    return check;
}

}  // namespace rules_cc_autoconf
