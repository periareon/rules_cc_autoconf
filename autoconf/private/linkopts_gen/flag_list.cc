#include "autoconf/private/linkopts_gen/flag_list.h"

#include <algorithm>
#include <sstream>

namespace rules_cc_autoconf {

namespace {

// Options whose argument is the following word. `-l`/`-L`/`-F` with a space
// are legal too but configure never emits them that way.
bool takes_separate_argument(const std::string& flag) {
    static const char* const kPaired[] = {
        "-framework",     "-weak_framework",     "-Xlinker",
        "-Wl,-framework", "-Wl,-weak_framework", "-arch",
    };
    return std::find(std::begin(kPaired), std::end(kPaired), flag) !=
           std::end(kPaired);
}

}  // namespace

std::vector<std::string> flatten_flags(const std::vector<std::string>& values) {
    // Each unit is one option plus its separate argument, if any.
    std::vector<std::vector<std::string>> units;
    for (const auto& value : values) {
        // A LIBS / LDFLAGS-style value is a whitespace-separated list of
        // options, e.g. gnulib's INTL_MACOSX_LIBS is
        // "-Wl,-framework -Wl,CoreFoundation -Wl,-framework -Wl,CoreServices".
        // Written as a single line the whole value becomes one linker
        // argument, which clang then splits on commas ("ld: unknown options:
        // -framework -Wl"), so the words are emitted one per line.
        std::istringstream tokens(value);
        std::string word;
        while (tokens >> word) {
            std::vector<std::string> unit{word};
            std::string argument;
            if (takes_separate_argument(word) && tokens >> argument) {
                unit.push_back(argument);
            }
            if (std::find(units.begin(), units.end(), unit) == units.end()) {
                units.push_back(unit);
            }
        }
    }

    std::vector<std::string> flags;
    for (const auto& unit : units) {
        flags.insert(flags.end(), unit.begin(), unit.end());
    }
    return flags;
}

}  // namespace rules_cc_autoconf
