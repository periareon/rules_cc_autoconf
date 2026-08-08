#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <system_error>

#ifndef _WIN32
#include <sys/wait.h>
#else
#include <windows.h>
#endif

#include "autoconf/private/checker/check_runner.h"
#include "autoconf/private/checker/debug_logger.h"
#include "autoconf/private/common/file_util.h"

namespace rules_cc_autoconf {

constexpr const char* kCoptsMarker = "{rules_cc_autoconf:copts}";
constexpr const char* kLinkoptsMarker = "{rules_cc_autoconf:linkopts}";

namespace {

#ifdef _WIN32
/**
 * @brief Convert a long path to Windows 8.3 short path format (no spaces).
 *
 * This function is used to avoid issues with cmd.exe when paths contain spaces.
 * If conversion fails, returns the original path wrapped in quotes.
 *
 * @param long_path The long path to convert.
 * @return The short path (8.3 format) or quoted original path if conversion
 * fails.
 */
std::string get_short_path(const std::string& long_path) {
    // Get the required buffer size
    DWORD length = GetShortPathNameA(long_path.c_str(), nullptr, 0);
    if (length == 0) {
        // If conversion fails, return original path quoted
        return "\"" + long_path + "\"";
    }

    // Get the short path
    std::vector<char> buffer(length);
    DWORD result = GetShortPathNameA(long_path.c_str(), buffer.data(), length);
    if (result == 0 || result >= length) {
        // If conversion fails, return original path quoted
        return "\"" + long_path + "\"";
    }

    return std::string(buffer.data());
}
#endif

/**
 * @brief Quote an argument if it contains spaces.
 *
 * On Windows, uses double quotes. On Unix, uses single quotes.
 * This prevents command-line parsing issues when paths or arguments contain
 * spaces.
 *
 * @param arg The argument string to potentially quote.
 * @return The quoted argument if it contains spaces, otherwise the original
 * argument.
 */
std::string quote_if_needed(const std::string& arg) {
#ifdef _WIN32
    if (arg.find_first_of(" \t()&|<>^") != std::string::npos) {
        return "\"" + arg + "\"";
    }
#else
    if (arg.find_first_of(" \t()|&;<>$`*?[]{}!#~'\"\\") != std::string::npos) {
        std::string quoted = "'";
        for (char c : arg) {
            if (c == '\'') {
                quoted += "'\\''";
            } else {
                quoted += c;
            }
        }
        quoted += "'";
        return quoted;
    }
#endif
    return arg;
}

/**
 * @brief Sanitize a define name for use in filenames.
 *
 * Replaces invalid filesystem characters with underscores to create a safe
 * filename from a define name.
 *
 * @param define_name The define name to sanitize.
 * @return A sanitized string safe for use in filenames.
 */
std::string sanitize_for_filename(const std::string& define_name) {
    std::string result = define_name;
    for (char& c : result) {
        if (c == '/' || c == '\\' || c == ':' || c == '*' || c == '?' ||
            c == '"' || c == '<' || c == '>' || c == '|') {
            c = '_';
        }
    }
    return result;
}

/**
 * @brief Check if a language string refers to C++.
 */
bool is_cpp(const std::string& language) {
    return language == "cpp" || language == "c++";
}

/**
 * @brief Build a shell command string from a vector of arguments.
 *
 * On Windows, the first argument (compiler/linker path) is converted to
 * 8.3 short path format. All arguments with spaces are quoted.
 *
 * @param cmd Vector of command parts (program + arguments).
 * @return The assembled shell command string.
 */
std::string build_command_string(const std::vector<std::string>& cmd) {
    std::stringstream cmd_str;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmd_str << " ";
        if (i == 0) {
#ifdef _WIN32
            cmd_str << get_short_path(cmd[i]);
#else
            cmd_str << quote_if_needed(cmd[i]);
#endif
        } else {
            cmd_str << quote_if_needed(cmd[i]);
        }
    }
    return cmd_str.str();
}

/**
 * @brief Execute a shell command, optionally suppressing output.
 *
 * If verbose debug is not enabled, stdout and stderr are redirected to
 * /dev/null (or NUL on Windows).
 *
 * @param label A label for debug logging (e.g., "compile", "link").
 * @param cmd Vector of command parts.
 * @return The process exit code (already WEXITSTATUS-unwrapped on Unix).
 */
int run_command(const std::string& label, const std::vector<std::string>& cmd) {
    std::string full_cmd = build_command_string(cmd);

    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        full_cmd += " >NUL 2>&1";
#else
        full_cmd += " >/dev/null 2>&1";
#endif
    }

