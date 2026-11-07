"""What a given Databricks environment can actually do.

Databricks is not one platform. A commercial workspace, a FedRAMP High
GovCloud workspace, and an IL5 GovCloud DoD workspace differ in which
features exist at all, and a server designed against the commercial feature
set can fail on deployment in a restricted one for reasons no code review
would catch.

Every SUPPORTED or UNSUPPORTED value below is from Databricks' own published
feature availability for that environment, checked 2026-11-07. Anything not
published for an environment is UNKNOWN rather than guessed, and a probe or
the user resolves it.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Availability(str, Enum):
    """Tri-state, because "not documented" is not the same as "absent"."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class EnvironmentProfile(str, Enum):
    """Which Databricks environment the generated server will run in."""

    COMMERCIAL = "commercial"
    GOV_FEDRAMP_HIGH = "gov_fedramp_high"
    GOV_IL5 = "gov_il5"
    AZURE_GOV = "azure_gov"
    SELF_MANAGED_RESTRICTED = "self_managed_restricted"
    UNKNOWN = "unknown"


# Capability keys. Named for what a design decision turns on, not for how
# Databricks markets the feature.
CAPABILITIES = (
    "databricks_apps",
    "apps_multi_replica",
    "unity_catalog",
    "serverless_sql",
    "uc_functions",
    "genie_spaces",
    "genie_one",
    "vector_search_standard",
    "vector_search_storage_optimized",
    "vector_search_full_text",
    "vector_search_reranker",
    "lakebase",
    "managed_mcp_endpoints",
    "unity_gateway_mcp_ui",
    "foundation_model_apis",
    "external_model_endpoints",
    "public_package_index",
    "public_model_providers",
)

S = Availability.SUPPORTED
X = Availability.UNSUPPORTED
U = Availability.UNKNOWN

_PROFILES: dict[EnvironmentProfile, dict[str, Availability]] = {
    EnvironmentProfile.COMMERCIAL: {
        "databricks_apps": S,
        "apps_multi_replica": S,
        "unity_catalog": S,
        "serverless_sql": S,
        "uc_functions": S,
        "genie_spaces": S,
        "genie_one": S,
        "vector_search_standard": S,
        "vector_search_storage_optimized": S,
        "vector_search_full_text": S,
        "vector_search_reranker": S,
        "lakebase": S,
        "managed_mcp_endpoints": S,
        "unity_gateway_mcp_ui": S,
        "foundation_model_apis": S,
        "external_model_endpoints": S,
        "public_package_index": U,
        "public_model_providers": U,
    },
    # Source: Databricks DoD IL5 feature availability for us-gov-west-1.
    EnvironmentProfile.GOV_IL5: {
        "databricks_apps": S,
        "apps_multi_replica": S,
        "unity_catalog": S,
        "serverless_sql": U,
        "uc_functions": U,
        "genie_spaces": S,
        "genie_one": S,
        "vector_search_standard": S,
        "vector_search_storage_optimized": X,
        "vector_search_full_text": X,
        "vector_search_reranker": S,
        "lakebase": X,
        "managed_mcp_endpoints": U,
        "unity_gateway_mcp_ui": U,
        "foundation_model_apis": S,
        "external_model_endpoints": U,
        "public_package_index": U,
        "public_model_providers": X,
    },
    # GovCloud release notes confirm Apps, AI Search and Genie One. The
    # storage-optimized and full-text gaps are published for IL5
    # specifically, so they stay UNKNOWN here rather than assumed.
    EnvironmentProfile.GOV_FEDRAMP_HIGH: {
        "databricks_apps": S,
        "apps_multi_replica": S,
        "unity_catalog": S,
        "serverless_sql": U,
        "uc_functions": U,
        "genie_spaces": S,
        "genie_one": S,
        "vector_search_standard": S,
        "vector_search_storage_optimized": U,
        "vector_search_full_text": U,
        "vector_search_reranker": U,
        "lakebase": U,
        "managed_mcp_endpoints": U,
        "unity_gateway_mcp_ui": U,
        "foundation_model_apis": S,
        "external_model_endpoints": U,
        "public_package_index": U,
        "public_model_providers": X,
    },
    EnvironmentProfile.AZURE_GOV: {
        "databricks_apps": U,
        "apps_multi_replica": U,
        "unity_catalog": S,
        "serverless_sql": U,
        "uc_functions": U,
        "genie_spaces": U,
        "genie_one": U,
        "vector_search_standard": U,
        "vector_search_storage_optimized": U,
        "vector_search_full_text": U,
        "vector_search_reranker": U,
        "lakebase": U,
        "managed_mcp_endpoints": U,
        "unity_gateway_mcp_ui": U,
        "foundation_model_apis": U,
        "external_model_endpoints": U,
        "public_package_index": U,
        "public_model_providers": X,
    },
    EnvironmentProfile.SELF_MANAGED_RESTRICTED: {
        key: (X if key in {"public_package_index", "public_model_providers"} else U)
        for key in CAPABILITIES
    },
    EnvironmentProfile.UNKNOWN: {key: U for key in CAPABILITIES},
}

