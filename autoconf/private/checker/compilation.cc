#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <sstream>
#include <system_error>

#ifndef _WIN32
#include <sys/wait.h>
#else
#include <windows.h>
#endif

#include "autoconf/private/checker/check_runner.h"
#include "autoconf/private/checker/debug_logger.h"

namespace rules_cc_autoconf {

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
    if (arg.find(' ') != std::string::npos) {
#ifdef _WIN32
        // Windows: use double quotes
        return "\"" + arg + "\"";
#else
        // Unix: use single quotes for simplicity
        return "'" + arg + "'";
#endif
    }
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
    // Replace invalid filesystem characters with underscores
    for (char& c : result) {
        if (c == '/' || c == '\\' || c == ':' || c == '*' || c == '?' ||
            c == '"' || c == '<' || c == '>' || c == '|') {
            c = '_';
        }
    }
    return result;
}
}  // namespace

std::vector<std::string> CheckRunner::filter_error_flags(
    const std::vector<std::string>& flags) {
    std::vector<std::string> filtered;

    for (const std::string& flag : flags) {
        // Skip flags that promote warnings to errors
        // Configuration checks need to allow warnings since we expect some
        // checks to fail or produce warnings
        if (flag == "-Werror" || flag == "/WX" || flag == "-Werror=all") {
            continue;
        }
        // Skip individual -Werror= flags
        if (flag.rfind("-Werror=", 0) == 0) {
            continue;
        }
        // Skip -Wincompatible-library-redeclaration which fails on function
        // checks
        if (flag == "-Wincompatible-library-redeclaration") {
            continue;
        }
        filtered.push_back(flag);
    }

    return filtered;
}

std::vector<std::string> CheckRunner::get_compiler_and_flags(
    const std::string& language) {
    std::vector<std::string> cmd;

    if (language == "cpp" || language == "c++") {
        DebugLogger::debug("C++ compiler path: [" + config_.cpp_compiler + "]");
        cmd.push_back(config_.cpp_compiler);
        std::vector<std::string> filtered_flags =
            filter_error_flags(config_.cpp_flags);
        cmd.insert(cmd.end(), filtered_flags.begin(), filtered_flags.end());
    } else {
        DebugLogger::debug("C compiler path: [" + config_.c_compiler + "]");
        cmd.push_back(config_.c_compiler);
        std::vector<std::string> filtered_flags =
            filter_error_flags(config_.c_flags);
        cmd.insert(cmd.end(), filtered_flags.begin(), filtered_flags.end());
    }

    return cmd;
}

std::vector<std::string> CheckRunner::get_compiler_and_link_flags(
    const std::string& language) {
    std::vector<std::string> cmd{};

    if (language == "cpp" || language == "c++") {
        DebugLogger::debug("C++ compiler path (for linking): [" +
                           config_.cpp_compiler + "]");
        cmd.push_back(config_.cpp_compiler);
        std::vector<std::string> filtered_flags =
            filter_error_flags(config_.cpp_flags);
        cmd.insert(cmd.end(), filtered_flags.begin(), filtered_flags.end());
        // Add linker flags
        std::vector<std::string> filtered_link_flags =
            filter_error_flags(config_.cpp_link_flags);
        cmd.insert(cmd.end(), filtered_link_flags.begin(),
                   filtered_link_flags.end());
    } else {
        DebugLogger::debug("C compiler path (for linking): [" +
                           config_.c_compiler + "]");
        cmd.push_back(config_.c_compiler);
        std::vector<std::string> filtered_flags =
            filter_error_flags(config_.c_flags);
        cmd.insert(cmd.end(), filtered_flags.begin(), filtered_flags.end());
        // Add linker flags
        std::vector<std::string> filtered_link_flags =
            filter_error_flags(config_.c_link_flags);
        cmd.insert(cmd.end(), filtered_link_flags.begin(),
                   filtered_link_flags.end());
    }

    return cmd;
}

std::string CheckRunner::get_file_extension(const std::string& language) {
    if (language == "cpp" || language == "c++") {
        return ".cpp";
    } else {
        return ".c";
    }
}

