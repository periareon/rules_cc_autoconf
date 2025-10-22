#include "autoconf/private/resolver/resolver_arg_parse.h"

#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>

namespace rules_cc_autoconf {

void ResolverArgParse::print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --results <file>       Path to JSON results file (can be "
                 "specified multiple times)\n";
    std::cout
        << "  --template <file>      Template file (config.h.in) (required)\n";
    std::cout << "  --config <file>        Optional config file (for running "
                 "additional checks from template)\n";
    std::cout << "  --output <file>        Path to output config.h file "
                 "(required)\n";
    std::cout << "  --inline <string> <file> Replace exact string in template "
                 "with file content (can be specified multiple times)\n";
    std::cout << "  --help                 Show this help message\n";
}

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
            // Remove trailing whitespace
            size_t last_non_ws = line.find_last_not_of(" \t\r\n");
            if (last_non_ws != std::string::npos) {
                line.erase(last_non_ws + 1);
            } else {
                line.clear();  // Line is all whitespace
            }
            // Remove leading whitespace
            size_t first_non_ws = line.find_first_not_of(" \t\r\n");
            if (first_non_ws != std::string::npos) {
                line.erase(0, first_non_ws);
            } else {
                line.clear();  // Line is all whitespace
            }
            // Only add non-empty lines
            if (!line.empty()) {
                args.push_back(line);
            }
        }
    }
    return args;
}
}  // namespace

std::optional<ResolverArgs> ResolverArgParse::parse(int argc, char* argv[]) {
    // Check for @file response file pattern
    // If there's only one argument and it starts with '@', treat it as a
    // response file
    std::vector<std::string> expanded_args;
    std::vector<char*> expanded_argv;
    int expanded_argc = argc;
    char** expanded_argv_ptr = argv;

    if (argc == 2 && argv[1][0] == '@') {
        // Extract file path (remove @ prefix)
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

        // Build new argv array: program name + expanded arguments
        expanded_argv.push_back(argv[0]);  // program name
        for (const auto& arg : expanded_args) {
            expanded_argv.push_back(const_cast<char*>(arg.c_str()));
        }
        expanded_argc = expanded_argv.size();
        expanded_argv_ptr = expanded_argv.data();
    }

    ResolverArgs args;
    args.show_help = false;

    for (int i = 1; i < expanded_argc; ++i) {
        std::string arg = expanded_argv_ptr[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            return args;
        } else if (arg == "--results") {
            if (i + 1 < expanded_argc) {
                args.results_paths.push_back(
                    std::string(expanded_argv_ptr[++i]));
            } else {
                std::cerr << "Error: --results requires a file path"
                          << std::endl;
                return std::nullopt;
            }
        } else if (arg == "--template") {
            if (i + 1 < expanded_argc) {
                args.template_path = std::string(expanded_argv_ptr[++i]);
            } else {
                std::cerr << "Error: --template requires a file path"
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
        } else if (arg == "--inline") {
            if (i + 2 < expanded_argc) {
                std::string search_string = std::string(expanded_argv_ptr[++i]);
                std::string file_path = std::string(expanded_argv_ptr[++i]);
                args.inlines[search_string] = file_path;
            } else {
                std::cerr
                    << "Error: --inline requires a search string and file path"
                    << std::endl;
                return std::nullopt;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return std::nullopt;
        }
    }

    // Validate required arguments
    if (args.results_paths.empty()) {
        std::cerr << "Error: At least one --results is required" << std::endl;
        return std::nullopt;
    }

    if (args.output_path.empty()) {
        std::cerr << "Error: --output is required" << std::endl;
        return std::nullopt;
    }

    if (!args.template_path.has_value()) {
        std::cerr << "Error: --template is required" << std::endl;
        return std::nullopt;
    }

    return args;
}

}  // namespace rules_cc_autoconf