PROFILE_NOTES: dict[EnvironmentProfile, list[str]] = {
    EnvironmentProfile.GOV_IL5: [
        "Vector Search Storage Optimized and Vector Search Full-Text Search "
        "are not available. A keyword path has to be SQL LIKE against a "
        "table, not a BM25 index.",
        "Lakebase Search is not available, so do not design state onto it.",
        "Databricks Apps supports horizontal scaling here, so a server "
        "holding state in process can be served by a different replica on "
        "the next request.",
        "Public model providers are not reachable. Any model call must go "
        "through Databricks Model Serving inside the boundary.",
        "Whether the managed MCP endpoints and the Unity Gateway MCPs UI "
        "exist here is not published. Deploy the app, grant CAN USE, and "
        "connect by URL; treat the UI as a convenience, not a dependency.",
    ],
    EnvironmentProfile.GOV_FEDRAMP_HIGH: [
        "Apps, AI Search and Genie One are confirmed available. The "
        "storage-optimized and full-text gaps are published for IL5 "
        "specifically; confirm them here before relying on either.",
        "Public model providers are not reachable from the boundary.",
    ],
    EnvironmentProfile.AZURE_GOV: [
        "Azure Government is being brought up to the commercial feature set, "
        "so treat almost everything as unknown and probe before designing.",
    ],
    EnvironmentProfile.SELF_MANAGED_RESTRICTED: [
        "Assume no public package index: the app build needs an internal "
        "mirror, and the dependency list has to be installable from it.",
        "Assume no public model providers.",
    ],
    EnvironmentProfile.UNKNOWN: [
        "Nothing is assumed. Run the probe, or ask which environment this "
        "is, before committing to a source kind.",
    ],
    EnvironmentProfile.COMMERCIAL: [
        "Network egress still depends on the workspace, so confirm the app "
        "build can reach a package index before assuming it can.",
    ],
}

# Which capability each data source kind depends on.
SOURCE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "uc_table": ("unity_catalog",),
    "uc_function": ("unity_catalog", "uc_functions"),
    "uc_volume_files": ("unity_catalog",),
    "genie_space": ("genie_spaces",),
    "ai_search_index": ("vector_search_standard",),
    "rest_api": (),
    "local_snapshot": (),
    "document_corpus": (),
}


class CapabilityFinding(BaseModel):
    """One environment-fit problem with a design."""

    subject: str
    capability: str
    availability: Availability
    severity: str = Field(description="blocker, high, medium or info")
    summary: str
    fix: str = ""


def capabilities(profile: EnvironmentProfile) -> dict[str, str]:
    """The capability matrix for one environment, as plain strings."""
    table = _PROFILES.get(profile, _PROFILES[EnvironmentProfile.UNKNOWN])
    return {key: table.get(key, U).value for key in CAPABILITIES}


def availability(profile: EnvironmentProfile, capability: str) -> Availability:
    """Look up one capability, defaulting to UNKNOWN."""
    table = _PROFILES.get(profile, _PROFILES[EnvironmentProfile.UNKNOWN])
    return table.get(capability, Availability.UNKNOWN)


def notes(profile: EnvironmentProfile) -> list[str]:
    """What a designer needs to know about this environment."""
    return list(PROFILE_NOTES.get(profile, []))


def source_kind_fit(profile: EnvironmentProfile) -> dict[str, list[str]]:
    """Group source kinds by whether this environment can serve them."""
    usable: list[str] = []
    verify: list[str] = []
    blocked: list[str] = []
    for kind, required in SOURCE_REQUIREMENTS.items():
        states = [availability(profile, cap) for cap in required]
        if any(state is Availability.UNSUPPORTED for state in states):
            blocked.append(kind)
        elif any(state is Availability.UNKNOWN for state in states):
            verify.append(kind)
        else:
            usable.append(kind)
    return {"usable": usable, "verify_first": verify, "blocked": blocked}


