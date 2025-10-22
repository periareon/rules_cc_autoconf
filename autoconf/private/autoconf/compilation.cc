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

#include "autoconf/private/autoconf/check_runner.h"
#include "autoconf/private/autoconf/debug_logger.h"

namespace rules_cc_autoconf {

namespace {

#ifdef _WIN32
/**
 * @brief Convert a long path to Windows 8.3 short path format (no spaces).
 *
 * This function is used to avoid issues with cmd.exe when paths contain spaces.
 * If conversion fails, returns the original path wrapped in quotes.
 *
 * @param longPath The long path to convert.
 * @return The short path (8.3 format) or quoted original path if conversion
 * fails.
 */
std::string get_short_path(const std::string& longPath) {
    // Get the required buffer size
    DWORD length = GetShortPathNameA(longPath.c_str(), nullptr, 0);
    if (length == 0) {
        // If conversion fails, return original path quoted
        return "\"" + longPath + "\"";
    }

    // Get the short path
    std::vector<char> buffer(length);
    DWORD result = GetShortPathNameA(longPath.c_str(), buffer.data(), length);
    if (result == 0 || result >= length) {
        // If conversion fails, return original path quoted
        return "\"" + longPath + "\"";
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
                              const std::string& language) {
    // Create temporary directory with random name
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    std::filesystem::path tmpDir = std::filesystem::temp_directory_path() /
                                   ("autoconf_" + std::to_string(dis(gen)));

    std::filesystem::create_directory(tmpDir);

    // Write source file
    std::filesystem::path sourceFile =
        tmpDir / ("conftest" + get_file_extension(language));
    std::ofstream source(sourceFile);
    if (!source.is_open()) {
        DebugLogger::warn("Failed to create source file");
        std::filesystem::remove_all(tmpDir);
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
        cmd.push_back("/Fo" + (tmpDir / "conftest.obj").string());
        cmd.push_back(sourceFile.string());
    } else {
        // GCC/Clang style
        cmd.push_back("-c");
        cmd.push_back(sourceFile.string());
        cmd.push_back("-o");
        cmd.push_back((tmpDir / "conftest.o").string());
    }

    // Execute compilation with output capture
    std::stringstream cmdStr;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmdStr << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmdStr << get_short_path(cmd[i]);
#else
            cmdStr << quote_if_needed(cmd[i]);
#endif
        } else {
            cmdStr << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string fullCmd = cmdStr.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        fullCmd += " >NUL 2>&1";
#else
        fullCmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing compile command: " + fullCmd);

    int result = std::system(fullCmd.c_str());

    // Clean up
    std::filesystem::remove_all(tmpDir);

    return result == 0;
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
    std::stringstream cmdStr;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmdStr << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmdStr << get_short_path(cmd[i]);
#else
            cmdStr << quote_if_needed(cmd[i]);
#endif
        } else {
            cmdStr << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string fullCmd = cmdStr.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        fullCmd += " >NUL 2>&1";
#else
        fullCmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing link command: " + fullCmd);

    int result = std::system(fullCmd.c_str());
    return result == 0;
}

std::optional<int> CheckRunner::try_compile_and_run(
    const std::string& code, const std::string& language) {
    // Create temporary directory with random name
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dis(100000, 999999);

    std::filesystem::path tmpDir = std::filesystem::temp_directory_path() /
                                   ("autoconf_" + std::to_string(dis(gen)));

    std::filesystem::create_directory(tmpDir);

    // Write source file
    std::filesystem::path sourceFile =
        tmpDir / ("conftest" + get_file_extension(language));
    std::ofstream source(sourceFile);
    if (!source.is_open()) {
        DebugLogger::warn("Failed to create source file");
        std::filesystem::remove_all(tmpDir);
        return std::nullopt;
    }
    source << code;
    source.close();

    // Step 1: Compile source to object file
    std::vector<std::string> cmd = get_compiler_and_flags(language);

    // Check if using MSVC based on compiler_type
    bool is_msvc = config_.compiler_type.rfind("msvc", 0) == 0;

    std::filesystem::path objectFile;
    if (is_msvc) {
        // MSVC uses /c for compile-only and /Fo for object output
        cmd.push_back("/c");
        objectFile = tmpDir / "conftest.obj";
        cmd.push_back("/Fo" + objectFile.string());
        cmd.push_back(sourceFile.string());
    } else {
        // GCC/Clang style
        cmd.push_back("-c");
        cmd.push_back(sourceFile.string());
        cmd.push_back("-o");
        objectFile = tmpDir / "conftest.o";
        cmd.push_back(objectFile.string());
    }

    // Execute compilation
    std::stringstream cmdStr;
    for (size_t i = 0; i < cmd.size(); ++i) {
        if (i > 0) cmdStr << " ";
        // Special handling for the compiler path (first element) on Windows
        if (i == 0) {
#ifdef _WIN32
            // On Windows, convert to short path (8.3 format) to avoid space
            // issues with cmd.exe
            cmdStr << get_short_path(cmd[i]);
#else
            cmdStr << quote_if_needed(cmd[i]);
#endif
        } else {
            cmdStr << quote_if_needed(cmd[i]);
        }
    }

    // Redirect stdout and stderr unless debug is enabled
    std::string fullCmd = cmdStr.str();
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        fullCmd += " >NUL 2>&1";
#else
        fullCmd += " >/dev/null 2>&1";
#endif
    }

    // Always log the command being executed for debugging
    DebugLogger::debug("Executing compile command: " + fullCmd);

    int compileResult = std::system(fullCmd.c_str());
    if (compileResult != 0) {
        DebugLogger::warn("Compilation failed");
        std::filesystem::remove_all(tmpDir);
        return std::nullopt;
    }

    // Step 2: Link object file to executable
#ifdef _WIN32
    std::filesystem::path executable = tmpDir / "conftest.exe";
#else
    std::filesystem::path executable = tmpDir / "conftest";
#endif

    bool linkResult = try_link(objectFile, executable, language);
    if (!linkResult) {
        DebugLogger::warn("Linking failed");
        std::filesystem::remove_all(tmpDir);
        return std::nullopt;
    }

    // Step 3: Run the executable
    std::string runCmd = quote_if_needed(executable.string());
    if (!DebugLogger::is_verbose_debug_enabled()) {
#ifdef _WIN32
        runCmd += " >NUL 2>&1";
#else
        runCmd += " >/dev/null 2>&1";
#endif
    }

    DebugLogger::debug("Executing run command: " + runCmd);

    int runResult = std::system(runCmd.c_str());

    // Clean up
    std::filesystem::remove_all(tmpDir);

    // Return exit code
    // On Windows, system() returns the exit code directly
    // On Unix, system() returns a status that needs WEXITSTATUS
#ifdef _WIN32
    return runResult;
#else
    return WEXITSTATUS(runResult);
#endif
}

}  // namespace rules_cc_autoconf
