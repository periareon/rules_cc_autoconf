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

#include "autoconf/private/checker/condition_evaluator.h"
#include "autoconf/private/common/file_util.h"
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
        std::string condition{};
        std::string output_path{};

        SrcMapping() : input_path(), condition(), output_path() {}
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
        << "  --src <in>,<CONDITION>,<out>  Input path, condition expression, "
           "and output path (may be repeated)\n";
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
                // Use comma as separator: {in},{CONDITION},{out}
                // First comma separates input path from condition.
                // Last comma separates condition from output path.
                std::size_t first = value.find(',');
                std::size_t last = value.rfind(',');
                if (first == std::string::npos || last == std::string::npos ||
                    first == last || first == 0 || last >= value.size() - 1) {
                    std::cerr << "Error: --src value must be of the form "
                              << "{in},{CONDITION},{out}, got: " << value
                              << std::endl;
                    return false;
                }
                SrcsArgs::SrcMapping mapping;
                mapping.input_path = value.substr(0, first);
                mapping.condition = value.substr(first + 1, last - first - 1);
                mapping.output_path = value.substr(last + 1);
                args.srcs.push_back(mapping);
            } else {
                std::cerr << "Error: --src requires a value of the form "
                             "{execpath},{CONDITION},{out}"
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
    std::ifstream file = open_ifstream(path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open results file: " + path);
    }

    nlohmann::json j{};
    file >> j;
    file.close();

    if (j.is_null() || !j.is_object() || j.empty()) {
        return ResultEntry{};
    }

    ResultEntry entry;
    nlohmann::json::const_iterator value_it = j.find("value");
    if (value_it != j.end() && !value_it->is_null()) {
        if (value_it->is_string()) {
            entry.value = value_it->get<std::string>();
        } else {
            entry.value = value_it->dump();
        }
    }
    nlohmann::json::const_iterator success_it = j.find("success");
    entry.success = (success_it != j.end()) ? success_it->get<bool>() : false;
    return entry;
}

bool eval_condition_for_src(
    const std::string& condition,
    const std::unordered_map<std::string, ResultEntry>& result_cache) {
    // Build a CheckResult map from the ResultEntry cache for eval_cond
    std::map<std::string, CheckResult> results;
    for (const auto& [name, entry] : result_cache) {
        results.emplace(name, CheckResult(name, entry.value, entry.success));
    }

    ConditionEvaluator evaluator(condition);
    return evaluator.compute(results);
}

bool generate_wrapped_source(const std::filesystem::path& out_path,
                             const std::filesystem::path& orig_path,
                             const std::string& condition, bool enabled) {
    std::ifstream in_file = open_ifstream(orig_path);
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

    std::ofstream out_file = open_ofstream(out_path);
    if (!out_file.is_open()) {
        std::cerr << "Error: Failed to open output file: " << out_path.string()
                  << std::endl;
        return false;
    }

    if (enabled) {
        out_file << original_content;
        if (!original_content.empty() && original_content.back() != '\n') {
            out_file << "\n";
        }
    } else {
        out_file << "#if 0 /* " << condition << " */\n";
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
        const std::string& condition = mapping.condition;

        // Extract all variable names from the (potentially compound) condition,
        // then load each one's result file.
        std::vector<std::string> var_names;
        try {
            var_names = ConditionEvaluator::extract_variable_names(condition);
        } catch (const std::exception& ex) {
            std::cerr << "Error: Failed to parse condition '" << condition
                      << "': " << ex.what() << std::endl;
            return 1;
        }

        for (const std::string& var : var_names) {
            if (result_cache.count(var)) continue;

            auto dep_it = dep_map.find(var);
            if (dep_it == dep_map.end()) {
                std::cerr << "Error: No --dep mapping provided for '" << var
                          << "' (referenced in condition '" << condition << "')"
                          << std::endl;
                return 1;
            }

            try {
                result_cache[var] =
                    load_single_result_from_file(dep_it->second);
            } catch (const std::exception& ex) {
                std::cerr << "Error: " << ex.what() << std::endl;
                return 1;
            }
        }

        bool enabled = false;
        try {
            enabled = eval_condition_for_src(condition, result_cache);
        } catch (const std::exception& ex) {
            std::cerr << "Error: Failed to evaluate condition '" << condition
                      << "': " << ex.what() << std::endl;
            return 1;
        }

        if (!generate_wrapped_source(out_path, orig_path, condition, enabled)) {
            return 1;
        }
    }

    return 0;
}