def retrieval_guidance(profile: EnvironmentProfile) -> dict[str, object]:
    """How to do keyword and semantic retrieval in this environment."""
    storage_optimized = availability(profile, "vector_search_storage_optimized")
    full_text = availability(profile, "vector_search_full_text")
    guidance: dict[str, object] = {
        "semantic_search": availability(profile, "vector_search_standard").value,
        "storage_optimized_endpoint": storage_optimized.value,
        "bm25_full_text_index": full_text.value,
    }
    if full_text is Availability.UNSUPPORTED:
        guidance["keyword_path"] = (
            "No BM25 full-text index here. For identifier-like queries "
            "(a clause number, a contract id, a statute cite) add a second "
            "tool that runs a SQL LIKE or an exact match against the chunk "
            "table, not against the index."
        )
        guidance["endpoint_type"] = "STANDARD"
    elif full_text is Availability.UNKNOWN:
        guidance["keyword_path"] = (
            "Confirm whether a full-text index exists here before designing "
            "a keyword tool on it. The SQL fallback always works."
        )
        guidance["endpoint_type"] = "STANDARD"
    else:
        guidance["keyword_path"] = (
            "A storage-optimized endpoint can carry a BM25 keyword index "
            "with no embeddings; use it for identifier-like queries."
        )
        guidance["endpoint_type"] = "STANDARD or STORAGE_OPTIMIZED"
    return guidance


def state_guidance(profile: EnvironmentProfile) -> str:
    """Whether in-process state is safe here."""
    if availability(profile, "apps_multi_replica") is Availability.SUPPORTED:
        return (
            "Apps can run more than one replica here, so state held in "
            "process is not reliable: the next request may reach a different "
            "replica. Keep state in the payload, or in a store every replica "
            "can read."
        )
    return (
        "Multi-replica behavior is not confirmed here. Design as if a second "
        "replica exists; it costs nothing and removes a class of bug."
    )


def model_access_guidance(profile: EnvironmentProfile) -> dict[str, str]:
    """How anything that needs a model should reach one in this environment."""
    public = availability(profile, "public_model_providers")
    base = {
        "pattern": (
            "Use an OpenAI-compatible client pointed at "
            "https://<workspace-host>/serving-endpoints, with the model "
            "parameter set to a serving endpoint name. Databricks Model "
            "Serving presents Foundation Model APIs, external models and "
            "customer-hosted models through that one interface, so the same "
            "code works whatever the environment allows."
        ),
        "function_calling": (
            "Databricks function calling is OpenAI-compatible on Foundation "
            "Model APIs and external-model endpoints."
        ),
    }
    if public is Availability.UNSUPPORTED:
        base["egress"] = (
            "Public model providers are not reachable. Do not default any "
            "tool, agent or evaluation to api.openai.com or an equivalent: "
            "it will fail closed, and in an accredited boundary attempting "
            "it is itself a finding."
        )
    else:
        base["egress"] = (
            "Confirm which providers the workspace is actually permitted to "
            "call before defaulting to one."
        )
    return base


def check_design(
    profile: EnvironmentProfile, source_kinds: list[str], uses_in_process_state: bool
) -> list[CapabilityFinding]:
    """Score a design's source choices against an environment."""
    findings: list[CapabilityFinding] = []
    for kind in sorted(set(source_kinds)):
        for capability in SOURCE_REQUIREMENTS.get(kind, ()):
            state = availability(profile, capability)
            if state is Availability.UNSUPPORTED:
                findings.append(
                    CapabilityFinding(
                        subject=kind,
                        capability=capability,
                        availability=state,
                        severity="blocker",
                        summary=(
                            f"A '{kind}' source needs {capability}, which "
                            f"{profile.value} does not have."
                        ),
                        fix=(
                            "Choose another source kind, or deploy this "
                            "server in an environment that has it."
                        ),
                    )
                )
            elif state is Availability.UNKNOWN:
                findings.append(
                    CapabilityFinding(
                        subject=kind,
                        capability=capability,
                        availability=state,
                        severity="medium",
                        summary=(
                            f"A '{kind}' source needs {capability}, and "
                            f"availability in {profile.value} is not "
                            "published."
                        ),
                        fix=(
                            "Confirm it in the workspace before building, or "
                            "add a source kind that needs nothing special as "
                            "a fallback."
                        ),
                    )
                )
    if (
        uses_in_process_state
        and availability(profile, "apps_multi_replica") is not Availability.UNSUPPORTED
    ):
        findings.append(
            CapabilityFinding(
                subject="state",
                capability="apps_multi_replica",
                availability=availability(profile, "apps_multi_replica"),
                severity="high",
                summary=(
                    "The design keeps state in process while the platform "
                    "may run more than one replica."
                ),
                fix=(
                    "Return an explicit handle and keep the state in the "
                    "payload, or put it in a store every replica can read."
                ),
            )
        )
    return findings