bool CheckRunner::try_compile(const std::string& code,
                              const std::string& language,
                              const std::string& unique_id) {
    // Create temporary directory with random name
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    std::filesystem::path tmp_dir =
        std::filesystem::temp_directory_path() /
        ("rules_cc_autoconf_" + std::to_string(dis(gen)));

    std::filesystem::create_directory(tmp_dir);

    // Sanitize unique_id for use in filename
    std::string safe_id = sanitize_for_filename(unique_id);

    // Write source file
    std::filesystem::path source_file =
        tmp_dir / (safe_id + get_file_extension(language));
    std::ofstream source(source_file);
    if (!source.is_open()) {
        DebugLogger::warn("Failed to create source file");
        std::filesystem::remove_all(tmp_dir);
        return false;
    }
    source << code;
    source.close();

    // Prepare compilation command
    std::vector<std::string> cmd = get_compiler_and_flags(language);

    // Check if using MSVC based on compiler_type
    bool is_msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    if (is_msvc) {
        // MSVC uses /c for compile-only and /Fo for object output
        cmd.push_back("/c");
        std::filesystem::path obj_path = tmp_dir / (safe_id + ".obj");
        cmd.push_back("/Fo" + obj_path.string());
        cmd.push_back(source_file.string());
    } else {
        // GCC/Clang style
        cmd.push_back("-c");
        cmd.push_back(source_file.string());
        cmd.push_back("-o");
        cmd.push_back((tmp_dir / (safe_id + ".o")).string());
    }

    // Execute compilation with output capture
    std::stringstream cmd_str;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmd_str << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmd_str << get_short_path(cmd[i]);
#else
            cmd_str << quote_if_needed(cmd[i]);
#endif
        } else {
            cmd_str << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string full_cmd = cmd_str.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        full_cmd += " >NUL 2>&1";
#else
        full_cmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing compile command: " + full_cmd);

    int result = std::system(full_cmd.c_str());

    // Clean up
    std::filesystem::remove_all(tmp_dir);

    // On Windows, system() returns the exit code directly
    // On Unix, system() returns a status that needs WEXITSTATUS
#ifdef _WIN32
    return result == 0;
#else
    return WEXITSTATUS(result) == 0;
#endif
}

bool CheckRunner::try_link(const std::filesystem::path& object_file,
                           const std::filesystem::path& executable,
                           const std::string& language) {
    // Prepare linking command
    std::vector<std::string> cmd;

    // Check if using MSVC based on compiler_type
    bool is_msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    if (is_msvc) {
        // MSVC: always use link.exe (linker tool from cc_toolchain)
        cmd.push_back(config_.linker);

        DebugLogger::debug("Linker tool path: [" + config_.linker + "]");

        // Get link flags (filtered for error flags only)
        std::vector<std::string> filtered_link_flags;
        if (language == "cpp" || language == "c++") {
            filtered_link_flags = filter_error_flags(config_.cpp_link_flags);
        } else {
            filtered_link_flags = filter_error_flags(config_.c_link_flags);
        }

        // link.exe format: flags first, then /OUT:, then object files
        // Add linker flags first
        if (!filtered_link_flags.empty()) {
            cmd.insert(cmd.end(), filtered_link_flags.begin(),
                       filtered_link_flags.end());
        }
        // Add output file with /OUT:
        cmd.push_back("/OUT:" + executable.string());
        // Add the object file
        cmd.push_back(object_file.string());
    } else {
        // GCC/Clang style - use linker tool if available, otherwise use
        // compiler
        std::string link_tool =
            config_.linker.empty()
                ? (language == "cpp" || language == "c++" ? config_.cpp_compiler
                                                          : config_.c_compiler)
                : config_.linker;

        if (!config_.linker.empty()) {
            DebugLogger::debug("Linker tool path: [" + config_.linker + "]");
        } else {
            DebugLogger::debug("Using compiler as linker: [" + link_tool + "]");
        }

        cmd.push_back(link_tool);

        // Get link flags (filtered)
        std::vector<std::string> filtered_link_flags;
        if (language == "cpp" || language == "c++") {
            filtered_link_flags = filter_error_flags(config_.cpp_link_flags);
        } else {
            filtered_link_flags = filter_error_flags(config_.c_link_flags);
        }
        cmd.insert(cmd.end(), filtered_link_flags.begin(),
                   filtered_link_flags.end());

        // Add the object file
        cmd.push_back(object_file.string());

        // Add output file
        cmd.push_back("-o");
        cmd.push_back(executable.string());
    }

    // Execute linking command
    std::stringstream cmd_str;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmd_str << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmd_str << get_short_path(cmd[i]);
#else
            cmd_str << quote_if_needed(cmd[i]);
#endif
        } else {
            cmd_str << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string full_cmd = cmd_str.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        full_cmd += " >NUL 2>&1";
#else
        full_cmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing link command: " + full_cmd);

    int result = std::system(full_cmd.c_str());
    // On Windows, system() returns the exit code directly
    // On Unix, system() returns a status that needs WEXITSTATUS
#ifdef _WIN32
    return result == 0;
#else
    return WEXITSTATUS(result) == 0;
#endif
}

