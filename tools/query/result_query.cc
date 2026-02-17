/// @file result_query.cc
/// Query and display autoconf check results as a dependency tree.
///
/// Usage:
///   bazel run //tools/result_query -- <target> [options]
///
/// Options:
///   --type, -t  cache|define|subst   Filter by result type
///   --key,  -k  KEY                  Filter by specific key name
///   --no-values                      Skip reading result values (faster)

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#ifdef _WIN32
#include <direct.h>
#define popen _popen
#define pclose _pclose
#define getcwd _getcwd
#define chdir _chdir
#else
#include <sys/wait.h>
#include <unistd.h>
#endif

#include "tools/json/json.h"

// ── Constants ────────────────────────────────────────────────────────────────

static constexpr const char* kAspectFlag =
    "--aspects=@rules_cc_autoconf//tools/query"
    ":result_query_aspect.bzl%result_query_aspect";
static constexpr const char* kOutputGroupsFlag = "--output_groups=result_query";

// Unicode box-drawing glyphs.
static constexpr const char* kElbow =
    "\xe2\x94\x94\xe2\x94\x80\xe2\x94\x80 ";  // └──
static constexpr const char* kTee =
    "\xe2\x94\x9c\xe2\x94\x80\xe2\x94\x80 ";             // ├──
static constexpr const char* kPipe = "\xe2\x94\x82   ";  // │
static constexpr const char* kSpace = "    ";
static constexpr const char* kCheck = " \xe2\x9c\x93";   // ✓
static constexpr const char* kCross = " \xe2\x9c\x97";   // ✗
static constexpr const char* kArrow = "  \xe2\x97\x80";  // ◀

// ── Types ────────────────────────────────────────────────────────────────────

/// A single node in the autoconf result DAG.
struct DagNode {
    std::string label;
    std::map<std::string, std::string> cache;  // key → result-file path
    std::map<std::string, std::string> define;
    std::map<std::string, std::string> subst;
    std::vector<std::string> deps;
};

/// Parsed value from a check result JSON file.
struct ResultValue {
    std::string display;
    bool success{false};
};

/// CLI arguments.
struct Args {
    std::string target;
    std::string filter_type;  // "" means all
    std::string filter_key;   // "" means all
    bool read_values{true};
};

// ── Command execution ────────────────────────────────────────────────────────

/// Run a command and capture its stdout.  Returns std::nullopt on failure.
static std::optional<std::string> capture(const std::string& cmd) {
    FILE* pipe = popen(cmd.c_str(), "r");
    if (pipe == nullptr) {
        return std::nullopt;
    }

    std::string output;
    char buf[512];
    while (fgets(buf, sizeof(buf), pipe) != nullptr) {
        output += buf;
    }

    int status = pclose(pipe);
#ifdef _WIN32
    if (status != 0) return std::nullopt;
#else
    if (WEXITSTATUS(status) != 0) return std::nullopt;
#endif

    // Trim trailing whitespace.
    while (!output.empty() &&
           (output.back() == '\n' || output.back() == '\r')) {
        output.pop_back();
    }
    return output;
}

// ── Label parsing ────────────────────────────────────────────────────────────

/// Parse a Bazel label into (package, name).
static std::pair<std::string, std::string> parse_label(
    const std::string& label) {
    std::string raw = label;

    // Strip any repo prefix  (@@repo//… , @repo//… , //…)
    auto pos = raw.find("//");
    if (pos != std::string::npos) {
        raw = raw.substr(pos + 2);
    } else if (!raw.empty() && raw[0] == ':') {
        raw = raw.substr(1);
    }

    auto colon = raw.find(':');
    if (colon != std::string::npos) {
        return {raw.substr(0, colon), raw.substr(colon + 1)};
    }
    auto slash = raw.rfind('/');
    std::string name =
        (slash != std::string::npos) ? raw.substr(slash + 1) : raw;
    return {raw, name};
}

// ── Path helpers ─────────────────────────────────────────────────────────────

static std::string path_join(const std::string& a, const std::string& b) {
    if (a.empty()) return b;
    if (b.empty()) return a;
    if (a.back() == '/' || a.back() == '\\') return a + b;
    return a + "/" + b;
}