    DebugLogger::debug("Executing " + label + " command: " + full_cmd);

    int result = std::system(full_cmd.c_str());
#ifdef _WIN32
    return result;
#else
    return WEXITSTATUS(result);
#endif
}

/**
 * @brief RAII helper for managing build artifacts (source, object, executable).
 *
 * Files are written into the provided directory (next to the check JSON file)
 * using a globally unique name derived from the check JSON filename. Build
 * artifacts are cleaned up on destruction.
 */
struct BuildDir {
    std::filesystem::path dir;
    std::string safe_id;

    /**
     * @brief Create a build dir context.
     * @param unique_id An identifier used in filenames (derived from check JSON
     *                  filename, e.g. "ac_cv_header_stdio_h.check.conftest").
     * @param base_dir Directory to write files into (parent of the check JSON).
     */
    BuildDir(const std::string& unique_id,
             const std::filesystem::path& base_dir)
        : dir(base_dir), safe_id(sanitize_for_filename(unique_id)) {}

    ~BuildDir() {
        std::error_code ec;
        file_remove(dir / (safe_id + ".c"), ec);
        file_remove(dir / (safe_id + ".cpp"), ec);
        file_remove(dir / (safe_id + ".o"), ec);
        file_remove(dir / (safe_id + ".obj"), ec);
        file_remove(dir / (safe_id + ".exe"), ec);
        file_remove(dir / safe_id, ec);
    }

    /**
     * @brief Write source code to a file inside the build directory.
     * @param code The source code to write.
     * @param extension The file extension (e.g., ".c" or ".cpp").
     * @return The path to the written source file, or nullopt on failure.
     */
    std::optional<std::filesystem::path> write_source(
        const std::string& code, const std::string& extension) {
        std::filesystem::path source_file = dir / (safe_id + extension);
        std::ofstream source = open_ofstream(source_file);
        if (!source.is_open()) {
            DebugLogger::warn("Failed to create source file");
            return std::nullopt;
        }
        source << code;
        source.close();
        return source_file;
    }

    /** @brief Get the path for an object file. */
    std::filesystem::path object_path(bool msvc) const {
        return dir / (safe_id + (msvc ? ".obj" : ".o"));
    }

    /** @brief Get the path for an executable. */
    std::filesystem::path executable_path() const {
#ifdef _WIN32
        return dir / (safe_id + ".exe");
#else
        return dir / safe_id;
#endif
    }

    // Non-copyable, non-movable
    BuildDir(const BuildDir&) = delete;
    BuildDir& operator=(const BuildDir&) = delete;
};

}  // namespace

std::vector<std::string> CheckRunner::filter_error_flags(
    const std::vector<std::string>& flags) {
    // Strip warning-as-error promotions in BOTH dialects regardless of the
    // active compiler — clang-cl accepts both /WX and -Werror; the Bazel
    // toolchain may inject either. Filtering universally keeps probe
    // behavior identical across compilers.
    std::vector<std::string> filtered;
    filtered.reserve(flags.size());
    for (const std::string& flag : flags) {
        // gcc/clang: -Werror, -Werror=all, -Werror=<name>
        if (flag == "-Werror" || flag == "-Werror=all") continue;
        if (flag.rfind("-Werror=", 0) == 0) continue;
        // MSVC/clang-cl: /WX (all-as-error), /we<num> (single-as-error).
        // Preserve /WX- (explicit disable) — it's not a promotion.
        if (flag == "/WX") continue;
        if (flag.rfind("/we", 0) == 0 && flag.size() > 3 &&
            std::isdigit(static_cast<unsigned char>(flag[3]))) {
            continue;
        }
        // Clang-only warning that breaks the K&R probe prototype.
        if (flag == "-Wincompatible-library-redeclaration") continue;
        filtered.push_back(flag);
    }
    return filtered;
}

