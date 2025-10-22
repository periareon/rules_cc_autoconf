/**
 * @brief Generates wrapped source files with conditional compilation based on
 * autoconf check results.
 */

#include <filesystem>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "autoconf/private/json/json.h"

namespace rules_cc_autoconf {

struct ResultEntry {
    std::string value{};
    bool success = false;

    ResultEntry() : value(), success(false) {}
};

struct SrcsArgs {
    std::vector<std::string> results_paths{};
    struct SrcMapping {
        std::string input_path{};
        std::string define{};
        std::string output_path{};

        SrcMapping() : input_path(), define(), output_path() {}
    };

    std::vector<SrcMapping> srcs{};
    bool show_help = false;

    SrcsArgs() : results_paths(), srcs(), show_help(false) {}
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout << "  --results <file>      Path to JSON results file (can be "
                 "specified multiple times)\n";
    std::cout
        << "  --src <in>=<DEFINE>=<out>  Input path, associated define, and "
           "output path (may be repeated)\n";
    std::cout << "  --help                Show this help message\n";
}

bool parse_args(int argc, char* argv[], SrcsArgs* out_args) {
    SrcsArgs args{};

    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            args.show_help = true;
            *out_args = args;
            return true;
        } else if (arg == "--results") {
            if (i + 1 < argc) {
                args.results_paths.push_back(argv[++i]);
            } else {
                std::cerr << "Error: --results requires a file path"
                          << std::endl;
                return false;
            }
        } else if (arg == "--src") {
            if (i + 1 < argc) {
                std::string value = argv[++i];
                std::size_t first_eq = value.find('=');
                std::size_t second_eq = (first_eq == std::string::npos)
                                            ? std::string::npos
                                            : value.find('=', first_eq + 1);
                if (first_eq == std::string::npos ||
                    second_eq == std::string::npos || first_eq == 0 ||
                    second_eq <= first_eq + 1 ||
                    second_eq >= value.size() - 1) {
                    std::cerr << "Error: --src value must be of the form "
                              << "{in}={DEFINE}={out}, got: " << value
                              << std::endl;
                    return false;
                }
                SrcsArgs::SrcMapping mapping;
                mapping.input_path = value.substr(0, first_eq);
                mapping.define =
                    value.substr(first_eq + 1, second_eq - first_eq - 1);
                mapping.output_path = value.substr(second_eq + 1);
                args.srcs.push_back(mapping);
            } else {
                std::cerr << "Error: --src requires a value of the form "
                             "{execpath}={DEFINE}"
                          << std::endl;
                return false;
            }
        } else {
            std::cerr << "Error: Unknown argument: " << arg << std::endl;
            return false;
        }
    }

    if (args.results_paths.empty()) {
        std::cerr << "Error: At least one --results is required" << std::endl;
        return false;
    }

    *out_args = args;
    return true;
}

void load_results(const std::vector<std::string>& paths,
                  std::unordered_map<std::string, ResultEntry>* results) {
    std::unordered_map<std::string, ResultEntry> merged_results;

    // Load and merge all results files
    for (const std::string& path : paths) {
        std::ifstream file(path);
        if (!file.is_open()) {
            throw std::runtime_error("Failed to open results file: " + path);
        }

        nlohmann::json j{};
        file >> j;
        file.close();

        // Handle null or empty JSON - treat as empty results
        if (j.is_null() || !j.is_object()) {
            continue;
        }

        for (nlohmann::json::iterator it = j.begin(); it != j.end(); ++it) {
            const std::string key = it.key();
            const nlohmann::json& val = it.value();
            ResultEntry entry;
            nlohmann::json::const_iterator value_it = val.find("value");
            if (value_it != val.end() && !value_it->is_null()) {
                entry.value = value_it->get<std::string>();
            }
            nlohmann::json::const_iterator success_it = val.find("success");
            entry.success =
                (success_it != val.end()) ? success_it->get<bool>() : false;
            // Merge results (later files override earlier ones for duplicate
            // keys)
            merged_results[key] = entry;
        }
    }

    *results = merged_results;
}

bool generate_wrapped_source(
    const std::filesystem::path& out_path,
    const std::filesystem::path& orig_path, const std::string& define,
    const std::unordered_map<std::string, ResultEntry>& results) {
    std::unordered_map<std::string, ResultEntry>::const_iterator it =
        results.find(define);
    bool defined = false;
    std::string value{};
    if (it != results.end()) {
        defined = it->second.success;
        value = it->second.value;
    }

    std::ifstream in_file(orig_path);
    if (!in_file.is_open()) {
        std::cerr << "Error: Failed to open source file: " << orig_path.string()
                  << std::endl;
        return false;
    }
    std::stringstream buffer{};
    buffer << in_file.rdbuf();
    std::string original_content = buffer.str();
    in_file.close();

    std::filesystem::create_directories(out_path.parent_path());

    std::ofstream out_file(out_path);
    if (!out_file.is_open()) {
        std::cerr << "Error: Failed to open output file: " << out_path.string()
                  << std::endl;
        return false;
    }

    if (defined) {
        out_file << "#define " << define;
        if (!value.empty()) {
            out_file << " " << value;
        }
        out_file << "\n";
    } else {
        out_file << "#undef " << define << "\n";
    }

    out_file << "#ifdef " << define << "\n";
    out_file << original_content;
    if (!original_content.empty() && original_content.back() != '\n') {
        out_file << "\n";
    }
    out_file << "#endif\n";

    out_file.close();
    return true;
}

}  // namespace rules_cc_autoconf

int main(int argc, char* argv[]) {
    using namespace rules_cc_autoconf;

    SrcsArgs args;
    if (!parse_args(argc, argv, &args)) {
        print_usage(argv[0]);
        return 1;
    }

    if (args.show_help) {
        print_usage(argv[0]);
        return 0;
    }

    std::unordered_map<std::string, ResultEntry> results;
    try {
        load_results(args.results_paths, &results);
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }

    for (std::vector<SrcsArgs::SrcMapping>::const_iterator it =
             args.srcs.begin();
         it != args.srcs.end(); ++it) {
        const std::filesystem::path out_path(it->output_path);
        const std::filesystem::path orig_path(it->input_path);
        const std::string& define = it->define;

        if (!generate_wrapped_source(out_path, orig_path, define, results)) {
            return 1;
        }
    }

    return 0;
}
