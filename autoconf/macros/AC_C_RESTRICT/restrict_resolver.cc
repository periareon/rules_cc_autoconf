/**
 * @brief Resolver tool for AC_C_RESTRICT keyword fallback chain.
 *
 * Reads the result JSON files from three compile checks (one per keyword
 * variant: restrict, __restrict__, __restrict) and writes a single result
 * JSON for the "restrict" define using the first keyword that compiled
 * successfully.
 *
 * Fallback order (matches GNU autoconf AC_C_RESTRICT):
 *   1. restrict      — if compiles, no #define needed (keyword is native)
 *   2. __restrict__  — if compiles, #define restrict __restrict__
 *   3. __restrict    — if compiles, #define restrict __restrict
 *   4. none          — #define restrict (empty, effectively removes it)
 */

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "autoconf/private/json/json.h"

namespace {

/**
 * @brief Read arguments from a response file (@file pattern).
 * @param file_path Path to the response file (without @ prefix).
 * @return Vector of argument strings, one per line, or empty on error.
 */
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
        // Trim whitespace from line
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

/**
 * @brief Parsed command-line arguments for restrict_resolver binary.
 */
struct RestrictResolverArgs {
    /** Path to the compile-check result JSON for bare "restrict" keyword
     * (required) */
    std::string restrict_path{};

    /** Path to the compile-check result JSON for "__restrict__" keyword
     * (required) */
    std::string restrict_dunder_path{};

    /** Path to the compile-check result JSON for "__restrict" keyword
     * (required) */
    std::string underscore_restrict_path{};

    /** Path to the output result JSON file (required) */
    std::string output_path{};

    /** Whether to show help */
    bool show_help = false;
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --restrict <file>      Path to result JSON for bare "
                 "'restrict' keyword check (required)\n";
    std::cout << "  --restrict__ <file>    Path to result JSON for "
                 "'__restrict__' keyword check (required)\n";
    std::cout << "  --_restrict <file>     Path to result JSON for "
                 "'__restrict' keyword check (required)\n";
    std::cout << "  --output <file>        Path to output result JSON "
                 "(required)\n";
    std::cout << "  --help                 Show this help message\n";
}

std::optional<RestrictResolverArgs> parse_args(int argc, char* argv[]) {
    // Check for @file response file pattern
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

    RestrictResolverArgs args;

    for (int i = 1; i < expanded_argc; ++i) {
        std::string arg = expanded_argv_ptr[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--restrict") {
            if (i + 1 < expanded_argc) {
                args.restrict_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --restrict requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--restrict__") {
            if (i + 1 < expanded_argc) {
                args.restrict_dunder_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --restrict__ requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--_restrict") {
            if (i + 1 < expanded_argc) {
                args.underscore_restrict_path =
                    std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --_restrict requires a file path"
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

    // Validate required arguments
    if (args.restrict_path.empty()) {
        std::cerr << "Error: --restrict is required" << std::endl;
        return std::nullopt;
    }
    if (args.restrict_dunder_path.empty()) {
        std::cerr << "Error: --restrict__ is required" << std::endl;
        return std::nullopt;
    }
    if (args.underscore_restrict_path.empty()) {
        std::cerr << "Error: --_restrict is required" << std::endl;
        return std::nullopt;
    }
    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

/**
 * @brief Read a checker result JSON file and return whether the check
 * succeeded.
 * @param path Path to the result JSON file.
 * @return true/false for success status, or nullopt on read error.
 */
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

    // The result file has one top-level key (the cache variable name)
    // with an object containing "success": true/false.
    auto it = j.begin();
    const nlohmann::json& result = it.value();

    if (!result.contains("success") || !result["success"].is_boolean()) {
        std::cerr << "Error: missing 'success' field in: " << path << std::endl;
        return std::nullopt;
    }

    return result["success"].get<bool>();
}

/**
 * @brief Write the final restrict define result JSON.
 *
 * Produces output in the same format as the checker binary so it can be
 * consumed by autoconf_hdr and the CcAutoconfInfo provider.
 *
 * @param path Output file path.
 * @param value The define value — nullopt means "don't define" (keyword works
 *              natively), empty string means #define restrict (empty),
 *              otherwise #define restrict <value>.
 * @param success Whether any keyword was found.
 * @return 0 on success, 1 on error.
 */
int write_result(const std::string& path,
                 const std::optional<std::string>& value, bool success) {
    nlohmann::json result_obj;

    if (value.has_value()) {
        if (value->empty()) {
            // Empty string: renders as #define restrict /**/
            result_obj["value"] = "";
        } else {
            // Keyword alias: renders as #define restrict <value>
            result_obj["value"] = *value;
        }
    } else {
        // No define needed: keyword works natively.
        // Write null value so the define is not emitted.
        result_obj["value"] = nullptr;
    }

    result_obj["success"] = success;
    result_obj["is_define"] = true;
    result_obj["is_subst"] = false;
    result_obj["type"] = "compile";
    result_obj["define"] = "restrict";
    result_obj["unquote"] = true;

    nlohmann::json j;
    j["restrict"] = result_obj;

    std::ofstream out(path);
    if (!out.is_open()) {
        std::cerr << "Error: cannot open output file: " << path << std::endl;
        return 1;
    }

    out << j.dump(4) << std::endl;
    return 0;
}

/**
 * @brief Apply the AC_C_RESTRICT fallback chain.
 *
 * Reads the three compile-check results and writes the resolved restrict
 * define based on which keyword the compiler supports.
 *
 * @param args Parsed command-line arguments.
 * @return 0 on success, 1 on error.
 */
int resolve_restrict(const RestrictResolverArgs& args) {
    // Read the three compile check results.
    auto restrict_ok = read_check_success(args.restrict_path);
    auto restrict_dunder_ok = read_check_success(args.restrict_dunder_path);
    auto underscore_restrict_ok =
        read_check_success(args.underscore_restrict_path);

    if (!restrict_ok.has_value() || !restrict_dunder_ok.has_value() ||
        !underscore_restrict_ok.has_value()) {
        return 1;  // Error already printed.
    }

    // Apply the fallback chain (matches GNU autoconf AC_C_RESTRICT order).
    if (*restrict_ok) {
        // Bare "restrict" keyword works — no #define needed.
        return write_result(args.output_path, std::nullopt, true);
    }

    if (*restrict_dunder_ok) {
        // __restrict__ works — #define restrict __restrict__
        return write_result(args.output_path, "__restrict__", true);
    }

    if (*underscore_restrict_ok) {
        // __restrict works — #define restrict __restrict
        return write_result(args.output_path, "__restrict", true);
    }

    // No keyword works — #define restrict /**/ (empty)
    return write_result(args.output_path, std::string(""), false);
}

}  // namespace

int main(int argc, char* argv[]) {
    std::optional<RestrictResolverArgs> args_opt = parse_args(argc, argv);
    if (!args_opt.has_value()) {
        print_usage(argv[0]);
        return 1;
    }

    RestrictResolverArgs args = *args_opt;

    if (args.show_help) {
        print_usage(argv[0]);
        return 0;
    }

    return resolve_restrict(args);
}
