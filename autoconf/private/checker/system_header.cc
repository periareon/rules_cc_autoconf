#include "autoconf/private/checker/system_header.h"

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <vector>

#ifdef _WIN32
#include <windows.h>
#endif

#include "autoconf/private/checker/config.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/checker/process.h"
#include "autoconf/private/common/file_util.h"

namespace rules_cc_autoconf {

namespace {

struct PreprocessorFiles {
    std::filesystem::path source;
    std::filesystem::path output;

    ~PreprocessorFiles() {
        std::error_code ec;
        file_remove(source, ec);
        file_remove(output, ec);
    }
};

#ifdef _WIN32
std::string get_short_path_sys(const std::string& long_path) {
    DWORD length = GetShortPathNameA(long_path.c_str(), nullptr, 0);
    if (length == 0) return "\"" + long_path + "\"";
    std::vector<char> buffer(length);
    DWORD result = GetShortPathNameA(long_path.c_str(), buffer.data(), length);
    if (result == 0 || result >= length) return "\"" + long_path + "\"";
    return std::string(buffer.data());
}
#endif

std::string quote_arg(const std::string& arg) {
    if (arg.find(' ') != std::string::npos) {
#ifdef _WIN32
        return "\"" + arg + "\"";
#else
        return "'" + arg + "'";
#endif
    }
    return arg;
}

bool ends_with(const std::string& str, const std::string& suffix) {
    if (suffix.size() > str.size()) return false;
    return str.compare(str.size() - suffix.size(), suffix.size(), suffix) == 0;
}

/**
 * Normalize path separators so that both `/` and `\` comparisons work
 * cross-platform when matching the header basename in line markers.
 */
std::string normalize_path_sep(const std::string& p) {
    std::string result = p;
    for (char& c : result) {
        if (c == '\\') c = '/';
    }
    return result;
}

}  // namespace

std::optional<std::filesystem::path> parse_line_markers(
    const std::string& preprocessor_output, const std::string& header) {
    std::istringstream stream(preprocessor_output);
    std::string line;
    std::string suffix = "/" + header;

    while (std::getline(stream, line)) {
        // GCC/Clang: # 1 "/usr/include/stddef.h" 1 3 4
        // MSVC:      #line 1 "C:\\Program Files\\...\\stddef.h"
        if (line.empty() || line[0] != '#') continue;

        // Find the first and last quote to extract the path
        size_t first_quote = line.find('"');
        if (first_quote == std::string::npos) continue;
        size_t last_quote = line.rfind('"');
        if (last_quote == first_quote) continue;

        std::string path =
            line.substr(first_quote + 1, last_quote - first_quote - 1);
        if (path.empty()) continue;

        std::string normalized = normalize_path_sep(path);
        if (ends_with(normalized, suffix) || normalized == header) {
            // Skip paths that look like our own conftest source
            if (normalized.find("conftest") != std::string::npos) continue;
            return std::filesystem::path(path);
        }
    }

    return std::nullopt;
}

std::optional<std::string> read_file_content(
    const std::filesystem::path& path) {
    std::ifstream file = open_ifstream(path);
    if (!file.is_open()) return std::nullopt;
    std::ostringstream buf;
    buf << file.rdbuf();
    return buf.str();
}

std::optional<std::filesystem::path> find_system_header_path(
    const std::string& compiler, const std::vector<std::string>& flags,
    const std::string& compiler_type, const std::string& header,
    const std::string& source_id, const std::filesystem::path& source_dir) {
    bool msvc = is_msvc_like(compiler_type);

    // Write a minimal source file that includes the target header
    std::string extension = ".c";
    std::string src_code = "#include <" + header + ">\n";
    std::filesystem::path src_path =
        source_dir / (source_id + ".gl_next" + extension);
    std::filesystem::path pp_out = source_dir / (source_id + ".gl_next.i");
    PreprocessorFiles cleanup{src_path, pp_out};

    {
        std::ofstream src = open_ofstream(src_path);
        if (!src.is_open()) {
            DebugLogger::warn("GL_NEXT_HEADER: failed to write source for " +
                              header);
            return std::nullopt;
        }
        src << src_code;
    }

    // Build the preprocessor command.
    std::vector<std::string> args{compiler};
    args.insert(args.end(), flags.begin(), flags.end());
    // /E retains the MSVC line markers needed to locate the header.
    args.insert(args.end(), {msvc ? "/E" : "-E", src_path.string()});
    if (!msvc) args.insert(args.end(), {"-o", pp_out.string()});

    std::ostringstream cmd;
#ifdef _WIN32
    cmd << get_short_path_sys(compiler);
#else
    cmd << quote_arg(compiler);
#endif

    for (size_t i = 1; i < args.size(); ++i) {
        cmd << " " << quote_arg(args[i]);
    }

    if (msvc) {
        cmd << " > " << quote_arg(pp_out.string()) << " 2>NUL";
    } else {
        cmd << " 2>/dev/null";
    }

    DebugLogger::debug("GL_NEXT_HEADER: running preprocessor: " + cmd.str());

#ifdef _WIN32
    int rc = std::system(cmd.str().c_str());
#else
    int rc =
        run_process(args, msvc ? pp_out : std::filesystem::path{}, "/dev/null");
#endif

    if (rc != 0) {
        DebugLogger::debug("GL_NEXT_HEADER: preprocessor failed for " + header +
                           " (rc=" + std::to_string(rc) + ")");
        return std::nullopt;
    }

    // Read and parse preprocessor output
    auto pp_content = read_file_content(pp_out);

    if (!pp_content.has_value()) {
        DebugLogger::warn("GL_NEXT_HEADER: could not read preprocessor output");
        return std::nullopt;
    }

    return parse_line_markers(*pp_content, header);
}

}  // namespace rules_cc_autoconf
