/**
 * @brief Runner for autoconf_conditional_hdr: conditionally wraps a processed
 * gnulib header with a passthrough fallback, mirroring gl_CONDITIONAL_HEADER.
 *
 * When the condition is truthy the processed template is passed through as-is.
 * When falsy, the output wraps the template in a dead `#else` block and
 * activates a passthrough using the include_next/next_header values:
 *
 *   #if 1
 *   #include_next <header.h>
 *   #else
 *   <processed template>
 *   #endif
 */

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "autoconf/private/common/file_util.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

struct Args {
    std::string src_path;
    std::string output_path;
    std::string condition_name;
    std::string include_next_name;
    std::string next_header_name;

    struct DepMapping {
        std::string name;
        std::string file_path;
    };
    std::vector<DepMapping> dep_mappings;
};

void print_usage(const char* prog) {
    std::cerr << "Usage: " << prog
              << " --src <file> --output <file>"
                 " --dep <name>=<file> [--dep ...]"
                 " --condition <name>"
                 " --include-next <name>"
                 " --next-header <name>\n";
}

bool parse_args(int argc, char* argv[], Args& out) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if ((arg == "--src" || arg == "--output" || arg == "--dep" ||
             arg == "--condition" || arg == "--include-next" ||
             arg == "--next-header") &&
            i + 1 >= argc) {
            std::cerr << "Error: " << arg << " requires a value\n";
            return false;
        }

        if (arg == "--src") {
            out.src_path = argv[++i];
        } else if (arg == "--output") {
            out.output_path = argv[++i];
        } else if (arg == "--condition") {
            out.condition_name = argv[++i];
        } else if (arg == "--include-next") {
            out.include_next_name = argv[++i];
        } else if (arg == "--next-header") {
            out.next_header_name = argv[++i];
        } else if (arg == "--dep") {
            std::string val = argv[++i];
            auto eq = val.find('=');
            if (eq == std::string::npos || eq == 0 || eq >= val.size() - 1) {
                std::cerr << "Error: --dep must be name=file, got: " << val
                          << "\n";
                return false;
            }
            out.dep_mappings.push_back({val.substr(0, eq), val.substr(eq + 1)});
        } else {
            std::cerr << "Error: unknown flag: " << arg << "\n";
            return false;
        }
    }

    if (out.src_path.empty() || out.output_path.empty() ||
        out.condition_name.empty() || out.include_next_name.empty() ||
        out.next_header_name.empty() || out.dep_mappings.empty()) {
        std::cerr << "Error: missing required arguments\n";
        return false;
    }
    return true;
}

struct ResultEntry {
    std::string value;
    bool success = false;
};

ResultEntry load_result(const std::string& path) {
    auto file = open_ifstream(path);
    if (!file.is_open()) {
        throw std::runtime_error("Failed to open result file: " + path);
    }

    nlohmann::json j;
    file >> j;

    if (j.is_null() || !j.is_object() || j.empty()) {
        return {};
    }

    ResultEntry entry;
    auto vi = j.find("value");
    if (vi != j.end() && !vi->is_null()) {
        entry.value = vi->is_string() ? vi->get<std::string>() : vi->dump();
    }
    auto si = j.find("success");
    entry.success = (si != j.end()) ? si->get<bool>() : false;
    return entry;
}

/**
 * Matches the autoconf_srcs truthiness pattern: a result is truthy when the
 * check succeeded AND the value is non-empty AND the value is not "0".
 */
bool is_truthy(const ResultEntry& entry) {
    return entry.success && !entry.value.empty() && entry.value != "0" &&
           entry.value != "false";
}

}  // namespace rules_cc_autoconf

int main(int argc, char* argv[]) {
    using namespace rules_cc_autoconf;

    Args args;
    if (!parse_args(argc, argv, args)) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }

    // Build name -> file path map from --dep flags.
    std::unordered_map<std::string, std::string> dep_map;
    for (const auto& m : args.dep_mappings) {
        dep_map[m.name] = m.file_path;
    }

    // Validate that all three named results are present in dep_map.
    for (const auto& name :
         {args.condition_name, args.include_next_name, args.next_header_name}) {
        if (dep_map.find(name) == dep_map.end()) {
            std::cerr << "Error: required check result '" << name
                      << "' not found in deps. Available:";
            for (const auto& [k, _] : dep_map) {
                std::cerr << " " << k;
            }
            std::cerr << "\n";
            return EXIT_FAILURE;
        }
    }

    // Load results for the three named checks.
    ResultEntry condition_result, include_next_result, next_header_result;
    try {
        condition_result = load_result(dep_map.at(args.condition_name));
        include_next_result = load_result(dep_map.at(args.include_next_name));
        next_header_result = load_result(dep_map.at(args.next_header_name));
    } catch (const std::exception& ex) {
        std::cerr << "Error loading result: " << ex.what() << "\n";
        return EXIT_FAILURE;
    }

    // Read the processed template.
    auto src_stream = open_ifstream(args.src_path);
    if (!src_stream.is_open()) {
        std::cerr << "Error: failed to open src: " << args.src_path << "\n";
        return EXIT_FAILURE;
    }
    std::stringstream buf;
    buf << src_stream.rdbuf();
    std::string src_content = buf.str();
    src_stream.close();

    // Write the output.
    auto out = open_ofstream(args.output_path);
    if (!out.is_open()) {
        std::cerr << "Error: failed to open output: " << args.output_path
                  << "\n";
        return EXIT_FAILURE;
    }

    if (is_truthy(condition_result)) {
        out << src_content;
    } else {
        // Resolve the next header value for the passthrough.  When the
        // upstream condition on GL_NEXT_HEADERS prevented resolution the
        // value will be empty; fall back to the output basename wrapped in
        // angle brackets (e.g. "lib/assert.h" → "<assert.h>").
        std::string next_hdr = next_header_result.value;
        if (next_hdr.empty()) {
            auto slash = args.output_path.rfind('/');
            std::string basename = (slash != std::string::npos)
                                       ? args.output_path.substr(slash + 1)
                                       : args.output_path;
            next_hdr = "<" + basename + ">";
        }

        // Construct the passthrough directive: #<include_next> <next_header>
        // GCC/Clang: #include_next <assert.h>
        // MSVC: # \n<inlined system header>
        std::string passthrough =
            "#" + include_next_result.value + " " + next_hdr;

        out << "#if 1\n";
        out << passthrough << "\n";
        out << "#else\n";
        out << src_content;
        if (!src_content.empty() && src_content.back() != '\n') {
            out << "\n";
        }
        out << "#endif\n";
    }

    out.close();
    return EXIT_SUCCESS;
}
