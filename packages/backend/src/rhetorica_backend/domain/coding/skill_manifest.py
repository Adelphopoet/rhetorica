"""Progressive skill discovery: descriptions and entry points, not bundle inventories."""


def bound_skill_manifest(*, repository, task):
    manifest = []
    for binding in task.skill_version_bindings:
        version = repository.get_version(
            skill_id=binding.skill_id,
            workspace_id=task.workspace_id,
            version_digest=binding.version_digest,
        )
        if version is None:
            continue
        entries = [a for a in version.content_bundle.assets if a.kind == "main"]
        manifest.append({
            **binding.model_dump(mode="json"),
            "instructions": [
                {
                    "path": a.path,
                    "name": str(a.frontmatter.get("name") or a.title or a.path)[:255],
                    "description": str(
                        a.frontmatter.get("description") or a.summary or a.title or ""
                    )[:500],
                }
                for a in entries
            ],
        })
    return manifest