static std::string dag_file_path(const std::string& bazel_bin,
                                 const std::string& target) {
    auto [pkg, name] = parse_label(target);
    return path_join(path_join(bazel_bin, pkg),
                     path_join("_result_query", name + ".dag.json"));
}

// ── JSON / DAG parsing ───────────────────────────────────────────────────────

static std::map<std::string, std::string> parse_string_map(
    const nlohmann::json& obj) {
    std::map<std::string, std::string> m;
    for (auto& [k, v] : obj.items()) {
        m[k] = v.get<std::string>();
    }
    return m;
}

static std::vector<DagNode> parse_dag(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) {
        return {};
    }
    nlohmann::json arr = nlohmann::json::parse(f, /*cb=*/nullptr,
                                               /*allow_exceptions=*/false);
    if (arr.is_discarded() || !arr.is_array()) {
        return {};
    }

    std::vector<DagNode> nodes;
    nodes.reserve(arr.size());
    for (auto& item : arr) {
        DagNode node;
        node.label = item.value("label", "");
        if (item.contains("cache"))
            node.cache = parse_string_map(item["cache"]);
        if (item.contains("define"))
            node.define = parse_string_map(item["define"]);
        if (item.contains("subst"))
            node.subst = parse_string_map(item["subst"]);
        if (item.contains("deps")) {
            for (auto& d : item["deps"]) {
                node.deps.push_back(d.get<std::string>());
            }
        }
        nodes.push_back(std::move(node));
    }
    return nodes;
}

using Graph = std::map<std::string, const DagNode*>;

static Graph build_graph(const std::vector<DagNode>& nodes) {
    Graph g;
    for (const auto& n : nodes) {
        g[n.label] = &n;
    }
    return g;
}

static std::vector<std::string> find_roots(const Graph& graph) {
    std::set<std::string> all;
    std::set<std::string> referenced;
    for (auto& [label, node] : graph) {
        all.insert(label);
        for (auto& d : node->deps) {
            referenced.insert(d);
        }
    }
    std::vector<std::string> roots;
    for (auto& l : all) {
        if (referenced.find(l) == referenced.end()) {
            roots.push_back(l);
        }
    }
    if (roots.empty()) {
        for (auto& l : all) roots.push_back(l);
    }
    std::sort(roots.begin(), roots.end());
    return roots;
}

// ── Result file reading ──────────────────────────────────────────────────────

static std::optional<ResultValue> read_result(const std::string& execroot,
                                              const std::string& file_path) {
    std::string full = path_join(execroot, file_path);
    std::ifstream f(full);
    if (!f.is_open()) return std::nullopt;

    nlohmann::json data =
        nlohmann::json::parse(f, /*cb=*/nullptr, /*allow_exceptions=*/false);
    if (data.is_discarded() || !data.is_object()) return std::nullopt;

    for (auto& [var_name, var_data] : data.items()) {
        bool success = var_data.value("success", false);
        ResultValue rv;
        rv.success = success;

        if (var_data.contains("value") && !var_data["value"].is_null()) {
            const auto& val = var_data["value"];
            std::string raw;
            if (val.is_string()) {
                raw = val.get<std::string>();
            } else {
                // Handle numeric or boolean values.
                raw = val.dump();
            }
            // Values are sometimes JSON-encoded strings (e.g. "\"foo\"").
            auto decoded = nlohmann::json::parse(raw, /*cb=*/nullptr,
                                                 /*allow_exceptions=*/false);
            if (!decoded.is_discarded() && decoded.is_string()) {
                rv.display = decoded.get<std::string>();
            } else {
                rv.display = raw;
            }
        } else {
            rv.display = success ? "yes" : "no";
        }
        return rv;
    }
    return std::nullopt;
}

// ── Tree printer ─────────────────────────────────────────────────────────────

/// One result item to display inside a tree node.
struct DisplayItem {
    std::string type;  // "cache", "define", "subst"
    std::string key;
    std::string file_path;
};