void CheckRunner::append_error_suppressions(
    std::vector<std::string>& cmd) const {
    // Modern clang/gcc escalate several C89-legacy warnings to errors by
    // default (without -Werror). Emit dialect-appropriate suppressions so
    // autoconf-shape probes (K&R prototypes, implicit int, etc.) survive.
    if (is_msvc_like(config_.compiler_type)) {
        // MSVC warning numbers; clang-cl maps these onto its clang-side
        // diagnostics via the cl-driver compatibility layer.
        //   C4013 implicit function declaration
        //   C4028 formal parameter differs from declaration
        //   C4029 declared parameter list different from definition
        //   C4047 differs in levels of indirection
        //   C4133 incompatible types (function pointer conversion)
        static const std::string kMsvcSuppressions[] = {
            "/wd4013", "/wd4028", "/wd4029", "/wd4047", "/wd4133",
        };
        cmd.insert(cmd.end(), std::begin(kMsvcSuppressions),
                   std::end(kMsvcSuppressions));
        return;
    }
    // gcc/clang: only suppressions BOTH understand — gcc errors out on
    // unknown -Wno-error=; "incompatible-pointer-types" covers what clang
    // spells "incompatible-function-pointer-types".
    static const std::string kGnuSuppressions[] = {
        "-Wno-error=strict-prototypes",
        "-Wno-error=implicit-function-declaration",
        "-Wno-error=implicit-int",
        "-Wno-error=int-conversion",
        "-Wno-error=incompatible-pointer-types",
    };
    cmd.insert(cmd.end(), std::begin(kGnuSuppressions),
               std::end(kGnuSuppressions));
}

std::vector<std::string> CheckRunner::replace_marker(
    const std::vector<std::string>& flags, const char* marker,
    const std::vector<std::string>& replacement) {
    std::vector<std::string> result;
    for (const auto& flag : flags) {
        if (flag == marker) {
            result.insert(result.end(), replacement.begin(), replacement.end());
        } else {
            result.push_back(flag);
        }
    }
    return result;
}

