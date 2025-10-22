#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "autoconf/private/checker/check.h"
#include "autoconf/private/checker/check_result.h"
#include "autoconf/private/checker/config.h"

namespace rules_cc_autoconf {

/**
 * @brief Executes autoconf-style configuration checks.
 *
 * Runs various types of checks (headers, functions, types, etc.) by compiling
 * and optionally running test programs using the configured toolchain.
 */
class CheckRunner {
   public:
    /**
     * @brief Construct a CheckRunner with the given configuration.
     * @param config Configuration containing compiler info and checks to run.
     *               Must remain valid for the lifetime of CheckRunner.
     */
    explicit CheckRunner(const Config& config);

    /**
     * @brief Run all checks defined in the configuration.
     * @return Vector of CheckResult objects, one for each check.
     */
    std::vector<CheckResult> run_all_checks();

    /**
     * @brief Run a single check.
     * @param check Check object describing the check to perform.
     * @return CheckResult containing the result of the check.
     */
    CheckResult run_check(const Check& check);

    // Deleted copy and move assignment operators (const reference member)
    CheckRunner& operator=(const CheckRunner&) = delete;
    CheckRunner& operator=(CheckRunner&&) = delete;

   private:
    const Config& config_;                ///< Reference to the configuration
    std::vector<CheckResult> results_{};  ///< Accumulated check results

    /** @brief Check if a header file exists and can be included. */
    CheckResult check_header(const Check& check);

    /** @brief Check if a function exists and can be linked. */
    CheckResult check_function(const Check& check);

    /** @brief Check if a library provides a function. */
    CheckResult check_lib(const Check& check);

    /** @brief Check if a preprocessor symbol is defined. */
    CheckResult check_symbol(const Check& check);

    /** @brief Check if a type exists. */
    CheckResult check_type_check(const Check& check);

    /** @brief Check if code compiles successfully. */
    CheckResult check_compile(const Check& check);

    /** @brief Produce the define value for the check unconditionally. */
    CheckResult check_define(const Check& check);

    /** @brief Determine the size of a type. */
    CheckResult check_sizeof(const Check& check);

    /** @brief Determine the alignment of a type. */
    CheckResult check_alignof(const Check& check);

    /** @brief Compute an integer value at compile time. */
    CheckResult check_compute_int(const Check& check);

    /** @brief Determine system endianness. */
    CheckResult check_endian(const Check& check);

    /** @brief Check if a declaration exists. */
    CheckResult check_decl(const Check& check);

    /** @brief Check if a struct/union member exists. */
    CheckResult check_member(const Check& check);

    /**
     * @brief Try to compile code with the configured compiler.
     * @param code Source code to compile.
     * @param language Language of the code ("c" or "cpp").
     * @param unique_id Optional unique identifier for this check (used in
     * filenames).
     * @return true if compilation succeeded, false otherwise.
     */
    bool try_compile(const std::string& code, const std::string& language = "c",
                     const std::string& unique_id = "conftest");

    /**
     * @brief Try to link an object file into an executable.
     * @param object_file Path to the object file to link.
     * @param executable Path where the executable should be created.
     * @param language Language of the code ("c" or "cpp").
     * @return true if linking succeeded, false otherwise.
     */
    bool try_link(const std::filesystem::path& object_file,
                  const std::filesystem::path& executable,
                  const std::string& language = "c");

    /**
     * @brief Try to compile and run code, returning its exit code.
     * @param code Source code to compile and run.
     * @param language Language of the code ("c" or "cpp").
     * @param unique_id Optional unique identifier for this check (used in
     * filenames).
     * @return Exit code of the program, or std::nullopt if
     * compilation/execution failed.
     */
    std::optional<int> try_compile_and_run(
        const std::string& code, const std::string& language = "c",
        const std::string& unique_id = "conftest");

    /**
     * @brief Try to compile and link code with a specific library.
     * @param code Source code to compile and link.
     * @param library Library name (without -l prefix) to link against.
     * @param language Language of the code ("c" or "cpp").
     * @param unique_id Optional unique identifier for this check (used in
     * filenames).
     * @return true if compilation and linking succeeded, false otherwise.
     */
    bool try_compile_and_link_with_lib(
        const std::string& code, const std::string& library,
        const std::string& language = "c",
        const std::string& unique_id = "conftest");

    /**
     * @brief Filter out flags that promote warnings to errors.
     * @param flags Original compiler flags.
     * @return Filtered flags safe for configuration checks.
     */
    std::vector<std::string> filter_error_flags(
        const std::vector<std::string>& flags);

    /**
     * @brief Get compiler command and flags for a language.
     * @param language Language ("c" or "cpp").
     * @return Vector of compiler command parts (compiler path followed by
     * flags).
     */
    std::vector<std::string> get_compiler_and_flags(
        const std::string& language);

    /**
     * @brief Get compiler command with both compile and link flags.
     * @param language Language ("c" or "cpp").
     * @return Vector of compiler command parts (compiler path followed by
     * compile and link flags).
     */
    std::vector<std::string> get_compiler_and_link_flags(
        const std::string& language);

    /**
     * @brief Get file extension for a language.
     * @param language Language ("c" or "cpp").
     * @return File extension (".c" or ".cpp").
     */
    std::string get_file_extension(const std::string& language);
};

}  // namespace rules_cc_autoconf
