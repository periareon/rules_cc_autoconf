/**
 * @brief Runner for cc_gnulib_conditional_hdrs.
 *
 * Populates a TreeArtifact directory with one file per truthy header.
 * Falsy headers produce no file (mirroring upstream gnulib's `rm -f $@`
 * branch); the consumer's `#include` falls through the include search
 * path to whatever comes next.
 *
 * Each `--hdr hdr_path,condition,slot` writes `hdr_path` to
 * `<out_dir>/<slot>` when `condition` evaluates truthy and does
 * nothing when it evaluates falsy.
 */

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "autoconf/private/checker/condition_evaluator.h"
#include "autoconf/private/common/file_util.h"
#include "tools/json/json.h"

namespace rules_cc_autoconf {

struct DepMapping {
    std::string name;
    std::string file_path;
};

struct HdrMapping {
    std::string hdr_path;
    std::string condition;
    std::string slot;
};

struct Args {
    std::string out_dir;
    std::vector<DepMapping> deps;
    std::vector<HdrMapping> hdrs;
    bool show_help = false;
};

void print_usage(const char* prog) {
    std::cerr << "Usage: " << prog << " --out-dir <dir>"
              << " [--dep <name>=<file> ...]"
              << " --hdr <hdr_path>,<condition>,<slot> [--hdr ...]\n";
}

bool parse_args(int argc, char* argv[], Args& out) {
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--help" || arg == "-h") {
            out.show_help = true;
            return true;
        }

        if ((arg == "--out-dir" || arg == "--dep" || arg == "--hdr") &&
            i + 1 >= argc) {
            std::cerr << "Error: " << arg << " requires a value\n";
            return false;
        }

        if (arg == "--out-dir") {
            out.out_dir = argv[++i];
        } else if (arg == "--dep") {
            std::string val = argv[++i];
            auto eq = val.find('=');
            if (eq == std::string::npos || eq == 0 || eq >= val.size() - 1) {
                std::cerr << "Error: --dep must be name=file, got: " << val
                          << "\n";
                return false;
            }
            out.deps.push_back({val.substr(0, eq), val.substr(eq + 1)});
        } else if (arg == "--hdr") {
            std::string val = argv[++i];
            auto first = val.find(',');
            auto last = val.rfind(',');
            if (first == std::string::npos || last == std::string::npos ||
                first == last || first == 0 || last >= val.size() - 1) {
                std::cerr << "Error: --hdr must be "
                             "hdr_path,condition,slot, got: "
                          << val << "\n";
                return false;
            }
            HdrMapping m;
            m.hdr_path = val.substr(0, first);
            m.condition = val.substr(first + 1, last - first - 1);
            m.slot = val.substr(last + 1);
            out.hdrs.push_back(m);
        } else {
            std::cerr << "Error: unknown flag: " << arg << "\n";
            return false;
        }
    }

    if (out.out_dir.empty()) {
        std::cerr << "Error: --out-dir is required\n";
        return false;
    }
    if (out.hdrs.empty()) {
        std::cerr << "Error: at least one --hdr is required\n";
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
    file.close();

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

// gnulib's `GL_GENERATE_*_H` uses the literal strings "true" / "false";
// ConditionEvaluator treats any non-empty, non-"0" value as truthy and
// would otherwise mis-classify "false" as truthy.  Normalize before
// handing off.
CheckResult to_check_result(const std::string& name, const ResultEntry& entry) {
    std::string value = entry.value;
    if (value == "false") {
        value.clear();
    }
    return CheckResult(name, value, entry.success);
}

bool evaluate(const std::string& condition,
              const std::unordered_map<std::string, ResultEntry>& cache) {
    std::map<std::string, CheckResult> results;
    for (const auto& [name, entry] : cache) {
        results.emplace(name, to_check_result(name, entry));
    }
    ConditionEvaluator evaluator(condition);
    return evaluator.compute(results);
}

bool copy_hdr_to_slot(const std::filesystem::path& src,
                      const std::filesystem::path& dst) {
    std::error_code ec;
    if (dst.has_parent_path()) {
        std::filesystem::create_directories(dst.parent_path(), ec);
        if (ec) {
            std::cerr << "Error: failed to create directory `"
                      << dst.parent_path().string() << "`: " << ec.message()
                      << "\n";
            return false;
        }
    }

    auto in = open_ifstream(src);
    if (!in.is_open()) {
        std::cerr << "Error: failed to open header: " << src.string() << "\n";
        return false;
    }
    auto out = open_ofstream(dst);
    if (!out.is_open()) {
        std::cerr << "Error: failed to open output: " << dst.string() << "\n";
        return false;
    }
    out << in.rdbuf();
    return out.good();
}

}  // namespace rules_cc_autoconf

int main(int argc, char* argv[]) {
    using namespace rules_cc_autoconf;

    Args args;
    if (!parse_args(argc, argv, args)) {
        print_usage(argv[0]);
        return EXIT_FAILURE;
    }
    if (args.show_help) {
        print_usage(argv[0]);
        return EXIT_SUCCESS;
    }

    std::unordered_map<std::string, std::string> dep_map;
    for (const auto& d : args.deps) {
        auto it = dep_map.find(d.name);
        if (it != dep_map.end() && it->second != d.file_path) {
            std::cerr << "Error: --dep `" << d.name
                      << "` listed twice with different files\n";
            return EXIT_FAILURE;
        }
        dep_map[d.name] = d.file_path;
    }

    std::filesystem::path out_dir(args.out_dir);
    std::error_code ec;
    std::filesystem::create_directories(out_dir, ec);
    if (ec) {
        std::cerr << "Error: failed to create out-dir `" << out_dir.string()
                  << "`: " << ec.message() << "\n";
        return EXIT_FAILURE;
    }

    std::unordered_map<std::string, ResultEntry> result_cache;

    for (const auto& hdr : args.hdrs) {
        std::vector<std::string> vars;
        try {
            vars = ConditionEvaluator::extract_variable_names(hdr.condition);
        } catch (const std::exception& ex) {
            std::cerr << "Error: parsing condition `" << hdr.condition
                      << "`: " << ex.what() << "\n";
            return EXIT_FAILURE;
        }

        for (const auto& v : vars) {
            if (result_cache.count(v)) continue;
            auto it = dep_map.find(v);
            if (it == dep_map.end()) {
                std::cerr << "Error: no --dep for `" << v
                          << "` (referenced in condition `" << hdr.condition
                          << "`)\n";
                return EXIT_FAILURE;
            }
            try {
                result_cache[v] = load_result(it->second);
            } catch (const std::exception& ex) {
                std::cerr << "Error: " << ex.what() << "\n";
                return EXIT_FAILURE;
            }
        }

        bool enabled = false;
        try {
            enabled = evaluate(hdr.condition, result_cache);
        } catch (const std::exception& ex) {
            std::cerr << "Error: evaluating condition `" << hdr.condition
                      << "`: " << ex.what() << "\n";
            return EXIT_FAILURE;
        }

        if (!enabled) {
            continue;
        }

        std::filesystem::path dst = out_dir / hdr.slot;
        if (!copy_hdr_to_slot(hdr.hdr_path, dst)) {
            return EXIT_FAILURE;
        }
    }

    return EXIT_SUCCESS;
}