static std::vector<DisplayItem> collect_items(const DagNode& node,
                                              const std::string& filter_type,
                                              const std::string& filter_key) {
    std::vector<DisplayItem> items;

    auto maybe_add = [&](const std::string& type,
                         const std::map<std::string, std::string>& bucket) {
        if (!filter_type.empty() && type != filter_type) return;
        for (auto& [key, path] : bucket) {
            if (!filter_key.empty() && key != filter_key) continue;
            items.push_back({type, key, path});
        }
    };

    maybe_add("cache", node.cache);
    maybe_add("define", node.define);
    maybe_add("subst", node.subst);
    return items;
}

static void print_tree(const Graph& graph, const std::string& label,
                       const std::string& execroot,
                       const std::string& filter_type,
                       const std::string& filter_key, bool read_values,
                       const std::string& prefix, bool is_last, bool is_root,
                       std::set<std::string>& visited) {
    auto it = graph.find(label);
    if (it == graph.end()) return;
    const DagNode& node = *it->second;

    // Print this node's label.
    if (is_root) {
        std::cout << label << "\n";
    } else {
        std::cout << prefix << (is_last ? kElbow : kTee) << label << "\n";
    }
    std::string child_prefix =
        is_root ? "" : prefix + (is_last ? kSpace : kPipe);

    if (visited.count(label) != 0) {
        std::cout << child_prefix << kElbow << "(...already shown above)\n";
        return;
    }
    visited.insert(label);

    // Gather result items + dep children.
    auto items = collect_items(node, filter_type, filter_key);

    std::vector<std::string> dep_labels;
    for (auto& d : node.deps) {
        if (graph.count(d) != 0) dep_labels.push_back(d);
    }

    std::size_t total = items.size() + dep_labels.size();
    std::size_t idx = 0;

    // Print result items.
    for (auto& item : items) {
        ++idx;
        bool last_child = (idx == total);
        std::cout << child_prefix << (last_child ? kElbow : kTee) << item.type
                  << ": " << item.key;

        if (read_values) {
            auto rv = read_result(execroot, item.file_path);
            if (rv.has_value()) {
                std::cout << " = " << rv->display;
                std::cout << (rv->success ? kCheck : kCross);
            }
        }

        if (!filter_key.empty() && item.key == filter_key) {
            std::cout << kArrow;
        }

        std::cout << "\n";
    }

    // Recurse into deps.
    for (auto& dep : dep_labels) {
        ++idx;
        bool last_child = (idx == total);
        print_tree(graph, dep, execroot, filter_type, filter_key, read_values,
                   child_prefix, last_child, /*is_root=*/false, visited);
    }
}

// ── Key search helpers ───────────────────────────────────────────────────────

/// Return true if @p node contains a result matching @p filter_type / @p key.
static bool node_has_key(const DagNode& node, const std::string& filter_type,
                         const std::string& key) {
    auto check = [&](const std::string& type,
                     const std::map<std::string, std::string>& bucket) {
        if (!filter_type.empty() && type != filter_type) return false;
        return bucket.count(key) != 0;
    };
    return check("cache", node.cache) || check("define", node.define) ||
           check("subst", node.subst);
}

/// Compute the set of node labels that lie on a path from any root to a node
/// that contains @p filter_key.  Only these labels should be printed in key-
/// search mode so that irrelevant branches are pruned.
static std::set<std::string> compute_relevant_nodes(
    const Graph& graph, const std::string& filter_type,
    const std::string& filter_key) {
    // Memoisation cache:  label → "can this node reach a match?"
    std::map<std::string, bool> cache;

    // Recursive DFS.  A node is relevant when it *is* a match or any of its
    // deps (transitively) is.
    std::function<bool(const std::string&)> reaches_match =
        [&](const std::string& label) -> bool {
        auto it = cache.find(label);
        if (it != cache.end()) return it->second;

        // Tentatively mark false to break cycles.
        cache[label] = false;

        auto git = graph.find(label);
        if (git == graph.end()) return false;

        if (node_has_key(*git->second, filter_type, filter_key)) {
            cache[label] = true;
            return true;
        }

        for (auto& dep : git->second->deps) {
            if (reaches_match(dep)) {
                cache[label] = true;
                return true;
            }
        }
        return false;
    };

    std::set<std::string> relevant;
    for (auto& [label, _] : graph) {
        if (reaches_match(label)) {
            relevant.insert(label);
        }
    }
    return relevant;
}

