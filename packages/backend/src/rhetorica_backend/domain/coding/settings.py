from pydantic import Field
from rhetorica_backend.contracts.coding import CreateCodingRunRequest
from rhetorica_backend.domain.scenarios.settings import ScenarioSettings


class CodingSettings(ScenarioSettings):
    coding_context_max_prompt_tokens: int = Field(
        default=32_000, ge=8_000, validation_alias="CODING_CONTEXT_MAX_PROMPT_TOKENS"
    )
    coding_context_retained_tool_tokens: int = Field(
        default=8_000, ge=1_000, validation_alias="CODING_CONTEXT_RETAINED_TOOL_TOKENS"
    )
    coding_launch_defaults: dict[str, CreateCodingRunRequest] = Field(
        default_factory=dict, validation_alias="CODING_LAUNCH_DEFAULTS"
    )
    coding_enabled: bool = Field(default=False, validation_alias="CODING_ENABLED")
    coding_runtime_mode: str = Field(default="kubernetes", validation_alias="CODING_RUNTIME_MODE")
    coding_artifact_signing_secret: str = Field(
        default="", validation_alias="CODING_ARTIFACT_SIGNING_SECRET"
    )
    coding_query_gateway_internal_token: str = Field(
        default="", validation_alias="CODING_QUERY_GATEWAY_INTERNAL_TOKEN"
    )
    coding_query_gateway_url: str = Field(
        default="http://coding-query-gateway:8092", validation_alias="CODING_QUERY_GATEWAY_URL"
    )
