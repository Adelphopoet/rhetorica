# Capability Registries in Rhetorica

Rhetorica has two distinct capability registry modules that serve different purposes across the system.

## 1. LLM Capability Registry (`infra/llm/capability_registry.py`)

**Purpose**: Phase-aware model routing and LLM capability management.

**Key Classes**:
- `LlmCapabilityRegistry` - Central registry of LLM model capability profiles
- `LlmCapabilityProfile` - Immutable capability profile for a model
- `LlmPhase` - Phases in the chat orchestration pipeline
- `LlmCapability` - Capabilities an LLM model may support

**Responsibilities**:
- Models and channel capabilities
- Phase routing (PLANNER, WORKER, SYNTHESIS, REPAIR, CLASSIFIER)
- Cost estimation and model selection
- Connector capability detection from configuration

**Usage**: Injected into orchestrators and services that need to select appropriate LLM models for different phases.

---

## 2. Tool Capability Registry (`domain/chat/orchestration/capability_registry.py`)

**Purpose**: Tool-to-provider mapping and workspace capability surface.

**Key Components**:
- `TOOL_CAPABILITY_REGISTRY` - Mapping of tool names to `ToolCapabilitySpec`
- `WorkspaceCapabilitySurface` - Derived tool surface and degradation state for a workspace turn
- `RuntimeCapabilityState` - Mutable runtime capability state accumulated during a turn

**Responsibilities**:
- Tool-to-provider mapping (e.g., `fetch_metadata_tables` -> `openmetadata`)
- Degradation state tracking (which capabilities are missing or degraded)
- Tool provenance and usage tracking
- Policy decisions for available tools

**Usage**: Used during orchestration to determine which tools are available and how capabilities degrade.

---

## Summary

| Aspect | infra/llm | domain/chat/orchestration |
|--------|-----------|--------------------------|
| **Focus** | LLM models | Tools/providers |
| **Selection** | Model for phase | Tool for task |
| **Key Output** | `LlmCapabilityProfile` | `WorkspaceCapabilitySurface` |
| **Represents** | "Which model to use?" | "Which tools are available?" |
