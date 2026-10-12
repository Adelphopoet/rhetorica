# Local Coding setup

Use `CODING_ENABLED=true` and `CODING_RUNTIME_MODE=local_docker` for development on trusted repositories.

Configure existing LLM, GitLab, and database connectors in the admin UI, then run:

```sh
docker compose exec -T api uv run --no-sync python /workspace/tools/coding/setup_profiles.py --llm <llm-connector> --gitlab <gitlab-connector> --database <database-connector>
```

For existing local launch defaults, configure the workspace environment with `configure_workspace.py --workspace <workspace-id> --from-launch-defaults <coordinator-id>`. Credentials belong in local ignored settings.