/// Print only the paths from @p label to nodes that match @p filter_key.
/// Intermediate (non-matching) nodes show just their label; matching nodes
/// show the matching result items.
static void print_key_search(const Graph& graph, const std::string& label,
                             const std::string& execroot,
                             const std::string& filter_type,
                             const std::string& filter_key, bool read_values,
                             const std::set<std::string>& relevant,
                             const std::string& prefix, bool is_last,
                             bool is_root, std::set<std::string>& visited) {
    auto it = graph.find(label);
    if (it == graph.end()) return;
    const DagNode& node = *it->second;

    // Print node label.
    if (is_root) {
        std::cout << label << "\n";
    } else {
        std::cout << prefix << (is_last ? kElbow : kTee) << label << "\n";
    }
    std::string child_prefix =
        is_root ? "" : prefix + (is_last ? kSpace : kPipe);

    if (visited.count(label) != 0) {
        return;
    }
    visited.insert(label);

    // Only show result items if this node actually has the match.
    auto items = collect_items(node, filter_type, filter_key);

    // Only follow deps that are relevant (on a path to a match).
    std::vector<std::string> relevant_deps;
    for (auto& d : node.deps) {
        if (relevant.count(d) != 0) {
            relevant_deps.push_back(d);
        }
    }

    std::size_t total = items.size() + relevant_deps.size();
    std::size_t idx = 0;

    for (auto& item : items) {
        ++idx;
        bool last_child = (idx == total);
        std::cout << child_prefix << (last_child ? kElbow : kTee) << item.type
                  << ": " << item.key;

        if (read_values) {
            auto rv = read_result(execroot, item.file_path);
            if (rv.has_value()) {
                std::cout << " = " << rv->display;
                std::cout << (rv->success ? kCheck : kCross);
            }
        }
        std::cout << kArrow << "\n";
    }

    for (auto& dep : relevant_deps) {
        ++idx;
        bool last_child = (idx == total);
        print_key_search(graph, dep, execroot, filter_type, filter_key,
                         read_values, relevant, child_prefix, last_child,
                         /*is_root=*/false, visited);
    }
}

static void print_available_keys(const Graph& graph,
                                 const std::string& filter_type) {
    std::map<std::string, std::set<std::string>> by_type;

    for (auto& [_, node] : graph) {
        auto collect = [&](const std::string& type,
                           const std::map<std::string, std::string>& bucket) {
            if (!filter_type.empty() && type != filter_type) return;
            for (auto& [k, v] : bucket) {
                by_type[type].insert(k);
            }
        };
        collect("cache", node->cache);
        collect("define", node->define);
        collect("subst", node->subst);
    }

    std::cout << "Available keys:\n";
    for (auto& [type, keys] : by_type) {
        std::cout << "  " << type << ":\n";
        for (auto& k : keys) {
            std::cout << "    " << k << "\n";
        }
    }
}

// ── Argument parsing ─────────────────────────────────────────────────────────

static void print_usage() {
    std::cerr
        << "Usage: bazel run //tools/result_query -- <target> [options]\n"
        << "\n"
        << "Options:\n"
        << "  --type, -t  cache|define|subst   Filter by result type\n"
        << "  --key,  -k  KEY                  Filter by key name\n"
        << "  --no-values                      Skip reading result values\n";
}

static std::optional<Args> parse_args(int argc, char* argv[]) {
    Args args;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];

        if (arg == "--type" || arg == "-t") {
            if (++i >= argc) {
                std::cerr << "Missing value for " << arg << "\n";
                return std::nullopt;
            }
            args.filter_type = argv[i];
            if (args.filter_type != "cache" && args.filter_type != "define" &&
                args.filter_type != "subst") {
                std::cerr << "Invalid --type: " << args.filter_type
                          << " (expected cache, define, or subst)\n";
                return std::nullopt;
            }
        } else if (arg == "--key" || arg == "-k") {
            if (++i >= argc) {
                std::cerr << "Missing value for " << arg << "\n";
                return std::nullopt;
            }
            args.filter_key = argv[i];
        } else if (arg == "--no-values") {
            args.read_values = false;
        } else if (arg == "--help" || arg == "-h") {
            print_usage();
            std::exit(0);
        } else if (arg[0] == '-') {
            std::cerr << "Unknown flag: " << arg << "\n";
            print_usage();
            return std::nullopt;
        } else if (args.target.empty()) {
            args.target = arg;
        } else {
            std::cerr << "Unexpected positional argument: " << arg << "\n";
            return std::nullopt;
        }
    }
    if (args.target.empty()) {
        print_usage();
        return std::nullopt;
    }
    return args;
}

