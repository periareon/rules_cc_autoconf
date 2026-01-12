"""# providers"""

CcAutoconfInfo = provider(
    doc = "A provider containing autoconf configuration and results.",
    fields = {
        "deps": "depset[CcAutoconfInfo]: Dependencies of the current info target.",
        "owner": "Label: The label of the owner of the results.",
        "results": "dict[str, File]: A map of define names to JSON files containing the defines produced by `CcAutoconfCheck` actions. Each define name must be unique.",
    },
)

CcAutoconfToolchainInfo = provider(
    doc = "A provider containing default autoconf checks that can be overridden by targets.",
    fields = {
        "defaults": "dict[str, File]: A map of define names to JSON files containing default values. These can be overridden by autoconf targets.",
        "defaults_by_label": "dict[Label, dict[str, File]]: A map from source labels to their respective define name -> File mappings. Used for filtering with defaults_include/defaults_exclude.",
    },
)
