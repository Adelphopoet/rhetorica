"""Explicitly migrate a local preset and pinned pip recipe into workspace settings."""

import argparse

from rhetorica_backend.contracts.coding import (
    UpsertEnvironmentProfileRequest,
    UpsertWorkspaceCodingDefaults,
)
from rhetorica_backend.domain.coding.bootstrap import build_coding_defaults
from rhetorica_backend.domain.coding.settings import CodingSettings
from rhetorica_backend.infra.coding.environment_identity import CodingEnvironmentIdentity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default="default")
    parser.add_argument("--login", default="admin")
    parser.add_argument("--from-launch-defaults", required=True)
    args = parser.parse_args()
    settings = CodingSettings()
    defaults = build_coding_defaults(settings)
    actor = defaults.service.users.get_by_login(workspace_id=args.workspace, login=args.login)
    if actor is None or not actor.is_admin:
        raise PermissionError("local_coding_setup_admin_required")
    actor = actor.for_workspace(args.workspace)
    configured = settings.coding_launch_defaults[args.from_launch_defaults]
    current = defaults.repository.get(args.workspace)
    request = UpsertWorkspaceCodingDefaults(
        expected_revision=current.revision,
        resources=configured.model_dump(exclude={"task", "conversation_id"}),
    )
    if current.resources != request.resources:
        current = defaults.replace(actor, request)
    # Deployment identity is resolved from the actual runtime, never from the UI.
    import os

    image = os.environ["CODING_RUNNER_IMAGE_DIGEST"]
    digest = os.environ["CODING_RUNNER_SOFTWARE_MANIFEST_DIGEST"]
    tools = CodingEnvironmentIdentity(
        manifest_digest=digest, runtime_url=settings.execution_runtime_url
    ).tool_versions()
    profile = defaults.service.environments.get(profile_id=configured.environment_profile_id)
    if profile is None:
        raise ValueError("coding_environment_required")
    fields = profile.model_dump(
        include={
            "id",
            "max_runtime_seconds",
            "cpu_limit",
            "memory_limit",
            "disk_limit_bytes",
            "pid_limit",
            "output_limit_bytes",
        }
    )
    requirements = [f"{name}=={tools[name]}" for name in ["dbt-core", "dbt-trino", "pytest"]]
    runtime_class = os.environ["CODING_RUNNER_RUNTIME_CLASS_NAME"]
    if (
        profile.image_digest != image
        or profile.pip_dependencies != requirements
        or profile.runtime_class_name != runtime_class
    ):
        profile = defaults.service.environments.replace(
            request=UpsertEnvironmentProfileRequest(
                **fields,
                expected_revision=profile.revision,
                pip_dependencies=requirements,
                dependency_sources=["https://pypi.org/simple"],
            ),
            actor_user_id=actor.id,
            image_digest=image,
            software_manifest_digest=digest,
            tool_versions=tools,
            runtime_class_name=runtime_class,
        )
    print(
        f"Workspace {args.workspace}: preset revision {current.revision}; environment {profile.id} revision {profile.revision}; pip packages {len(profile.pip_dependencies)}"
    )


if __name__ == "__main__":
    main()