// ── Main ─────────────────────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    auto args = parse_args(argc, argv);
    if (!args) return 1;

    // Resolve workspace directory.
    const char* ws_env = std::getenv("BUILD_WORKSPACE_DIRECTORY");
    std::string workspace = ws_env != nullptr ? ws_env : ".";

    // Save and change to workspace directory so bazel commands work.
    // (bazel run already sets cwd, but BUILD_WORKSPACE_DIRECTORY is reliable.)
    std::string original_cwd;
    {
        char cwd[4096];
        if (getcwd(cwd, sizeof(cwd)) != nullptr) original_cwd = cwd;
    }
    if (chdir(workspace.c_str()) != 0) {
        std::cerr << "Failed to chdir to workspace: " << workspace << "\n";
        return 1;
    }

    // 1. Build the target with the result_query aspect.
    std::cerr << "Building " << args->target
              << " with result_query aspect...\n";
    {
        std::string cmd = "bazel build ";
        cmd += kAspectFlag;
        cmd += " ";
        cmd += kOutputGroupsFlag;
        cmd += " ";
        cmd += args->target;
        int ret = std::system(cmd.c_str());
        if (ret != 0) {
            std::cerr << "bazel build failed\n";
            return 1;
        }
    }

    // 2. Locate output paths.
    auto bazel_bin = capture("bazel info bazel-bin 2>/dev/null");
    if (!bazel_bin) {
        std::cerr << "Failed to get bazel-bin path\n";
        return 1;
    }

    auto execroot = capture("bazel info execution_root 2>/dev/null");
    if (!execroot) {
        std::cerr << "Failed to get execution_root path\n";
        return 1;
    }

    // 3. Parse the DAG.
    std::string dag_path = dag_file_path(*bazel_bin, args->target);
    auto nodes = parse_dag(dag_path);
    if (nodes.empty()) {
        std::cerr << "No autoconf results found (DAG file: " << dag_path
                  << ")\n";
        return 1;
    }

    Graph graph = build_graph(nodes);
    auto roots = find_roots(graph);

    // 4. Key search mode — prune to only paths that lead to a match.
    if (!args->filter_key.empty()) {
        auto relevant =
            compute_relevant_nodes(graph, args->filter_type, args->filter_key);
        if (relevant.empty()) {
            std::string type_label =
                args->filter_type.empty() ? "any" : args->filter_type;
            std::cout << "Key '" << args->filter_key
                      << "' not found (type=" << type_label << ").\n\n";
            print_available_keys(graph, args->filter_type);
            return 0;
        }

        std::string type_label =
            args->filter_type.empty() ? "any" : args->filter_type;
        std::cout << "\nSearching for " << type_label << ": "
                  << args->filter_key << "\n\n";

        for (std::size_t i = 0; i < roots.size(); ++i) {
            if (relevant.count(roots[i]) == 0) continue;
            if (i > 0) std::cout << "\n";
            std::set<std::string> visited;
            print_key_search(graph, roots[i], *execroot, args->filter_type,
                             args->filter_key, args->read_values, relevant,
                             /*prefix=*/"", /*is_last=*/true,
                             /*is_root=*/true, visited);
        }
        std::cout << "\n";
        return 0;
    }

    // 5. Full tree mode.
    std::cout << "\n";
    for (std::size_t i = 0; i < roots.size(); ++i) {
        if (i > 0) std::cout << "\n";
        std::set<std::string> visited;
        print_tree(graph, roots[i], *execroot, args->filter_type,
                   args->filter_key, args->read_values,
                   /*prefix=*/"", /*is_last=*/true, /*is_root=*/true, visited);
    }
    std::cout << "\n";
    return 0;
}
