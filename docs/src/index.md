# rules_cc_autoconf

This module provides a rule to mimic [GNU Autoconf](https://www.gnu.org/software/autoconf/)
functionality, allowing you to generate `config.h` files by running compilation checks against
a cc_toolchain.

## Setup

```python
bazel_dep(name = "rules_cc_autoconf", version = "{version}")
```

## Overview

At a high level, Autoconf is a system for asking the C/C++ toolchain questions
like *“does this header exist?”*, *“is this function available?”* or *“what is
the size of this type?”* and then encoding the answers into a generated
`config.h`. Projects can then use preprocessor conditionals around those
defines instead of hard‑coding assumptions about the platform.

`rules_cc_autoconf` brings this style of configuration into **Bazel**. Rather
than running `./configure` as an opaque shell script, you describe checks in
Starlark (via the `macros` module) and run them with the `autoconf` rule. The
rule talks directly to the Bazel C/C++ toolchain, so it respects things like
platforms, features, remote execution and sandboxing.

Running traditional Autoconf inside Bazel (for example with a `genrule` that
invokes `./configure`) tends to be brittle: it often assumes a POSIX shell,
mutates the source tree, and performs many unrelated checks in one big step.
That makes caching and debugging harder, and is awkward on non‑Unix platforms
or in remote execution environments.

In contrast, this rule generates a small JSON config for each `autoconf`
target and runs a dedicated C++ tool that executes *only* the checks you
requested. That gives you:

- granular actions that cache well and are easy to reason about
- hermetic behaviour (no in‑tree `config.status`, `config.log`, etc.)
- configuration that is driven by Bazel’s toolchain and platform selection

The result is the same style of `config.h` you would expect from GNU Autoconf,
but integrated cleanly into Bazel builds.

## Why not just run Autoconf?

GNU [Autoconf](https://www.gnu.org/software/autoconf/) is excellent at probing
platform capabilities and generating Make-based build trees. However, dropping
`./configure` into a Bazel build has a few downsides:

- **Non-hermetic**: traditional Autoconf expects to mutate the source tree
  (creating `config.status`, `config.log`, generated headers, etc.), which
  conflicts with Bazel’s sandboxing and caching model.
- **Opaque, coarse-grained actions**: a single `configure` step can do dozens of
  unrelated checks at once, making cache behaviour and debugging harder.
- **Environment-sensitive**: running shell scripts and toolchain discovery
  inside a Bazel action is fragile across platforms, remote execution, and
  containerized builds.

Projects in the
[Bazel Central Registry](https://github.com/bazelbuild/bazel-central-registry)
often reimplement the *build* logic in Bazel, but they still miss Autoconf’s
convenient `config.h` generation. `rules_cc_autoconf` focuses on that gap: it
provides autoconf-like checks in a hermetic, deterministic way, driven entirely
by Bazel’s C/C++ toolchain configuration.
