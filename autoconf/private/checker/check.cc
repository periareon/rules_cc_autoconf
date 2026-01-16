#include "autoconf/private/checker/check.h"

#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

std::string check_type_to_string(CheckType type) {
    switch (type) {
        case CheckType::kHeader:
            return "header";
        case CheckType::kFunction:
            return "function";
        case CheckType::kLib:
            return "lib";
        case CheckType::kSymbol:
            return "symbol";
        case CheckType::kType:
            return "type";
        case CheckType::kCompile:
            return "compile";
        case CheckType::kLink:
            return "link";
        case CheckType::kDefine:
            return "define";
        case CheckType::kSubst:
            return "subst";
        case CheckType::kM4Define:
            return "m4_define";
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

std::optional<Check> Check::from_json(const void* json_data) {
    const nlohmann::json& json = *static_cast<const nlohmann::json*>(json_data);

    // Parse required fields
    if (!json.contains("type") || !json["type"].is_string()) {
        DebugLogger::warn("Check missing 'type' field");
        return std::nullopt;
    }

    if (!json.contains("name") || !json["name"].is_string()) {
        DebugLogger::warn("Check missing 'name' field");
        return std::nullopt;
    }

    if (!json.contains("define") || !json["define"].is_string()) {
        DebugLogger::warn("Check missing 'define' field");
        return std::nullopt;
    }

    // Parse check type
    std::string type_str = json["type"].get<std::string>();
    CheckType type;

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
        type = CheckType::kSubst;
    } else if (type_str == "m4_define") {
        type = CheckType::kM4Define;
    } else {
        DebugLogger::warn("Unknown check type: " + type_str);
        return std::nullopt;
    }

    // Create check with required fields
    Check check(type, json["name"].get<std::string>(),
                json["define"].get<std::string>(),
                json.contains("language") && json["language"].is_string()
                    ? json["language"].get<std::string>()
                    : "c");

    // Parse optional fields
    if (json.contains("code") && json["code"].is_string()) {
        check.code_ = json["code"].get<std::string>();
    }

    if (json.contains("file_path") && json["file_path"].is_string()) {
        check.file_path_ = json["file_path"].get<std::string>();
    }

    if (json.contains("define_value") && json["define_value"].is_string()) {
        check.define_value_ = json["define_value"].get<std::string>();
    }

    if (json.contains("define_value_fail") &&
        json["define_value_fail"].is_string()) {
        check.define_value_fail_ = json["define_value_fail"].get<std::string>();
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

    return check;
}

}  // namespace rules_cc_autoconf
