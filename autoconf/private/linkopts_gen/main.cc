/**
 * @brief Extracts linker flags from autoconf subst result JSON files.
 *
 * Reads AC_SEARCH_LIBS / AC_SUBST result files and outputs a linker
 * response file containing only non-empty flag values. This is used
 * by the autoconf_linkopts rule to dynamically determine which -l
 * flags are needed based on build-time autoconf checks.
 *
 * Usage:
 *   linkopts_gen --var LIBPTHREAD=path/to/result.json \
 *                --var FDATASYNC_LIB=path/to/result.json \
 *                --output flags.txt
 *
 * Output format: one flag per line (linker response file format).
 */

#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "autoconf/private/common/file_util.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

struct VarMapping {
    std::string name;
    std::string file_path;
};

bool parse_args(int argc, char* argv[], std::vector<VarMapping>* vars,
                std::string* output_path) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--var") {
            if (i + 1 >= argc) {
                std::cerr << "Error: --var requires a value\n";
                return false;
            }
            std::string value = argv[++i];
            auto eq = value.find('=');
            if (eq == std::string::npos || eq == 0 || eq >= value.size() - 1) {
                std::cerr << "Error: --var must be NAME=PATH, got: " << value
                          << "\n";
                return false;
            }
            vars->push_back({value.substr(0, eq), value.substr(eq + 1)});
        } else if (arg == "--output") {
            if (i + 1 >= argc) {
                std::cerr << "Error: --output requires a value\n";
                return false;
            }
            *output_path = argv[++i];
        } else {
            std::cerr << "Error: unknown argument: " << arg << "\n";
            return false;
        }
    }

    if (output_path->empty()) {
        std::cerr << "Error: --output is required\n";
        return false;
    }
    return true;
}

std::string extract_flag_value(const std::string& file_path,
                               const std::string& var_name) {
    auto ifs = open_ifstream(file_path);
    if (!ifs.is_open()) {
        std::cerr << "Error: cannot open " << file_path << "\n";
        return "";
    }

    nlohmann::json root;
    ifs >> root;

    // Result files have the form: { "cache_var_name": { "value": "...", ... } }
    // The outer key is the cache variable name, not the subst variable name.
    for (auto& [key, result] : root.items()) {
        if (!result.is_object()) continue;

        if (result.contains("subst") && result["subst"].is_string()) {
            if (result["subst"].get<std::string>() != var_name) {
                continue;
            }
        }

        if (!result.contains("value")) continue;

        const auto& val = result["value"];
        if (val.is_null()) return "";
        if (val.is_string()) return val.get<std::string>();
        if (val.is_number() && val.get<int>() == 0) return "";
        return val.dump();
    }

    // Fallback: try using the value directly for simple AC_SUBST results
    for (auto& [key, result] : root.items()) {
        if (!result.is_object()) continue;
        if (!result.contains("value")) continue;

        const auto& val = result["value"];
        if (val.is_null()) return "";
        if (val.is_string()) return val.get<std::string>();
        return "";
    }

    return "";
}

int run(int argc, char* argv[]) {
    std::vector<VarMapping> vars;
    std::string output_path;

    if (!parse_args(argc, argv, &vars, &output_path)) {
        return 1;
    }

    std::vector<std::string> flags;
    for (const auto& var : vars) {
        std::string flag = extract_flag_value(var.file_path, var.name);
        if (!flag.empty()) {
            bool found = false;
            for (const auto& existing : flags) {
                if (existing == flag) {
                    found = true;
                    break;
                }
            }
            if (!found) {
                flags.push_back(flag);
            }
        }
    }

    auto ofs = open_ofstream(output_path);
    if (!ofs.is_open()) {
        std::cerr << "Error: cannot open output file " << output_path << "\n";
        return 1;
    }

    for (const auto& flag : flags) {
        ofs << flag << "\n";
    }

    return 0;
}

}  // namespace rules_cc_autoconf

int main(int argc, char* argv[]) { return rules_cc_autoconf::run(argc, argv); }
