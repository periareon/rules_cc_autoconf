#include "autoconf/private/checker/process.h"

#ifndef _WIN32
#include <fcntl.h>
#include <spawn.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cerrno>
#include <system_error>

extern char** environ;

namespace rules_cc_autoconf {
namespace {

void check_error(int err, const std::string& operation) {
    if (err != 0) {
        throw std::system_error(err, std::generic_category(), operation);
    }
}

struct FileActions {
    posix_spawn_file_actions_t actions;

    FileActions() {
        check_error(posix_spawn_file_actions_init(&actions),
                    "posix_spawn_file_actions_init");
    }

    ~FileActions() { posix_spawn_file_actions_destroy(&actions); }

    void redirect(int fd, const std::filesystem::path& path) {
        check_error(
            posix_spawn_file_actions_addopen(
                &actions, fd, path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0666),
            "redirect child output to " + path.string());
    }
};

}  // namespace

int run_process(const std::vector<std::string>& args,
                const std::filesystem::path& stdout_path,
                const std::filesystem::path& stderr_path) {
    FileActions actions;
    if (!stdout_path.empty()) actions.redirect(STDOUT_FILENO, stdout_path);
    if (!stderr_path.empty()) actions.redirect(STDERR_FILENO, stderr_path);

    std::vector<char*> argv;
    argv.reserve(args.size() + 1);
    for (const std::string& arg : args) {
        argv.push_back(const_cast<char*>(arg.c_str()));
    }
    argv.push_back(nullptr);

    const std::string operation = "start " + args.front();
    pid_t pid;
    check_error(posix_spawnp(&pid, argv.front(), &actions.actions, nullptr,
                             argv.data(), environ),
                operation);
    int status;
    while (waitpid(pid, &status, 0) == -1) {
        const int err = errno;
        if (err != EINTR) check_error(err, "wait for " + args.front());
    }
    if (WIFSIGNALED(status)) {
        throw std::system_error(std::make_error_code(std::errc::interrupted),
                                args.front() + " terminated by signal " +
                                    std::to_string(WTERMSIG(status)));
    }
    const int exit_code = WEXITSTATUS(status);
    // POSIX allows spawn setup/exec failures to be reported by the child.
    // Do not cache this ambiguous status as an absent compiler capability.
    if (exit_code == 127) {
        throw std::system_error(
            std::make_error_code(std::errc::io_error),
            args.front() +
                " exited with status 127 (possible startup failure)");
    }
    return exit_code;
}

}  // namespace rules_cc_autoconf
#endif
