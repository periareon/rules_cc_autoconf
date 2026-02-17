"""Aspect for collecting the autoconf result DAG."""

# buildifier: disable=bzl-visibility
load("//autoconf/private:providers.bzl", "CcAutoconfInfo")

ResultQueryInfo = provider(
    doc = "Transitive collection of autoconf result DAG nodes.",
    fields = {
        "node_jsons": "depset[string]: JSON-encoded node descriptors for every CcAutoconfInfo target in the graph.",
        "result_files": "depset[File]: All result files in the transitive closure (so they get built).",
    },
)

def _result_query_aspect_impl(target, ctx):
    transitive_jsons = []
    transitive_files = []
    direct_dep_labels = []

    deps = getattr(ctx.rule.attr, "deps", [])
    for dep in deps:
        if ResultQueryInfo in dep:
            transitive_jsons.append(dep[ResultQueryInfo].node_jsons)
            transitive_files.append(dep[ResultQueryInfo].result_files)
        if CcAutoconfInfo in dep:
            direct_dep_labels.append(str(dep.label))

    local_jsons = []
    local_files = []

    if CcAutoconfInfo in target:
        info = target[CcAutoconfInfo]
        node = {
            "cache": {k: v.path for k, v in info.cache_results.items()},
            "define": {k: v.path for k, v in info.define_results.items()},
            "deps": sorted(direct_dep_labels),
            "label": str(target.label),
            "subst": {k: v.path for k, v in info.subst_results.items()},
        }
        local_jsons.append(json.encode(node))
        local_files.extend(info.cache_results.values())
        local_files.extend(info.define_results.values())
        local_files.extend(info.subst_results.values())

    all_jsons = depset(local_jsons, transitive = transitive_jsons)
    all_files = depset(local_files, transitive = transitive_files)

    # Write combined DAG JSON — every node writes the full transitive view,
    # but only the root target's file is requested via --output_groups.
    output = ctx.actions.declare_file(
        "_result_query/{}.dag.json".format(target.label.name),
    )
    nodes = all_jsons.to_list()
    if nodes:
        ctx.actions.write(
            output = output,
            content = "[\n" + ",\n".join(nodes) + "\n]\n",
        )
    else:
        ctx.actions.write(output = output, content = "[]\n")

    return [
        ResultQueryInfo(
            node_jsons = all_jsons,
            result_files = all_files,
        ),
        OutputGroupInfo(
            # Include result files so the checks actually execute when the
            # user asks for values.
            result_query = depset([output], transitive = [all_files]),
        ),
    ]

result_query_aspect = aspect(
    implementation = _result_query_aspect_impl,
    attr_aspects = ["deps"],
)
