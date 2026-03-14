#include "autoconf/private/checker/system_header.h"

#include <cstdlib>
#include <fstream>
#include <sstream>
#include <vector>

#ifndef _WIN32
#include <sys/wait.h>
#else
#include <windows.h>
#endif

#include "autoconf/private/checker/debug_logger.h"

namespace rules_cc_autoconf {

namespace {

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
    std::ifstream file(path);
    if (!file.is_open()) return std::nullopt;
    std::ostringstream buf;
    buf << file.rdbuf();
    return buf.str();
}

std::optional<std::filesystem::path> find_system_header_path(
    const std::string& compiler, const std::vector<std::string>& flags,
    const std::string& compiler_type, const std::string& header,
    const std::string& source_id, const std::filesystem::path& source_dir) {
    bool msvc = compiler_type.rfind("msvc", 0) == 0;

    // Write a minimal source file that includes the target header
    std::string extension = ".c";
    std::string src_code = "#include <" + header + ">\n";
    std::filesystem::path src_path =
        source_dir / (source_id + ".gl_next" + extension);
    std::filesystem::path pp_out = source_dir / (source_id + ".gl_next.i");

    {
        std::ofstream src(src_path);
        if (!src.is_open()) {
            DebugLogger::warn("GL_NEXT_HEADER: failed to write source for " +
                              header);
            return std::nullopt;
        }
        src << src_code;
    }

    // Build the preprocessor command
    std::ostringstream cmd;
#ifdef _WIN32
    cmd << get_short_path_sys(compiler);
#else
    cmd << quote_arg(compiler);
#endif

    for (const std::string& f : flags) {
        cmd << " " << quote_arg(f);
    }

    if (msvc) {
        // MSVC: /E writes preprocessed output to stdout, /EP suppresses #line
        // markers so we use /E to keep them
        cmd << " /E " << quote_arg(src_path.string());
        cmd << " > " << quote_arg(pp_out.string()) << " 2>NUL";
    } else {
        cmd << " -E " << quote_arg(src_path.string());
        cmd << " -o " << quote_arg(pp_out.string()) << " 2>/dev/null";
    }

    DebugLogger::debug("GL_NEXT_HEADER: running preprocessor: " + cmd.str());

    int rc = std::system(cmd.str().c_str());
#ifndef _WIN32
    rc = WEXITSTATUS(rc);
#endif

    // Clean up source file
    std::error_code ec;
    std::filesystem::remove(src_path, ec);

    if (rc != 0) {
        DebugLogger::debug("GL_NEXT_HEADER: preprocessor failed for " + header +
                           " (rc=" + std::to_string(rc) + ")");
        std::filesystem::remove(pp_out, ec);
        return std::nullopt;
    }

    // Read and parse preprocessor output
    auto pp_content = read_file_content(pp_out);
    std::filesystem::remove(pp_out, ec);

    if (!pp_content.has_value()) {
        DebugLogger::warn("GL_NEXT_HEADER: could not read preprocessor output");
        return std::nullopt;
    }

    return parse_line_markers(*pp_content, header);
}

}  // namespace rules_cc_autoconf