std::optional<int> CheckRunner::try_compile_and_run(
    const std::string& code, const std::string& language,
    const std::string& unique_id) {
    // Create temporary directory with random name
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    std::filesystem::path tmp_dir = std::filesystem::temp_directory_path() /
                                    ("autoconf_" + std::to_string(dis(gen)));

    std::filesystem::create_directory(tmp_dir);

    // Sanitize unique_id for use in filename
    std::string safe_id = sanitize_for_filename(unique_id);

    // Write source file
    std::filesystem::path source_file =
        tmp_dir / (safe_id + get_file_extension(language));
    std::ofstream source(source_file);
    if (!source.is_open()) {
        DebugLogger::warn("Failed to create source file");
        std::filesystem::remove_all(tmp_dir);
        return std::nullopt;
    }
    source << code;
    source.close();

    // Step 1: Compile source to object file
    std::vector<std::string> cmd = get_compiler_and_flags(language);

    // Check if using MSVC based on compiler_type
    bool is_msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    std::filesystem::path object_file;
    if (is_msvc) {
        // MSVC uses /c for compile-only and /Fo for object output
        cmd.push_back("/c");
        object_file = tmp_dir / (safe_id + ".obj");
        cmd.push_back("/Fo" + object_file.string());
        cmd.push_back(source_file.string());
    } else {
        // GCC/Clang style
        cmd.push_back("-c");
        cmd.push_back(source_file.string());
        cmd.push_back("-o");
        object_file = tmp_dir / (safe_id + ".o");
        cmd.push_back(object_file.string());
    }

    // Execute compilation
    std::stringstream cmd_str;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmd_str << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmd_str << get_short_path(cmd[i]);
#else
            cmd_str << quote_if_needed(cmd[i]);
#endif
        } else {
            cmd_str << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string full_cmd = cmd_str.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        full_cmd += " >NUL 2>&1";
#else
        full_cmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing compile command: " + full_cmd);

    int compile_result = std::system(full_cmd.c_str());
    if (compile_result != 0) {
        DebugLogger::warn("Compilation failed");
        std::filesystem::remove_all(tmp_dir);
        return std::nullopt;
    }

    // Step 2: Link object file to executable
#ifdef _WIN32
    std::filesystem::path executable = tmp_dir / (safe_id + ".exe");
#else
    std::filesystem::path executable = tmp_dir / safe_id;
#endif

    bool link_result = try_link(object_file, executable, language);
    if (!link_result) {
        DebugLogger::warn("Linking failed");
        std::filesystem::remove_all(tmp_dir);
        return std::nullopt;
    }

    // Step 3: Run the executable
    std::string run_cmd = quote_if_needed(executable.string());
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        run_cmd += " >NUL 2>&1";
#else
        run_cmd += " >/dev/null 2>&1";
#endif
    }

    DebugLogger::debug("Executing run command: " + run_cmd);

    int run_result = std::system(run_cmd.c_str());

    // Clean up
    std::filesystem::remove_all(tmp_dir);

    // Return exit code
    // On Windows, system() returns the exit code directly
    // On Unix, system() returns a status that needs WEXITSTATUS
#ifdef _WIN32
    return run_result;
#else
    return WEXITSTATUS(run_result);
#endif
}

bool CheckRunner::try_compile_and_link_with_lib(const std::string& code,
                                                const std::string& library,
                                                const std::string& language,
                                                const std::string& unique_id) {
    // Create temporary directory with random name
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    std::filesystem::path tmp_dir =
        std::filesystem::temp_directory_path() /
        ("rules_cc_autoconf_" + std::to_string(dis(gen)));

    std::filesystem::create_directory(tmp_dir);

    // Sanitize unique_id for use in filename
    std::string safe_id = sanitize_for_filename(unique_id);

    // Write source file
    std::filesystem::path source_file =
        tmp_dir / (safe_id + get_file_extension(language));
    std::ofstream source(source_file);
    if (!source.is_open()) {
        DebugLogger::warn("Failed to create source file");
        std::filesystem::remove_all(tmp_dir);
        return false;
    }
    source << code;
    source.close();

    // Prepare compilation and linking command
    std::vector<std::string> cmd = get_compiler_and_link_flags(language);

    // Check if using MSVC based on compiler_type
    bool is_msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    std::filesystem::path executable;
    if (is_msvc) {
        // MSVC uses /Fe for executable output
        executable = tmp_dir / (safe_id + ".exe");
        cmd.push_back("/Fe" + executable.string());
        cmd.push_back(source_file.string());
        // Add library - MSVC uses .lib extension
        cmd.push_back(library + ".lib");
    } else {
        // GCC/Clang style - compile and link in one step
        cmd.push_back(source_file.string());
        cmd.push_back("-o");
        executable = tmp_dir / safe_id;
        cmd.push_back(executable.string());
        // Add library with -l prefix
        cmd.push_back("-l" + library);
    }

    // Execute compilation and linking with output capture
    std::stringstream cmd_str;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmd_str << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmd_str << get_short_path(cmd[i]);
#else
            cmd_str << quote_if_needed(cmd[i]);
#endif
        } else {
            cmd_str << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string full_cmd = cmd_str.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        full_cmd += " >NUL 2>&1";
#else
        full_cmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing compile and link command: " + full_cmd);

    int result = std::system(full_cmd.c_str());

    // Clean up
    std::filesystem::remove_all(tmp_dir);

    // On Windows, system() returns the exit code directly
    // On Unix, system() returns a status that needs WEXITSTATUS
#ifdef _WIN32
    return result == 0;
#else
    return WEXITSTATUS(result) == 0;
#endif
}

}  // namespace rules_cc_autoconf
