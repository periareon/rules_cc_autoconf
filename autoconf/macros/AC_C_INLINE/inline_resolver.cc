/**
 * @brief Resolver tool for AC_C_INLINE keyword fallback chain.
 *
 * Accepts an ordered list of --check keyword=path pairs and a --define name.
 * Reads each result JSON in order and picks the first successful keyword.
 *
 * If the first successful keyword matches the define name (the native
 * keyword), no #define is needed (value = null). Otherwise the keyword
 * becomes the replacement value: #define <define> <keyword>.
 * If none succeed, the define is set to empty.
 *
 * Example (AC_C_INLINE):
 *   inline_resolver \
 *     --define inline \
 *     --check inline=/path/to/inline.json \
 *     --check __inline__=/path/to/__inline__.json \
 *     --check __inline=/path/to/__inline.json \
 *     --output /path/to/result.json
 */

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "tools/json/json.h"

namespace {

std::vector<std::string> read_response_file(const std::string& file_path) {
    std::vector<std::string> args;
    std::ifstream file(file_path);
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open response file: " << file_path
                  << std::endl;
        return args;
    }

    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty()) {
            size_t last_non_ws = line.find_last_not_of(" \t\r\n");
            if (last_non_ws != std::string::npos) {
                line.erase(last_non_ws + 1);
            } else {
                line.clear();
            }
            size_t first_non_ws = line.find_first_not_of(" \t\r\n");
            if (first_non_ws != std::string::npos) {
                line.erase(0, first_non_ws);
            } else {
                line.clear();
            }
            if (!line.empty()) {
                args.push_back(line);
            }
        }
    }
    return args;
}

struct KeywordCheck {
    std::string keyword;
    std::string path;
};

struct ResolverArgs {
    std::string define_name{};
    std::vector<KeywordCheck> checks{};
    std::string output_path{};
    bool show_help = false;
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --define <name>          Define name to resolve "
                 "(required)\n";
    std::cout << "  --check <keyword>=<file> Keyword and its result JSON, "
                 "in fallback order (at least one required)\n";
    std::cout << "  --output <file>          Path to output result JSON "
                 "(required)\n";
    std::cout << "  --help                   Show this help message\n";
}

std::optional<ResolverArgs> parse_args(int argc, char* argv[]) {
    std::vector<std::string> expanded_args;
    std::vector<char*> expanded_argv;
    int expanded_argc = argc;
    char** expanded_argv_ptr = argv;

    if (argc == 2 && argv[1][0] == '@') {
        std::string file_path = std::string(argv[1] + 1);
        if (file_path.empty()) {
            std::cerr << "Error: Response file path cannot be empty after '@'"
                      << std::endl;
            return std::nullopt;
        }
        expanded_args = read_response_file(file_path);
        if (expanded_args.empty()) {
            return std::nullopt;
        }

        expanded_argv.push_back(argv[0]);
        for (const auto& arg : expanded_args) {
            expanded_argv.push_back(const_cast<char*>(arg.c_str()));
        }
        expanded_argc = expanded_argv.size();
        expanded_argv_ptr = expanded_argv.data();
    }

    ResolverArgs args;

    for (int i = 1; i < expanded_argc; ++i) {
        std::string arg = expanded_argv_ptr[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--define") {
            if (i + 1 < expanded_argc) {
                args.define_name = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --define requires a name" << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--check") {
            if (i + 1 < expanded_argc) {
                std::string val = std::string(expanded_argv_ptr[++i]);
                auto eq = val.find('=');
                if (eq == std::string::npos || eq == 0) {
                    std::cerr << "Error: --check requires keyword=path format"
                              << std::endl;
                    return std::nullopt;
                }
                args.checks.push_back({val.substr(0, eq), val.substr(eq + 1)});
            } else {
                std::cerr << "Error: --check requires keyword=path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--output") {
            if (i + 1 < expanded_argc) {
                args.output_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --output requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    if (args.define_name.empty()) {
        std::cerr << "Error: --define is required" << std::endl;
        return std::nullopt;
    }
    if (args.checks.empty()) {
        std::cerr << "Error: at least one --check is required" << std::endl;
        return std::nullopt;
    }
    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

std::optional<bool> read_check_success(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        std::cerr << "Error: cannot open result file: " << path << std::endl;
        return std::nullopt;
    }

    nlohmann::json j;
    file >> j;

    if (!j.is_object() || j.empty()) {
        std::cerr << "Error: invalid result JSON in: " << path << std::endl;
        return std::nullopt;
    }

    if (!j.contains("success") || !j["success"].is_boolean()) {
        std::cerr << "Error: missing 'success' field in: " << path << std::endl;
        return std::nullopt;
    }

    return j["success"].get<bool>();
}

/**
 * @brief Write the resolved define result JSON.
 *
 * @param path Output file path.
 * @param value empty string = #define <name> (empty),
 *              non-empty = #define <name> <value>.
 * @param success Whether any keyword was found.
 */
int write_result(const std::string& path,
                 const std::optional<std::string>& value, bool success) {
    nlohmann::json j;

    if (value.has_value()) {
        j["value"] = *value;
    } else {
        j["value"] = nullptr;
    }

    j["success"] = success;
    j["type"] = "compile";

    std::ofstream out(path);
    if (!out.is_open()) {
        std::cerr << "Error: cannot open output file: " << path << std::endl;
        return 1;
    }

    out << j.dump(4) << std::endl;
    return 0;
}

int resolve(const ResolverArgs& args) {
    for (const auto& check : args.checks) {
        auto ok = read_check_success(check.path);
        if (!ok.has_value()) {
            return 1;
        }

        if (*ok) {
            return write_result(args.output_path, check.keyword, true);
        }
    }

    // No keyword works — #define <define> /**/ (empty)
    return write_result(args.output_path, std::string(""), false);
}

}  // namespace

int main(int argc, char* argv[]) {
    auto args_opt = parse_args(argc, argv);
    if (!args_opt.has_value()) {
        print_usage(argv[0]);
        return 1;
    }

    if (args_opt->show_help) {
        print_usage(argv[0]);
        return 0;
    }

    return resolve(*args_opt);
}
