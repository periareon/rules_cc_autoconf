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

#include "tools/json/json.h"

namespace rules_cc_autoconf {

struct ResultEntry {
    std::string value{};
    bool success = false;
};

struct SrcsArgs {
    struct DepMapping {
        std::string lookup_name{};
        std::string file_path{};

        DepMapping() : lookup_name(), file_path() {}
    };

    std::vector<DepMapping> dep_mappings{};
    struct SrcMapping {
        std::string input_path{};
        std::string define{};
        std::string output_path{};

        SrcMapping() : input_path(), define(), output_path() {}
    };

    std::vector<SrcMapping> srcs{};
    bool show_help = false;

    SrcsArgs() : dep_mappings(), srcs(), show_help(false) {}
};

void print_usage(const char* program_name) {
    std::cout << "Usage: " << program_name << " [options]\n";
    std::cout << "Options:\n";
    std::cout
        << "  --dep <name>=<file>   Mapping of lookup name to JSON result file "
           "(can be specified multiple times)\n";
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
        } else if (arg == "--dep") {
            if (i + 1 < argc) {
                std::string value = argv[++i];
                std::size_t eq = value.find('=');
                if (eq == std::string::npos || eq == 0 ||
                    eq >= value.size() - 1) {
                    std::cerr << "Error: --dep value must be of the form "
                              << "{name}={file}, got: " << value << std::endl;
                    return false;
                }
                SrcsArgs::DepMapping m;
                m.lookup_name = value.substr(0, eq);
                m.file_path = value.substr(eq + 1);
                args.dep_mappings.push_back(m);
            } else {
                std::cerr << "Error: --dep requires a name=file argument"
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

    if (args.dep_mappings.empty()) {
        std::cerr << "Error: At least one --dep is required" << std::endl;
        return false;
    }

    *out_args = args;
    return true;
}

std::unordered_map<std::string, std::string> build_dep_map(
    const std::vector<SrcsArgs::DepMapping>& mappings) {
    std::unordered_map<std::string, std::string> out;
    for (const SrcsArgs::DepMapping& m : mappings) {
        if (m.lookup_name.empty() || m.file_path.empty()) {
            throw std::runtime_error(
                "Invalid --dep mapping (empty name or path)");
        }
        std::unordered_map<std::string, std::string>::iterator it =
            out.find(m.lookup_name);
        if (it != out.end() && it->second != m.file_path) {
            throw std::runtime_error("Duplicate --dep mapping for name '" +
                                     m.lookup_name + "' with different files");
        }
        out[m.lookup_name] = m.file_path;
    }
    return out;
}

ResultEntry load_single_result_from_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open results file: " + path);
    }

    nlohmann::json j{};
    file >> j;
    file.close();

    if (j.is_null() || !j.is_object() || j.empty()) {
        // Treat empty/invalid as unsuccessful.
        return ResultEntry{};
    }

    // Each results file produced by these rules is expected to contain exactly
    // one entry. We take the first entry regardless of its key.
    nlohmann::json::iterator it = j.begin();
    const nlohmann::json& val = it.value();

    ResultEntry entry;
    nlohmann::json::const_iterator value_it = val.find("value");
    if (value_it != val.end() && !value_it->is_null()) {
        if (value_it->is_string()) {
            entry.value = value_it->get<std::string>();
        } else {
            entry.value = value_it->dump();
        }
    }
    nlohmann::json::const_iterator success_it = val.find("success");
    entry.success = (success_it != val.end()) ? success_it->get<bool>() : false;
    return entry;
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

    bool enabled = defined && !value.empty() && value != "0";

    if (enabled) {
        out_file << original_content;
        if (!original_content.empty() && original_content.back() != '\n') {
            out_file << "\n";
        }
    } else {
        out_file << "#if 0 /* " << define << " */\n";
        out_file << original_content;
        if (!original_content.empty() && original_content.back() != '\n') {
            out_file << "\n";
        }
        out_file << "#endif\n";
    }

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

    std::unordered_map<std::string, std::string> dep_map;
    std::unordered_map<std::string, ResultEntry> result_cache;
    try {
        dep_map = build_dep_map(args.dep_mappings);
    } catch (const std::exception& ex) {
        std::cerr << "Error: " << ex.what() << std::endl;
        return 1;
    }

    for (const SrcsArgs::SrcMapping& mapping : args.srcs) {
        const std::filesystem::path out_path(mapping.output_path);
        const std::filesystem::path orig_path(mapping.input_path);
        const std::string& define = mapping.define;

        std::unordered_map<std::string, std::string>::iterator dep_it =
            dep_map.find(define);
        if (dep_it == dep_map.end()) {
            std::cerr << "Error: No --dep mapping provided for '" << define
                      << "'" << std::endl;
            return 1;
        }

        // Load result once per referenced file.
        std::unordered_map<std::string, ResultEntry>::iterator cached =
            result_cache.find(define);
        if (cached == result_cache.end()) {
            try {
                result_cache[define] =
                    load_single_result_from_file(dep_it->second);
            } catch (const std::exception& ex) {
                std::cerr << "Error: " << ex.what() << std::endl;
                return 1;
            }
        }

        // Generate the wrapper using the resolved (namespace-agnostic) result.
        std::unordered_map<std::string, ResultEntry> one;
        one[define] = result_cache[define];
        if (!generate_wrapped_source(out_path, orig_path, define, one)) {
            return 1;
        }
    }

    return 0;
}
