#pragma once

#include <fstream>
#include <iostream>
#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * @brief Read arguments from an action args file (@file pattern).
 * @param file_path Path to the args file (without @ prefix).
 * @return Vector of argument strings, one per line, or empty on error.
 */
inline std::vector<std::string> read_action_args_file(
    const std::string& file_path) {
    std::vector<std::string> args;
    std::ifstream file(file_path);
    if (!file.is_open()) {
        std::cerr << "Error: Cannot open action args file: " << file_path
                  << std::endl;
        return args;
    }

    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty()) {
            // Remove trailing whitespace
            size_t last_non_ws = line.find_last_not_of(" \t\r\n");
            if (last_non_ws != std::string::npos) {
                line.erase(last_non_ws + 1);
            } else {
                line.clear();
            }
            // Remove leading whitespace
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
 * @brief Expand @file action args if present.
 *
 * If argc==2 and argv[1] starts with '@', reads the args file and expands
 * arguments. The caller should use the returned argc/argv for parsing.
 *
 * @param argc Original argument count.
 * @param argv Original argument array.
 * @param expanded_args Storage for expanded argument strings (must outlive
 *                      returned argv pointer).
 * @param expanded_argv Storage for expanded argv array (must outlive
 *                      returned argv pointer).
 * @param[out] out_argc Receives the new argument count.
 * @param[out] out_argv Receives pointer to the argument array.
 * @return true if arguments were expanded (or no expansion needed), false on
 * error.
 */
inline bool expand_action_args(int argc, char* argv[],
                               std::vector<std::string>& expanded_args,
                               std::vector<char*>& expanded_argv, int& out_argc,
                               char**& out_argv) {
    out_argc = argc;
    out_argv = argv;

    if (argc == 2 && argv[1][0] == '@') {
        std::string file_path(argv[1] + 1);
        if (file_path.empty()) {
            std::cerr
                << "Error: Action args file path cannot be empty after '@'"
                << std::endl;
            return false;
        }
        expanded_args = read_action_args_file(file_path);
        if (expanded_args.empty()) {
            return false;
        }
        expanded_argv.push_back(argv[0]);
        for (const std::string& arg : expanded_args) {
            expanded_argv.push_back(const_cast<char*>(arg.c_str()));
        }
        out_argc = static_cast<int>(expanded_argv.size());
        out_argv = expanded_argv.data();
    }
    return true;
}

}  // namespace rules_cc_autoconf