std::vector<std::string> CheckRunner::get_compiler_and_flags(
    const std::string& language, const std::vector<std::string>& extra_copts) {
    std::vector<std::string> cmd;
    if (is_cpp(language)) {
        DebugLogger::debug("C++ compiler path: [" + config_.cpp_compiler + "]");
        cmd.push_back(config_.cpp_compiler);
        auto filtered = filter_error_flags(config_.cpp_flags);
        auto processed = replace_marker(filtered, kCoptsMarker, extra_copts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
    } else {
        DebugLogger::debug("C compiler path: [" + config_.c_compiler + "]");
        cmd.push_back(config_.c_compiler);
        auto filtered = filter_error_flags(config_.c_flags);
        auto processed = replace_marker(filtered, kCoptsMarker, extra_copts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
    }
    append_error_suppressions(cmd);
    return cmd;
}

std::vector<std::string> CheckRunner::get_compiler_and_link_flags(
    const std::string& language, const std::vector<std::string>& extra_copts,
    const std::vector<std::string>& extra_linkopts) {
    std::vector<std::string> cmd;
    if (is_cpp(language)) {
        DebugLogger::debug("C++ compiler path (for linking): [" +
                           config_.cpp_compiler + "]");
        cmd.push_back(config_.cpp_compiler);
        auto filtered = filter_error_flags(config_.cpp_flags);
        auto processed = replace_marker(filtered, kCoptsMarker, extra_copts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
        append_error_suppressions(cmd);
        auto link_filtered = filter_error_flags(config_.cpp_link_flags);
        auto link_processed =
            replace_marker(link_filtered, kLinkoptsMarker, extra_linkopts);
        cmd.insert(cmd.end(), link_processed.begin(), link_processed.end());
    } else {
        DebugLogger::debug("C compiler path (for linking): [" +
                           config_.c_compiler + "]");
        cmd.push_back(config_.c_compiler);
        auto filtered = filter_error_flags(config_.c_flags);
        auto processed = replace_marker(filtered, kCoptsMarker, extra_copts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
        append_error_suppressions(cmd);
        auto link_filtered = filter_error_flags(config_.c_link_flags);
        auto link_processed =
            replace_marker(link_filtered, kLinkoptsMarker, extra_linkopts);
        cmd.insert(cmd.end(), link_processed.begin(), link_processed.end());
    }
    return cmd;
}

std::string CheckRunner::get_file_extension(const std::string& language) {
    return is_cpp(language) ? ".cpp" : ".c";
}

bool CheckRunner::try_compile(const std::string& code,
                              const std::string& language,
                              const std::vector<std::string>& extra_copts) {
    BuildDir tmp(source_id_, source_dir_);
    std::optional<std::filesystem::path> source_file =
        tmp.write_source(code, get_file_extension(language));
    if (!source_file) return false;

    std::vector<std::string> cmd =
        get_compiler_and_flags(language, extra_copts);
    bool msvc = is_msvc_like(config_.compiler_type);

    if (msvc) {
        cmd.push_back("/c");
        cmd.push_back("/Fo" + tmp.object_path(true).string());
        cmd.push_back(source_file->string());
    } else {
        cmd.push_back("-c");
        cmd.push_back(source_file->string());
        cmd.push_back("-o");
        cmd.push_back(tmp.object_path(false).string());
    }

    return run_command("compile", cmd) == 0;
}

bool CheckRunner::try_link(const std::filesystem::path& object_file,
                           const std::filesystem::path& executable,
                           const std::string& language,
                           const std::vector<std::string>& extra_linkopts) {
    std::vector<std::string> cmd;
    bool msvc = is_msvc_like(config_.compiler_type);

    if (msvc) {
        cmd.push_back(config_.linker);
        DebugLogger::debug("Linker tool path: [" + config_.linker + "]");
        auto link_flags = filter_error_flags(
            is_cpp(language) ? config_.cpp_link_flags : config_.c_link_flags);
        auto processed =
            replace_marker(link_flags, kLinkoptsMarker, extra_linkopts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
        cmd.push_back("/OUT:" + executable.string());
        cmd.push_back(object_file.string());
    } else {
        std::string link_tool =
            config_.linker.empty()
                ? (is_cpp(language) ? config_.cpp_compiler : config_.c_compiler)
                : config_.linker;
        if (!config_.linker.empty()) {
            DebugLogger::debug("Linker tool path: [" + config_.linker + "]");
        } else {
            DebugLogger::debug("Using compiler as linker: [" + link_tool + "]");
        }
        cmd.push_back(link_tool);
        auto link_flags = filter_error_flags(
            is_cpp(language) ? config_.cpp_link_flags : config_.c_link_flags);
        auto processed =
            replace_marker(link_flags, kLinkoptsMarker, extra_linkopts);
        cmd.insert(cmd.end(), processed.begin(), processed.end());
        cmd.push_back(object_file.string());
        cmd.push_back("-o");
        cmd.push_back(executable.string());
    }

    return run_command("link", cmd) == 0;
}

bool CheckRunner::try_compile_and_link(
    const std::string& code, const std::string& language,
    const std::vector<std::string>& extra_copts,
    const std::vector<std::string>& extra_linkopts) {
    BuildDir tmp(source_id_, source_dir_);
    std::optional<std::filesystem::path> source_file =
        tmp.write_source(code, get_file_extension(language));
    if (!source_file) return false;

    bool msvc = is_msvc_like(config_.compiler_type);

    if (msvc) {
        // On MSVC, compile and link in one cl.exe invocation. Using cl.exe
        // directly (instead of separate cl.exe /c + link.exe) ensures that
        // default libraries are linked, including legacy_stdio_definitions.lib
        // which provides linker symbols for UCRT inline functions like printf.
        std::vector<std::string> cmd =
            get_compiler_and_link_flags(language, extra_copts, extra_linkopts);
        std::filesystem::path exe = tmp.executable_path();
        cmd.push_back("/Fe" + exe.string());
        cmd.push_back(source_file->string());
        return run_command("compile and link", cmd) == 0;
    }

    // GCC/Clang: compile then link separately
    std::vector<std::string> cmd =
        get_compiler_and_flags(language, extra_copts);
    std::filesystem::path obj = tmp.object_path(false);

    cmd.push_back("-c");
    cmd.push_back(source_file->string());
    cmd.push_back("-o");
    cmd.push_back(obj.string());

    if (run_command("compile", cmd) != 0) {
        DebugLogger::warn("Compilation failed");
        return false;
    }

    // Step 2: Link
    std::filesystem::path exe = tmp.executable_path();
    return try_link(obj, exe, language, extra_linkopts);
}

bool CheckRunner::try_compile_and_link_with_lib(
    const std::string& code, const std::string& library,
    const std::string& language, const std::vector<std::string>& extra_copts,
    const std::vector<std::string>& extra_linkopts) {
    BuildDir tmp(source_id_, source_dir_);
    std::optional<std::filesystem::path> source_file =
        tmp.write_source(code, get_file_extension(language));
    if (!source_file) return false;

    // Extras go through the marker-substituting helper so -Wno-error=
    // suppressions stay after any caller-supplied -Werror=… (last-wins).
    std::vector<std::string> cmd =
        get_compiler_and_link_flags(language, extra_copts, extra_linkopts);
    bool msvc = is_msvc_like(config_.compiler_type);

    if (msvc) {
        std::filesystem::path exe = tmp.dir / (tmp.safe_id + ".exe");
        cmd.push_back("/Fe" + exe.string());
        cmd.push_back(source_file->string());
        cmd.push_back(library + ".lib");
    } else {
        cmd.push_back(source_file->string());
        cmd.push_back("-o");
        cmd.push_back((tmp.dir / tmp.safe_id).string());
        cmd.push_back("-l" + library);
    }

    return run_command("compile and link", cmd) == 0;
}

}  // namespace rules_cc_autoconf
