# rules_cc_autoconf

Autoconf-style configuration checks for Bazel. This module lets you generate
`config.h` files by running compilation checks against Bazel’s configured
`cc_toolchain`, using Starlark macros instead of shell-driven `configure`
scripts.

## Setup

Add the following to your `MODULE.bazel` file:

```python
bazel_dep(name = "rules_cc_autoconf", version = "{version}")
```

## Documentation

For full documentation and examples, see:
<https://periareon.github.io/rules_cc_autoconf/>

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
often reimplement the _build_ logic in Bazel, but they still miss Autoconf’s
convenient `config.h` generation. `rules_cc_autoconf` focuses on that gap: it
provides autoconf-like checks in a hermetic, deterministic way, driven entirely
by Bazel’s C/C++ toolchain configuration.
