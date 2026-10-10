/**
 * @brief Turn LIBS / LDFLAGS-style values into a deduplicated flag list.
 */
#pragma once

#include <string>
#include <vector>

namespace rules_cc_autoconf {

/**
 * Split whitespace-separated option strings into one option per element,
 * dropping repeats, in first-seen order.
 *
 * Options that take their argument as the next word (`-framework
 * CoreServices`, `-Xlinker -rpath`, and their `-Wl,` spellings) are kept
 * together and compared as a unit, so a repeated `-Wl,-framework` is not
 * dropped from in front of a new framework name.
 *
 * @param values Variable values in the order they were given.
 * @return Flags, one option word per element.
 */
std::vector<std::string> flatten_flags(const std::vector<std::string>& values);

}  // namespace rules_cc_autoconf
